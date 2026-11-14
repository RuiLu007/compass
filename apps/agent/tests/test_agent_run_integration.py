"""Real Retail PostgreSQL acceptance for the Lesson 15 execution kernel."""

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from compass_agent.data_connections.legacy import LegacyScopeCheck
from compass_agent.data_connections.transport import (
    AuthorizedDatabaseBinding,
    supports_bounded_cancel,
)
from compass_agent.runtime.context import compass_definition
from compass_agent.runtime.control import OwnerKey, RunControl, RunRegistry
from compass_agent.runtime.service import RunService
from compass_agent.schemas import (
    AnalysisFileNone,
    RunCompleted,
)
from compass_agent.tools.builtin.database_query import DatabaseResult, query_database
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from .query_fixtures import provider_for
from .test_agent_run import CHAT, FakeRouting, model_result, run_command, selected_route
from .test_run_autonomy import (
    ScriptedModels,
    output,
    tool,
)

RELEASE = "retail-dev-20261022b"
CONTENT_HASH = "7ae26405a71589c98ca8379bec1e1ec1ff77e16232ff2e1bf26b6d4501b8183e"
TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
WORKSPACE_ID = UUID("20000000-0000-0000-0000-000000000002")
ACTOR_ID = UUID("30000000-0000-0000-0000-000000000003")
OWNER = OwnerKey(actor_user_id=ACTOR_ID, tenant_id=TENANT_ID, workspace_id=WORKSPACE_ID)
PERIOD = {"start": "2025-11-17", "end": "2025-11-24"}
COMPARE_TO = {"start": "2025-11-24", "end": "2025-12-01"}

AD_HOC_SQL = """
    WITH products AS (
        SELECT tenant_id, spu_id, spu_code, spu_name
        FROM core.spu WHERE spu_code IN ('SPU1', 'SPU3')
    )
    SELECT p.spu_name AS product, count(k.sku_id) AS variants
    FROM products p JOIN core.sku k USING (tenant_id, spu_id)
    GROUP BY p.spu_code, p.spu_name ORDER BY p.spu_code DESC LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class RetailTarget:
    database: str
    binding: AuthorizedDatabaseBinding
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
        binding=AuthorizedDatabaseBinding(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            role=role,
            scope_check=LegacyScopeCheck(101, role),
            dsn=SecretStr(readonly_url),
        ),
        admin_dsn=SecretStr(admin_url),
    )


@pytest.fixture(scope="module")
def retail_target() -> RetailTarget:
    return _required_target()


def _complete(control: RunControl) -> None:
    terminal = control.commit(
        RunCompleted(
            status="completed",
            finalAnswer="Retail PostgreSQL acceptance completed",
            file=AnalysisFileNone(status="none"),
        )
    )
    assert terminal.status == "completed"


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


def test_ad_hoc_sql_discovers_tables_and_publishes_a_real_join(retail_target: RetailTarget):
    def submit_step(input_):
        result = json.loads(input_.messages[-1].content)
        assert result["columns"] == ["product", "variants"]
        assert result["rows"][0][0] == "户外露营101-3"
        assert len(result["rows"]) == 1 and result["rows"][0][1] > 0
        return model_result(text="商品：" + result["rows"][0][0])

    async def scenario():
        models = ScriptedModels(
            [
                output(tool("database_query", "list", op="list_tables")),
                output(
                    tool(
                        "database_query",
                        "describe",
                        op="describe_table",
                        schema="core",
                        table="spu",
                    )
                ),
                output(tool("database_query", "query", op="query", sql=AD_HOC_SQL)),
                submit_step,
            ]
        )
        agent = RunService(
            cast(Any, models),
            cast(Any, FakeRouting(selected_route())),
            tools_provider=provider_for(lambda _: retail_target.binding),
            autonomy_profiles=(),
            definition=compass_definition(),
        )
        control = RunRegistry().register(OWNER)
        events = []
        command = run_command().model_copy(update={"question": "列出数据表，再执行这条 SQL"})
        result = await agent.run(command, CHAT, control, events.append)
        assert result.status == "completed", result
        assert result.final_answer == "商品：户外露营101-3"
        starts = [event for event in events if event.type == "tool.started"]
        assert [event.payload.tool_kind for event in starts] == ["database_query"] * 3
        assert json.loads(starts[-1].payload.input_preview or "{}") == {
            "op": "query",
            "sql": AD_HOC_SQL,
        }
        finishes = [event for event in events if event.type == "tool.finished"]
        assert [event.payload.status for event in finishes] == ["ok"] * 3
        preview = json.loads(finishes[-1].payload.result_preview or "{}")
        assert preview["rows"][0][0] == "户外露营101-3" and "resultId" not in preview

    asyncio.run(scenario())


def test_ad_hoc_sql_keeps_retail_scope_and_caps_the_preview(retail_target: RetailTarget):
    async def scenario():
        control = RunRegistry().register(OWNER)

        async def run(sql: str):
            return await control.perform(
                lambda: query_database(
                    tool("database_query", "probe", op="query", sql=sql),
                    retail_target.binding,
                    control,
                )
            )

        for unsafe in (
            "SELECT 1; SELECT 2",
            "SELECT * INTO temp_test FROM core.spu",
            "WITH changed AS (DELETE FROM core.spu RETURNING *) SELECT * FROM changed",
            "SELECT pg_catalog.set_config('role', 'postgres', false)",
            "SELECT rolname FROM pg_catalog.pg_roles",
            "SELECT rolname FROM pg_roles",
            "WITH pg_roles AS (SELECT 1 FROM pg_roles) SELECT * FROM pg_roles",
            "SELECT spu_code FROM core.spu FOR UPDATE",
        ):
            assert await run(unsafe) == "invalid_parameters", unsafe
        scope = await run("SELECT DISTINCT tenant_id FROM core.spu ORDER BY tenant_id")
        assert isinstance(scope, DatabaseResult) and scope.rows == ((101,),)

        async def tables(offset: int):
            return await control.perform(
                lambda: query_database(
                    tool("database_query", "tables", op="list_tables", offset=offset),
                    retail_target.binding,
                    control,
                )
            )

        first_page = await tables(0)
        assert isinstance(first_page, DatabaseResult) and first_page.next_offset == 50
        second_page = await tables(first_page.next_offset)
        assert isinstance(second_page, DatabaseResult) and second_page.rows
        assert set(first_page.rows).isdisjoint(second_page.rows)
        rows = await run("SELECT sku_id FROM core.sku ORDER BY sku_id")
        assert isinstance(rows, DatabaseResult) and len(rows.rows) == 50 and rows.truncated
        assert rows.rows == tuple(sorted(rows.rows))
        aliases = await run("SELECT 1 AS code, 2 AS code")
        assert isinstance(aliases, DatabaseResult)
        assert aliases.columns == ("code", "code") and aliases.rows == ((1, 2),)
        wide = await run("SELECT lpad('汉', 1800, '汉') AS value")
        assert isinstance(wide, DatabaseResult) and wide.truncated and not wide.rows
        assert len(wide.observation().encode()) <= 4096
        _complete(control)

    asyncio.run(scenario())
