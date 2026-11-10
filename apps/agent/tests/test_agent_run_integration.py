"""Real Retail PostgreSQL acceptance for the Lesson 15 execution kernel."""

import asyncio
import json
import os
from dataclasses import dataclass, field
from uuid import UUID

import psycopg
import pytest
from compass_agent.runtime.control import OwnerKey, RunControl, RunRegistry, StopRequested
from compass_agent.runtime.retail import (
    PreparedRetailCall,
    RetailBinding,
    RetailQueryResult,
    prepare_retail_call,
    query_retail,
    supports_bounded_cancel,
)
from compass_agent.schemas import (
    AnalysisFileNone,
    RunCompleted,
    ToolCall,
    ToolCallFunction,
)
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

RELEASE = "retail-dev-20261022b"
CONTENT_HASH = "7ae26405a71589c98ca8379bec1e1ec1ff77e16232ff2e1bf26b6d4501b8183e"
TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
WORKSPACE_ID = UUID("20000000-0000-0000-0000-000000000002")
ACTOR_ID = UUID("30000000-0000-0000-0000-000000000003")
OWNER = OwnerKey(actor_user_id=ACTOR_ID, tenant_id=TENANT_ID, workspace_id=WORKSPACE_ID)
PERIOD = {"start": "2025-11-17", "end": "2025-11-24"}
COMPARE_TO = {"start": "2025-11-24", "end": "2025-12-01"}


@dataclass(frozen=True, slots=True)
class RetailTarget:
    database: str
    binding: RetailBinding
    admin_dsn: SecretStr = field(repr=False)


def _required_target() -> RetailTarget:
    readonly_url = os.environ.get("AGENT_RETAIL_TEST_DATABASE_URL", "")
    admin_url = os.environ.get("AGENT_RETAIL_TEST_ADMIN_URL", "")
    if not readonly_url or not admin_url:
        pytest.fail(
            "Explicit AGENT_RETAIL_TEST_DATABASE_URL and "
            "AGENT_RETAIL_TEST_ADMIN_URL credentials required"
        )
    try:
        readonly = conninfo_to_dict(readonly_url)
        admin = conninfo_to_dict(admin_url)
    except psycopg.Error:
        pytest.fail("AGENT_RETAIL_TEST_* values must be valid PostgreSQL connection strings")

    required = ("dbname", "host", "user", "password")
    if any(not readonly.get(name) for name in required) or any(
        not admin.get(name) for name in required
    ):
        pytest.fail("AGENT_RETAIL_TEST_* credentials must explicitly name host, database and login")

    database = str(readonly["dbname"])
    admin_database = str(admin["dbname"])
    if not database.startswith("compass_retail_test_") or not admin_database.startswith(
        "compass_retail_test_"
    ):
        pytest.fail("Dedicated compass_retail_test_* databases required")

    readonly_endpoint = (readonly["host"], readonly.get("port", "5432"), database)
    admin_endpoint = (admin["host"], admin.get("port", "5432"), admin_database)
    if readonly_endpoint != admin_endpoint:
        pytest.fail("AGENT_RETAIL_TEST_* credentials must target the same dedicated database")

    role = str(readonly["user"])
    if not role.endswith("_a_east"):
        pytest.fail("AGENT_RETAIL_TEST_DATABASE_URL must use the published a_east profile")
    if role == str(admin["user"]):
        pytest.fail("Retail runtime and management credentials must use separate roles")
    if not supports_bounded_cancel():
        pytest.fail("Retail integration acceptance requires libpq 17+ cancel_safe support")

    return RetailTarget(
        database=database,
        binding=RetailBinding(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            release=RELEASE,
            content_hash=CONTENT_HASH,
            role=role,
            retail_tenant_id=101,
            schema_name="core",
            dsn=SecretStr(readonly_url),
        ),
        admin_dsn=SecretStr(admin_url),
    )


@pytest.fixture(scope="module")
def retail_target() -> RetailTarget:
    return _required_target()


def _channel_call(call_id: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        function=ToolCallFunction(
            name="retail_sales",
            arguments=json.dumps(
                {"dimension": "channel", "period": PERIOD, "compareTo": COMPARE_TO}
            ),
        ),
    )


def _product_call(call_id: str, channel_code: str, **extra: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        function=ToolCallFunction(
            name="retail_sales",
            arguments=json.dumps(
                {
                    "dimension": "product",
                    "period": PERIOD,
                    "compareTo": COMPARE_TO,
                    "channelCode": channel_code,
                    **extra,
                }
            ),
        ),
    )


def _prepare(target: RetailTarget, call: ToolCall) -> PreparedRetailCall:
    prepared = prepare_retail_call(call, target.binding)
    assert prepared.safe_error is None
    return prepared


async def _perform(control: RunControl, target: RetailTarget, call: ToolCall) -> RetailQueryResult:
    prepared = _prepare(target, call)
    return await control.perform(lambda: query_retail(prepared, control))


def _complete(control: RunControl) -> None:
    terminal = control.commit(
        RunCompleted(
            status="completed",
            finalAnswer="Retail PostgreSQL acceptance completed",
            file=AnalysisFileNone(status="none"),
        )
    )
    assert terminal.status == "completed"


