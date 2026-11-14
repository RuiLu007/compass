"""Generic real SQL and Todo cases preserve snapshot and stream projections."""

import asyncio
import json
import os
from typing import Any, cast
from urllib.parse import urlsplit

from compass_agent.data_connections.transport import AuthorizedDatabaseBinding
from compass_agent.harness.autonomy import AutonomyLimits
from compass_agent.runtime.context import compass_definition
from compass_agent.runtime.public_projection import reduce_packet
from compass_agent.runtime.service import RunService
from pydantic import SecretStr

from .query_fixtures import provider_for
from .test_agent_run import CHAT, FakeRouting, model_result, selected_route
from .test_database_tools_integration import targets as targets
from .test_durable_runtime_integration import (
    command,
    execute,
    prepare,
    service,
)
from .test_run_autonomy import (
    ScriptedModels,
    channel,
    complete,
    definition,
    output,
    product,
    tool,
)


def observation(request, call_id):
    return next(
        json.loads(message.content)
        for message in request.messages
        if message.role == "tool" and message.tool_call_id == call_id
    )


def generic_case(name, *, trusted=CHAT):
    assert trusted.scope.kind == "tenant"
    dsn = os.environ["AGENT_TEST_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    binding = AuthorizedDatabaseBinding(
        trusted.scope.tenant_id,
        trusted.scope.workspace_id,
        urlsplit(dsn).username or "test_reader",
        SecretStr(dsn),
    )
    limits = AutonomyLimits(tools=1) if name == "tool-budget" else None
    if name == "ad-hoc-sql":
        actions = [
            output(tool("database_query", "query", op="query", sql="SELECT 3 AS amount")),
            model_result(text="查询完成。"),
        ]
    elif name == "planned-diagnosis":
        actions = [
            output(definition()),
            output(channel()),
            output(complete()),
            model_result(text="计划已完成。"),
        ]
    elif name == "missing-period":
        actions = [
            output(tool("database_query", "empty", op="query", sql="SELECT 1 WHERE false")),
            model_result(text="所选范围缺少数据。"),
        ]
    elif name == "tool-budget":
        actions = [output(channel()), output(product())]
    else:
        actions = [output(channel()), output(product()), model_result(text="查询与汇总完成。")]
    models = ScriptedModels(actions)
    worker = RunService(
        cast(Any, models),
        cast(Any, FakeRouting(selected_route())),
        tools_provider=provider_for(lambda _: binding),
        autonomy_profiles=(),
        autonomy_limits=limits,
        definition=compass_definition(),
    )
    return worker, models, command(question="请查询并汇总数据。")


def public_state(snapshot):
    view, messages = {"schemaVersion": 1, "run": None, "objects": {}}, {}
    for packet in snapshot["packets"]:
        reduce_packet(view, messages, packet)
    return view, messages


def test_generic_cases_have_identical_snapshot_and_sse_projection(targets):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            for name in (
                "planned-diagnosis",
                "model-synthesis",
                "ad-hoc-sql",
                "tool-budget",
                "missing-period",
            ):
                worker, models, value = generic_case(name)
                run = await execute(runtime, value, worker)
                assert run.status == ("failed" if name == "tool-budget" else "completed"), (
                    name,
                    run.reason_code,
                )
                await runtime.publisher.flush(run.id)
                snapshot = runtime.reader.messages(run.session_id, CHAT).model_dump(
                    mode="json", by_alias=True
                )
                rows = await runtime.streams.rows(run.id)
                live = {"packets": [{**row["packet"], "seq": seq} for seq, row in rows]}
                assert public_state(live) == public_state(snapshot)
                count = len(models.requests)
                runtime.reader.messages(run.session_id, CHAT)
                assert len(models.requests) == count

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()
