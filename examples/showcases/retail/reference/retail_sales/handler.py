"""Fixed Retail tool validation, read-only execution and bounded observations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg import pq
from psycopg.pq import TransactionStatus
from pydantic import SecretStr, TypeAdapter, ValidationError

from compass_agent.schemas import (
    ChannelRunParams,
    ProductRunParams,
    RetailRunParams,
    RunPeriod,
    RunToolError,
    ToolCall,
    ToolDefinition,
    ToolFunction,
)
from compass_agent.tools.business.retail_sales.sql import (
    CHANNEL_SALES_SQL,
    PRODUCT_SALES_SQL,
    RETAIL_IDENTITY_SQL,
)
from compass_agent.tools.contracts import (
    PhaseCanceled,
    ResourceUnconfirmed,
    StopRequested,
    ToolControl,
)

RETAIL_TOOL_NAME = "retail_sales"
MIN_DAY = date(2023, 1, 1)
MAX_DAY = date(2026, 1, 1)
PARAMS = TypeAdapter(RetailRunParams)


@dataclass(frozen=True, slots=True)
class RetailBinding:
    tenant_id: UUID
    workspace_id: UUID
    role: str
    retail_tenant_id: int
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
    base_covered: bool = False
    comparison_covered: bool = False


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


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_tool_arguments(value: str, maximum: int = 8192) -> object:
    if len(value.encode()) > maximum:
        raise ValueError("tool arguments too large")
    return json.loads(
        value,
        object_pairs_hook=_strict_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
    )


def validate_period(start: str, end: str) -> tuple[date, date]:
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
        params = PARAMS.validate_python(parse_tool_arguments(call.function.arguments))
        start, end = validate_period(params.period.start, params.period.end)
        compare_start = compare_end = None
        if params.compare_to is not None:
            compare_start, compare_end = validate_period(
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


async def check_retail_identity(cursor, binding: RetailBinding) -> None:
    rows = await _fetch(cursor, RETAIL_IDENTITY_SQL, {"tenant": binding.retail_tenant_id})
    if rows != [(binding.role, binding.role, "on", True, True)]:
        raise RetailIdentityMismatch("Retail scope or read-only identity mismatch")


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


async def read_retail[ReadResult](
    binding: RetailBinding,
    control: ToolControl,
    read: Callable[[Any], Awaitable[ReadResult]],
    *,
    connect: Callable[[RetailBinding, UUID], Awaitable[Any]] = open_retail_connection,
) -> ReadResult:
    connection = cursor = None
    command: asyncio.Task | None = None
    connecting = asyncio.ensure_future(connect(binding, control.id))
    interrupted = False
    try:
        connection = await asyncio.shield(connecting)
        control.require_running()
        cursor = connection.cursor()
        command = asyncio.create_task(_execute(cursor, "BEGIN READ ONLY"))
        await asyncio.shield(command)
        control.require_running()
        command = asyncio.create_task(check_retail_identity(cursor, binding))
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
        receipt = await _settle_retail(
            connecting, connection, cursor, command, interrupted=interrupted
        )
        if interrupted:
            raise PhaseCanceled(cleanup_confirmed=receipt)
        if not receipt:
            raise ResourceUnconfirmed("retail cleanup unconfirmed")


async def query_retail(prepared, control, *, connect=open_retail_connection):
    from hashlib import sha256

    from compass_agent.execution_context import recorded

    binding = prepared.binding

    return await recorded(
        "tool",
        f"retail:{prepared.tool_call_id}",
        {
            "params": prepared.params.model_dump(mode="json") if prepared.params else None,
            "binding": {
                "tenantId": str(binding.tenant_id),
                "workspaceId": str(binding.workspace_id),
                "role": binding.role,
                "retailTenantId": binding.retail_tenant_id,
                "connectionDigest": sha256(binding.dsn.get_secret_value().encode()).hexdigest(),
            }
            if binding
            else None,
        },
        lambda: _query_retail_live(prepared, control, connect=connect),
        RetailQueryResult,
    )


async def _query_retail_live(
    prepared: PreparedRetailCall,
    control: ToolControl,
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

    try:
        values = await read_retail(
            prepared.binding,
            control,
            lambda cursor: _fetch(cursor, prepared.statement, prepared.values),
            connect=connect,
        )
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
                base_covered=bool(values and len(values[0]) >= 7 and values[0][5]),
                comparison_covered=bool(values and len(values[0]) >= 7 and values[0][6]),
            )
    except RetailIdentityMismatch:
        outcome = RetailQueryResult(
            status="error",
            dimension=prepared.params.dimension,
            params=prepared.params,
            error_code="retail_unavailable",
        )
    except (psycopg.errors.QueryCanceled, TimeoutError):
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
    return outcome


def retail_observation(
    result: RetailQueryResult,
    *,
    query_id: str | None = None,
    todo_completion: dict[str, object] | None = None,
) -> str:
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
        "period": result.params.period.model_dump(),
        "compareTo": result.params.compare_to.model_dump() if result.params.compare_to else None,
        "baseCovered": result.base_covered,
        "comparisonCovered": result.comparison_covered,
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
    if query_id is not None:
        payload["queryId"] = query_id
    if todo_completion is not None:
        payload["completeTodo"] = todo_completion
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    while len(encoded.encode()) > 4096 and payload["rows"]:
        payload["rows"].pop()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > 4096:
        raise ValueError("retail observation exceeds limit")
    return encoded


def retail_source(result: RetailQueryResult) -> dict:
    """Complete authorized rows for analysis; the model observation remains a short preview."""
    return {
        "dimension": result.dimension,
        "params": result.params.model_dump(mode="json", by_alias=True),
        "rows": [
            {
                "code": row.code,
                "name": row.name,
                "paidCent": str(row.paid_cent),
                "comparePaidCent": str(row.compare_paid_cent)
                if row.compare_paid_cent is not None
                else None,
                "deltaCent": str(row.delta_cent) if row.delta_cent is not None else None,
            }
            for row in result.rows
        ],
    }
