"""Shared isolated data setup and generic Agent fixtures."""

from pathlib import Path
from typing import Any, LiteralString, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from compass_agent.data_connections.transport import AuthorizedDatabaseBinding
from compass_agent.runtime.context import compass_definition
from compass_agent.runtime.service import RunService
from compass_agent.schemas import GenerateInput
from compass_agent.tools.builtin.database_query.handler import registration
from psycopg import sql
from pydantic import SecretStr

from .retail_fixtures import DEFAULT_PERIODS
from .test_agent_run import FakeRouting, model_result, selected_route
from .test_run_autonomy import ScriptedModels

PERIODS = DEFAULT_PERIODS.model_dump()
SOURCE = """
const lead = await agent("channel_compare", args);
if (lead.status !== "ok") return { status: "needs_data", evidenceRefs: [] };
const next = {...args, channelCode: lead.channelCode, leadEvidenceRef: lead.evidenceRef};
const [product, review] = await parallel([
  () => agent("product_breakdown", next),
  () => agent("scope_review", next),
]);
if (product.status !== "ok" || review.status !== "ok") {
  return {status: "needs_review", evidenceRefs: [lead.evidenceRef]};
}
return {status: "ok", evidenceRefs: [lead.evidenceRef, product.evidenceRef, review.evidenceRef]};
"""


class WorkflowModels(ScriptedModels):
    def __init__(self, exercise_script_retry=False):
        super().__init__([])
        self.generated = 0
        self.child_requests = []
        self.parent_requests: list[GenerateInput] = []
        self.asset_calls = []

    async def run(self, prepared, value, **kwargs):
        self.parent_requests.append(value)
        if not self.results:
            self.results.append(model_result(text="查询与分析完成。"))
        return await super().run(prepared, value, **kwargs)


def make_executor(
    assets, retail_dsn, scope, *, exercise_script_retry=False, legacy_approval=False, **overrides
):
    models = WorkflowModels(exercise_script_retry)
    bindings = ()
    if retail_dsn:
        bindings = (
            AuthorizedDatabaseBinding(
                scope.tenant_id,
                scope.workspace_id,
                urlsplit(retail_dsn).username or "test_reader",
                SecretStr(retail_dsn),
            ),
        )

    async def provider(trusted, control, emit):
        return (registration(bindings, emit=emit),) if bindings else ()

    options = {
        key: value
        for key, value in overrides.items()
        if key in {"verifiers", "task_verifiers", "autonomy_limits", "tool_loader"}
    }
    return RunService(
        cast(Any, models),
        cast(Any, FakeRouting(selected_route())),
        definition=compass_definition(),
        tools_provider=provider,
        autonomy_profiles=(),
        **options,
    ), models


def setup_retail(admin_url: str, dsn: str) -> None:
    target = urlsplit(dsn)
    database = target.path.lstrip("/")
    assert database.startswith("compass_retail_test_") and target.username and target.password
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(target.username),
                sql.Literal(target.password),
            )
        )
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    admin_target = urlunsplit(urlsplit(admin_url)._replace(path=target.path))
    with psycopg.connect(admin_target) as connection:
        connection.execute(
            sql.SQL(
                cast(LiteralString, (Path(__file__).parent / "workflow_retail.sql").read_text())
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA core TO {}").format(sql.Identifier(target.username))
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA core TO {}").format(
                sql.Identifier(target.username),
            )
        )


def new_retail(admin_url: str) -> str:
    admin = urlsplit(admin_url)
    suffix = uuid4().hex[:10]
    return urlunsplit(
        admin._replace(
            netloc=f"workflow_{suffix}:test-only@{admin.hostname}:{admin.port}",
            path=f"/compass_retail_test_{suffix}",
        )
    )


def setup_legacy_readonly_retail(admin_url: str, dsn: str) -> None:
    """Extend the small course dataset for legacy read-only scope and pagination tests."""
    setup_retail(admin_url, dsn)
    target = urlsplit(dsn)
    assert target.username and target.username.endswith("_a_east")
    with psycopg.connect(urlunsplit(urlsplit(admin_url)._replace(path=target.path))) as db:
        db.execute("UPDATE core.spu SET spu_name = '户外露营101-3' WHERE spu_code = 'SPU3'")
        db.execute(
            "INSERT INTO core.sku SELECT 101, series, 2 FROM generate_series(4, 54) AS series"
        )
        db.execute("INSERT INTO core.spu VALUES (102, 4, 'SPU4', '外租户样品')")
        for number in range(47):
            relation = sql.Identifier("core", f"fixture_{number:02d}")
            db.execute(sql.SQL("CREATE TABLE {} (sample int)").format(relation))
            db.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(
                    relation, sql.Identifier(target.username)
                )
            )
