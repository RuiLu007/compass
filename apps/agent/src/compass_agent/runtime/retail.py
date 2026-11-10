"""Fixed Retail tool validation, read-only execution and bounded observations."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg import pq
from psycopg.pq import TransactionStatus
from pydantic import SecretStr, TypeAdapter, ValidationError

from compass_agent.runtime.control import (
    PhaseCanceled,
    ResourceUnconfirmed,
    RunControl,
    StopRequested,
)
from compass_agent.runtime.retail_sql import (
    CHANNEL_SALES_SQL,
    PRODUCT_SALES_SQL,
    RETAIL_IDENTITY_SQL,
)
from compass_agent.schemas import (
    ChannelRunParams,
    ProductRunParams,
    RetailRunParams,
    RunPeriod,
    RunToolError,
    ToolCall,
    ToolDefinition,
    ToolFunction,
    TrustedContext,
)
from compass_agent.settings import Settings

logger = logging.getLogger(__name__)

RETAIL_TOOL_NAME = "retail_sales"
MIN_DAY = date(2023, 1, 1)
MAX_DAY = date(2026, 1, 1)
PARAMS = TypeAdapter(RetailRunParams)


@dataclass(frozen=True, slots=True)
class RetailBinding:
    tenant_id: UUID
    workspace_id: UUID
    release: str
    content_hash: str
    role: str
    retail_tenant_id: int
    schema_name: str
    dsn: SecretStr = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedRetailCall:
    tool_call_id: str
    binding: RetailBinding | None
    params: ChannelRunParams | ProductRunParams | None
    statement: str | None = field(default=None, repr=False)
    values: dict[str, Any] = field(default_factory=dict, repr=False)
    safe_error: RunToolError | None = None

    @property
    def tool_kind(self) -> Literal["retail_sales", "unavailable"]:
        # A known tool with safely validated params stays identifiable even when
        # its deployment binding is unavailable. Unknown/invalid calls expose no params.
        return "retail_sales" if self.params is not None else "unavailable"


@dataclass(frozen=True, slots=True)
class RetailRow:
    code: str
    name: str
    paid_cent: int
    compare_paid_cent: int | None
    delta_cent: int | None


@dataclass(frozen=True, slots=True)
class RetailQueryResult:
    status: Literal["ok", "empty", "error"]
    dimension: Literal["channel", "product"]
    params: ChannelRunParams | ProductRunParams
    rows: tuple[RetailRow, ...] = ()
    error_code: RunToolError | None = None


class RetailIdentityMismatch(Exception):
    pass


def retail_tool_definition() -> ToolDefinition:
    period = {
        "type": "object",
        "additionalProperties": False,
        "required": ["start", "end"],
        "properties": {
            "start": {"type": "string", "format": "date"},
            "end": {"type": "string", "format": "date"},
        },
    }
    common = {
        "period": period,
        "compareTo": {"anyOf": [period, {"type": "null"}]},
    }
    return ToolDefinition(
        function=ToolFunction(
            name=RETAIL_TOOL_NAME,
            description=(
                "查询授权 Retail 样本的渠道或商品实付金额。日期区间左闭右开；"
                "商品维度必须使用上一次渠道结果中的 channelCode。"
            ),
            parameters={
                "type": "object",
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["dimension", "period", "compareTo"],
                        "properties": {
                            "dimension": {"const": "channel"},
                            **common,
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["dimension", "period", "compareTo", "channelCode"],
                        "properties": {
                            "dimension": {"const": "product"},
                            **common,
                            "channelCode": {
                                "type": "string",
                                "pattern": "^[A-Z0-9_]{1,24}$",
                            },
                        },
                    },
                ],
            },
        )
    )


def supports_bounded_cancel() -> bool:
    try:
        return pq.version() >= 170000
    except Exception:
        return False


@lru_cache(maxsize=8)
def _log_configuration_error(code: str) -> None:
    # Code-only and once-per-process: never attach the scope JSON, DSN or driver error.
    logger.error("Retail Run binding unavailable; configuration_code=%s", code)


def binding_for(settings: Settings, context: TrustedContext) -> RetailBinding | None:
    scope, dsn, error = settings.retail_run_configuration()
    if error:
        _log_configuration_error(error)
        return None
    if scope is None or dsn is None:
        return None
    if not supports_bounded_cancel():
        _log_configuration_error("libpq_cancel_unavailable")
        return None
    if (
        context.purpose != "chat"
        or context.scope.kind != "tenant"
        or context.scope.tenant_id != scope.tenant_id
        or context.scope.workspace_id != scope.workspace_id
    ):
        return None
    return RetailBinding(
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        release=scope.release,
        content_hash=scope.content_hash,
        role=scope.role,
        retail_tenant_id=scope.retail_tenant_id,
        schema_name=scope.schema_name,
        dsn=dsn,
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_arguments(value: str) -> ChannelRunParams | ProductRunParams:
    if len(value.encode()) > 8192:
        raise ValueError("tool arguments too large")
    parsed = json.loads(
        value,
        object_pairs_hook=_strict_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
    )
    return PARAMS.validate_python(parsed)


def _validate_period(start: str, end: str) -> tuple[date, date]:
    left, right = date.fromisoformat(start), date.fromisoformat(end)
    days = (right - left).days
    if left < MIN_DAY or right > MAX_DAY or not 1 <= days <= 31:
        raise ValueError("date period outside allowed range")
    return left, right


def prepare_retail_call(call: ToolCall, binding: RetailBinding | None) -> PreparedRetailCall:
    if call.function.name != RETAIL_TOOL_NAME:
        return PreparedRetailCall(
            tool_call_id=call.id,
            binding=None,
            params=None,
            safe_error="unknown_tool",
        )
    try:
        params = _parse_arguments(call.function.arguments)
        start, end = _validate_period(params.period.start, params.period.end)
        compare_start = compare_end = None
        if params.compare_to is not None:
            compare_start, compare_end = _validate_period(
                params.compare_to.start,
                params.compare_to.end,
            )
    except (ValueError, TypeError, ValidationError):
        return PreparedRetailCall(
            tool_call_id=call.id,
            binding=None,
            params=None,
            safe_error="invalid_parameters",
        )
    if binding is None:
        return PreparedRetailCall(
            tool_call_id=call.id,
            binding=None,
            params=params,
            safe_error="retail_unavailable",
        )
    values = {
        "tenant": binding.retail_tenant_id,
        "start": start,
        "end": end,
        "compare_start": compare_start,
        "compare_end": compare_end,
    }
    if isinstance(params, ProductRunParams):
        values["channel"] = params.channel_code
    return PreparedRetailCall(
        tool_call_id=call.id,
        binding=binding,
        params=params,
        statement=PRODUCT_SALES_SQL if isinstance(params, ProductRunParams) else CHANNEL_SALES_SQL,
        values=values,
    )


def failed_retail_result(prepared: PreparedRetailCall) -> RetailQueryResult:
    if prepared.safe_error is None:
        raise ValueError("prepared call has no safe error")
    # Invalid arguments are not safe to echo, but the observation still needs a valid dimension.
    dimension = prepared.params.dimension if prepared.params is not None else "channel"
    params = prepared.params or ChannelRunParams(
        dimension="channel",
        period=RunPeriod(start="2023-01-01", end="2023-01-02"),
        compareTo=None,
    )
    return RetailQueryResult(
        status="error",
        dimension=dimension,
        params=params,
        error_code=prepared.safe_error,
    )


async def open_retail_connection(binding: RetailBinding, run_id: UUID):
    if not supports_bounded_cancel():
        raise RetailIdentityMismatch("bounded cancel unavailable")
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


def _consume(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()


async def _close_late(task: asyncio.Future) -> None:
    try:
        connection = await task
        await connection.close()
    except BaseException:
        pass


async def _settle_retail(
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


async def query_retail(
    prepared: PreparedRetailCall,
    control: RunControl,
    *,
    connect: Callable[[RetailBinding, UUID], Awaitable[Any]] = open_retail_connection,
) -> RetailQueryResult:
    if (
        prepared.safe_error is not None
        or prepared.binding is None
        or prepared.params is None
        or prepared.statement is None
    ):
        return failed_retail_result(prepared)

    connection = cursor = None
    command: asyncio.Task | None = None
    connecting = asyncio.ensure_future(connect(prepared.binding, control.id))
    interrupted = False
    outcome: RetailQueryResult | None = None
    try:
        connection = await asyncio.shield(connecting)
        control.require_running()
        cursor = connection.cursor()
        command = asyncio.create_task(_execute(cursor, "BEGIN READ ONLY"))
        await asyncio.shield(command)
        control.require_running()
        command = asyncio.create_task(_fetch(cursor, RETAIL_IDENTITY_SQL))
        identity_rows = await asyncio.shield(command)
        if len(identity_rows) != 1:
            raise RetailIdentityMismatch("identity row missing")
        session_user, current_user, read_only, rls_active = identity_rows[0]
        if (
            session_user != prepared.binding.role
            or current_user != prepared.binding.role
            or read_only != "on"
            or rls_active is not True
        ):
            raise RetailIdentityMismatch("read-only identity mismatch")
        control.require_running()
        command = asyncio.create_task(_fetch(cursor, prepared.statement, prepared.values))
        values = await asyncio.shield(command)
        if len(values) > 200:
            outcome = RetailQueryResult(
                status="error",
                dimension=prepared.params.dimension,
                params=prepared.params,
                error_code="result_limit",
            )
        else:
            rows = tuple(
                RetailRow(
                    code=str(row[0]),
                    name=str(row[1]),
                    paid_cent=int(row[2]),
                    compare_paid_cent=None if row[3] is None else int(row[3]),
                    delta_cent=None if row[4] is None else int(row[4]),
                )
                for row in values
            )
            outcome = RetailQueryResult(
                status="ok" if rows else "empty",
                dimension=prepared.params.dimension,
                params=prepared.params,
                rows=rows,
            )
    except (asyncio.CancelledError, StopRequested):
        interrupted = True
    except RetailIdentityMismatch:
        outcome = RetailQueryResult(
            status="error",
            dimension=prepared.params.dimension,
            params=prepared.params,
            error_code="retail_unavailable",
        )
    except psycopg.errors.QueryCanceled:
        outcome = RetailQueryResult(
            status="error",
            dimension=prepared.params.dimension,
            params=prepared.params,
            error_code="retail_query_timeout",
        )
    except TimeoutError:
        outcome = RetailQueryResult(
            status="error",
            dimension=prepared.params.dimension,
            params=prepared.params,
            error_code="retail_query_timeout",
        )
    except psycopg.Error:
        outcome = RetailQueryResult(
            status="error",
            dimension=prepared.params.dimension,
            params=prepared.params,
            error_code="retail_query_failed",
        )
    finally:
        receipt = await _settle_retail(
            connecting,
            connection,
            cursor,
            command,
            interrupted=interrupted,
        )
    if interrupted:
        raise PhaseCanceled(cleanup_confirmed=receipt)
    if not receipt:
        raise ResourceUnconfirmed("retail cleanup unconfirmed")
    if outcome is None:
        raise RuntimeError("retail query completed without outcome")
    return outcome


def retail_observation(result: RetailQueryResult) -> str:
    if result.status == "error":
        return json.dumps(
            {"status": "error", "errorCode": result.error_code},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    rows = [
        {
            "code": row.code,
            "name": row.name,
            "paidCent": str(row.paid_cent),
            "comparePaidCent": None
            if row.compare_paid_cent is None
            else str(row.compare_paid_cent),
            "deltaCent": None if row.delta_cent is None else str(row.delta_cent),
        }
        for row in result.rows[:12]
    ]
    payload = {
        "status": result.status,
        "dimension": result.dimension,
        "rowCount": len(result.rows),
        "totals": {
            "paidCent": str(sum(row.paid_cent for row in result.rows)),
            "comparePaidCent": None
            if all(row.compare_paid_cent is None for row in result.rows)
            else str(sum(row.compare_paid_cent or 0 for row in result.rows)),
            "deltaCent": None
            if all(row.delta_cent is None for row in result.rows)
            else str(sum(row.delta_cent or 0 for row in result.rows)),
        },
        "rows": rows,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    while len(encoded.encode()) > 4096 and payload["rows"]:
        payload["rows"].pop()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > 4096:
        raise ValueError("retail observation exceeds limit")
    return encoded