async def _assert_published_sample(target: RetailTarget) -> None:
    connection = await psycopg.AsyncConnection.connect(
        target.admin_dsn.get_secret_value(), autocommit=True, connect_timeout=5
    )
    try:
        cursor = await connection.execute(
            """
            SELECT r.release_id, g.input_sha256, r.status
            FROM ops.dataset_release AS r
            JOIN ops.generation_run AS g USING (release_id)
            WHERE g.stage = 'IMPORT' AND g.status = 'SUCCEEDED'
            """
        )
        rows = await cursor.fetchall()
        assert rows == [(RELEASE, CONTENT_HASH, "PUBLISHED")]
        current = await (await connection.execute("SELECT current_database()")).fetchone()
        assert current == (target.database,)
    finally:
        await connection.close()


def test_published_retail_channel_then_product_path(retail_target: RetailTarget) -> None:
    async def scenario() -> None:
        await _assert_published_sample(retail_target)

        registry = RunRegistry()
        control = registry.register(OWNER)
        rejected = prepare_retail_call(
            _product_call("call_sql_rejected", "POS", sql="SELECT pg_sleep(30)"),
            retail_target.binding,
        )
        assert rejected.safe_error == "invalid_parameters"
        assert rejected.statement is None and rejected.binding is None
        rejection = await control.perform(lambda: query_retail(rejected, control))
        assert rejection.status == "error" and rejection.error_code == "invalid_parameters"

        channels = await _perform(control, retail_target, _channel_call("call_channels"))
        assert channels.status == "ok"
        assert channels.rows[0].code == "POS"
        assert (
            channels.rows[0].paid_cent,
            channels.rows[0].compare_paid_cent,
            channels.rows[0].delta_cent,
        ) == (1_410_889, 543_596, -867_293)

        selected_channel = channels.rows[0].code
        products = await _perform(
            control,
            retail_target,
            _product_call("call_products", selected_channel),
        )
        assert products.status == "ok"
        assert [(row.code, row.delta_cent) for row in products.rows] == [
            ("SPU3", -511_199),
            ("SPU1", -259_344),
            ("SPU2", -96_750),
        ]
        assert sum(row.paid_cent for row in products.rows) == channels.rows[0].paid_cent
        assert sum(row.compare_paid_cent or 0 for row in products.rows) == (
            channels.rows[0].compare_paid_cent
        )
        _complete(control)

    asyncio.run(scenario())


async def _wait_for_blocked_backend(
    observer: psycopg.AsyncConnection, application_name: str
) -> int:
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        cursor = await observer.execute(
            """
            SELECT pid, state, wait_event_type, query
            FROM pg_stat_activity
            WHERE datname = current_database() AND application_name = %s
            """,
            (application_name,),
        )
        rows = await cursor.fetchall()
        if len(rows) == 1 and rows[0][1:3] == ("active", "Lock") and "WITH latest AS" in rows[0][3]:
            return int(rows[0][0])
        await asyncio.sleep(0.05)
    pytest.fail("Retail product connection did not reach the controlled database lock")


async def _wait_for_backend_exit(observer: psycopg.AsyncConnection, pid: int) -> None:
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        cursor = await observer.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE pid = %s",
            (pid,),
        )
        if await cursor.fetchone() == (0,):
            return
        await asyncio.sleep(0.05)
    pytest.fail("Canceled Retail backend remained visible in pg_stat_activity")


def test_blocked_retail_query_stops_cleans_up_and_allows_next_query(
    retail_target: RetailTarget,
) -> None:
    async def scenario() -> None:
        admin_url = retail_target.admin_dsn.get_secret_value()
        blocker = await psycopg.AsyncConnection.connect(
            admin_url, autocommit=False, connect_timeout=5
        )
        observer = await psycopg.AsyncConnection.connect(
            admin_url, autocommit=True, connect_timeout=5
        )
        registry = RunRegistry()
        control = registry.register(OWNER)
        task: asyncio.Task[RetailQueryResult] | None = None
        try:
            await blocker.execute("SET LOCAL lock_timeout = '2s'")
            # The identity query only references channel_product_daily. Locking the joined
            # channel table therefore proves the fixed business query itself is in flight.
            await blocker.execute("LOCK TABLE core.channel IN ACCESS EXCLUSIVE MODE")

            task = asyncio.create_task(
                _perform(control, retail_target, _channel_call("call_blocked"))
            )
            application_name = f"compass-run-{control.id}"
            pid = await _wait_for_blocked_backend(observer, application_name)

            accepted = control.request_stop("user")
            assert accepted.status == "accepted"
            with pytest.raises(StopRequested):
                async with asyncio.timeout(10):
                    await task
            terminal = control.commit(
                RunCompleted(
                    status="completed",
                    finalAnswer="late result must not win",
                    file=AnalysisFileNone(status="none"),
                )
            )
            assert terminal.status == "canceled"
            assert terminal.reason == "user" and terminal.cleanup == "confirmed"
            await _wait_for_backend_exit(observer, pid)
        finally:
            if task is not None and not task.done():
                control.request_stop("user")
            await blocker.rollback()
            if task is not None and not task.done():
                try:
                    async with asyncio.timeout(10):
                        await task
                except BaseException:
                    pass
            await blocker.close()
            await observer.close()

        next_registry = RunRegistry()
        next_control = next_registry.register(OWNER)
        result = await _perform(
            next_control,
            retail_target,
            _channel_call("call_after_cancel"),
        )
        assert result.status == "ok" and result.rows[0].code == "POS"
        assert result.rows[0].delta_cent == -867_293
        _complete(next_control)

    asyncio.run(scenario())
