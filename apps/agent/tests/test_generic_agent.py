"""A non-Retail Agent uses the same loop, registry, budget and result path."""

from typing import Any, cast

import pytest
from compass_agent.harness.context import AgentDefinition, build_messages
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.runtime.service import RunService
from compass_agent.tools.contracts import ExecutionReceipt
from psycopg.errors import UndefinedColumn
from sqlalchemy.exc import ProgrammingError

from .test_agent_run import CHAT, FakeModels, FakeRouting, model_result, run_command, selected_route
from .test_tool_dispatcher import call, registration


@pytest.mark.anyio
async def test_run_reports_workflow_catalog_database_failure():
    class MissingMigration:
        def active_tools(self, context, *, limit):
            raise ProgrammingError("private SQL", {}, UndefinedColumn("private error details"))

    executor = RunService(
        cast(Any, FakeModels([])),
        cast(Any, FakeRouting(selected_route())),
        definition=AgentDefinition(builtin_tools=frozenset({"workflow"})),
        workflow_assets=MissingMigration(),
    )
    control = RunRegistry().register(OwnerKey.from_context(CHAT))
    events = []
    result = await executor.run(run_command(), CHAT, control, events.append)
    assert result.status == "failed" and result.error_code == "internal_error"
    assert [
        event.payload.data
        for event in events
        if event.type == "stream.extension" and event.payload.name == "compass.runtime.failure"
    ] == [
        {
            "stage": "tool_setup",
            "exceptionType": "UndefinedColumn",
            "reasonCode": "database_schema_mismatch",
        }
    ]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("controlled", [False, True])
async def test_custom_tools_and_prompt_run_without_loading_retail(controlled):
    invoked = []

    async def add(args, context):
        invoked.append(context.run_id)
        return ExecutionReceipt(context.call_id, "ok", "confirmed", {"sum": 5})

    definition = AgentDefinition(system_prompt="你是计算助手。", tools=(registration("add", add),))
    models = FakeModels(
        [
            model_result(tool_calls=(call("add", "sum"),), token="next", finish="tool_calls"),
            model_result(text="结果是 5。"),
        ]
    )

    def retail_not_used(scope):
        pytest.fail("An Agent without Retail tools must not load a Retail connection")

    executor = RunService(
        cast(Any, models),
        cast(Any, FakeRouting(selected_route())),
        definition=definition,
        autonomy_profiles=() if controlled else None,
    )
    control = RunRegistry().register(OwnerKey.from_context(CHAT))
    result = await executor.run(run_command(), CHAT, control, lambda event: None)
    assert result.status == "completed" and result.final_answer == "结果是 5。"
    assert invoked == [control.id]
    first, second = (item[1] for item in models.requests)
    assert {tool.function.name for tool in first.tools} == {"add"}
    assert "retail_sales" not in (first.messages[0].content or "")
    assert "channel_summary" not in (first.messages[0].content or "")
    assert second.messages[-1].role == "tool"
    assert second.messages[-1].tool_call_id == "sum"


def test_default_definition_and_context_have_no_builtin_business_tools():
    definition = AgentDefinition()
    assert not definition.tools and not definition.builtin_tools
    messages = build_messages(definition, run_command())
    assert "经营分析" not in (messages[0].content or "")
    assert "retail_sales" not in (messages[0].content or "")


@pytest.mark.anyio
async def test_default_runtime_can_answer_without_business_configuration():
    models = FakeModels([model_result(text="你好。")])
    executor = RunService(cast(Any, models), cast(Any, FakeRouting(selected_route())))
    control = RunRegistry().register(OwnerKey.from_context(CHAT))
    result = await executor.run(run_command(), CHAT, control, lambda event: None)
    assert result.status == "completed" and result.final_answer == "你好。"
    assert models.requests[0][1].tools == []
