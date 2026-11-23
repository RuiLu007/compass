"""Bounded read-only PostgreSQL sessions for authorized data connections."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from pydantic import SecretStr

from compass_agent.tools.contracts import (
    PhaseCanceled,
    ResourceUnconfirmed,
    StopRequested,
    ToolControl,
)


@dataclass(frozen=True, slots=True)
class AuthorizedDatabaseBinding:
    tenant_id: UUID
    workspace_id: UUID
    role: str
    dsn: SecretStr = field(repr=False)
    connection_id: UUID | None = None
    name: str = ""
    revision: int = 1
    scope_check: Callable[[Any], Awaitable[None]] | None = field(default=None, repr=False)


class DatabaseIdentityMismatch(Exception):
    pass


def supports_bounded_cancel() -> bool:
    return hasattr(psycopg.AsyncConnection, "cancel_safe")


async def open_connection(binding: AuthorizedDatabaseBinding, run_id: UUID):
    if not supports_bounded_cancel():
        raise DatabaseIdentityMismatch("bounded cancel unavailable")
    options = (
        "-c default_transaction_read_only=on "
        "-c statement_timeout=5000 "
        "-c idle_in_transaction_session_timeout=8000"
    )
    async with asyncio.timeout(3):
        return await psycopg.AsyncConnection.connect(
            binding.dsn.get_secret_value(),
            autocommit=True,
            connect_timeout=3,
            application_name=f"compass-run-{run_id}",
            options=options,
        )


async def _execute(cursor, statement, values=None):
    await cursor.execute(statement, values)


async def _fetch(cursor, statement, values=None):
    await cursor.execute(statement, values)
    return await cursor.fetchall()


async def check_identity(cursor, binding: AuthorizedDatabaseBinding) -> None:
    await cursor.execute(
        "SELECT session_user, current_user, current_setting('transaction_read_only')"
    )
    if await cursor.fetchone() != (binding.role, binding.role, "on"):
        raise DatabaseIdentityMismatch("read-only identity mismatch")
    if binding.scope_check is not None:
        await binding.scope_check(cursor)


def _consume(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()


async def _close_late(task: asyncio.Future) -> None:
    try:
        connection = await task
        await connection.close()
    except BaseException:
        pass


async def _settle_connection(
    connecting: asyncio.Future,
    connection,
    cursor,
    command: asyncio.Task | None,
    *,
    interrupted: bool,
) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 8
    confirmed = True

    async def bounded(awaitable, maximum):
        remaining = max(0.001, min(maximum, deadline - loop.time()))
        async with asyncio.timeout(remaining):
            return await awaitable

    if connection is None:
        try:
            connection = await bounded(asyncio.shield(connecting), 3.1)
        except BaseException:
            if connecting.done():
                _consume(connecting)
                # A completed connect failure returned no handle to this caller.
                return True
            asyncio.create_task(_close_late(connecting))
            return False

    if interrupted and command is not None and not command.done():
        try:
            await bounded(connection.cancel_safe(timeout=1), 1.2)
        except BaseException:
            # A failed cancel request does not make cleanup unknowable by itself:
            # statement_timeout may still settle the command below.
            pass
    if command is not None and not command.done():
        try:
            await bounded(asyncio.shield(command), 5.5)
        except BaseException:
            # A completed task (including QueryCanceled) is a positive settlement.
            if not command.done():
                confirmed = False
    if command is not None and command.done():
        _consume(command)

    if cursor is not None:
        try:
            await bounded(cursor.close(), 0.5)
        except BaseException:
            confirmed = False
    try:
        status = connection.pgconn.transaction_status
        if status not in {TransactionStatus.IDLE, TransactionStatus.UNKNOWN}:
            await bounded(connection.rollback(), 1)
            status = connection.pgconn.transaction_status
        if status != TransactionStatus.IDLE:
            confirmed = False
    except BaseException:
        confirmed = False
    try:
        await bounded(connection.close(), 0.5)
    except BaseException:
        confirmed = False
    return confirmed


async def read_database[ReadResult](
    binding: AuthorizedDatabaseBinding,
    control: ToolControl,
    read: Callable[[Any], Awaitable[ReadResult]],
    *,
    connect: Callable[[AuthorizedDatabaseBinding, UUID], Awaitable[Any]] = open_connection,
    repeatable_read: bool = False,
) -> ReadResult:
    connection = cursor = None
    command: asyncio.Task | None = None
    connecting = asyncio.ensure_future(connect(binding, control.id))
    interrupted = False
    try:
        connection = await asyncio.shield(connecting)
        control.require_running()
        cursor = connection.cursor()
        command = asyncio.create_task(
            _execute(
                cursor,
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
                if repeatable_read
                else "BEGIN READ ONLY",
            )
        )
        await asyncio.shield(command)
        control.require_running()
        command = asyncio.create_task(check_identity(cursor, binding))
        await asyncio.shield(command)
        control.require_running()
        if (
            control.phase_deadline is not None
            and asyncio.get_running_loop().time() >= control.phase_deadline
        ):
            raise TimeoutError
        command = asyncio.ensure_future(read(cursor))
        return await asyncio.shield(command)
    except (asyncio.CancelledError, StopRequested):
        interrupted = True
        raise
    finally:
        receipt = await _settle_connection(
            connecting, connection, cursor, command, interrupted=interrupted
        )
        if interrupted:
            raise PhaseCanceled(cleanup_confirmed=receipt)
        if not receipt:
            raise ResourceUnconfirmed("database cleanup unconfirmed")
