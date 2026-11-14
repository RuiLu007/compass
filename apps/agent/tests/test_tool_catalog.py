"""Shipped-tool discovery, registration and generic planning acceptance."""

import ast
import json
from pathlib import Path
from typing import Any, cast

import pytest
from compass_agent.harness.context import AgentDefinition
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.runtime.service import RunService
from compass_agent.tools.builtin.todo_write.plan import (
    TODO_ACTION,
    ExecutionPlan,
    TodoDefinition,
    TodoRejected,
)
from compass_agent.tools.catalog import TOOLS, definitions, registrations
from compass_agent.tools.catalog_contracts import CatalogContext
from compass_agent.tools.contracts import ToolRejected
from compass_agent.tools.registry import ToolRegistry

from .test_agent_run import CHAT, FakeModels, FakeRouting, model_result, run_command, selected_route
from .test_tool_dispatcher import call

ROOT = Path(__file__).parents[1] / "src/compass_agent/tools"


def test_every_shipped_tool_has_one_named_directory_and_one_registration():
    names = [spec.name for spec in TOOLS]
    assert len(names) == len(set(names))
    directories = {
        (category, path.name)
        for category in ("builtin", "business")
        for path in (ROOT / category).iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert directories == {(spec.category, spec.name) for spec in TOOLS}
    for spec in TOOLS:
        module = ROOT / spec.category / spec.name / "__init__.py"
        assert module.is_file()
    for path in (ROOT / "builtin").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("compass_agent.tools.business"), path


@pytest.mark.parametrize("database", [False, True])
@pytest.mark.parametrize("sandbox", [False, True])
@pytest.mark.parametrize("controlled", [False, True])
def test_preflight_and_registration_publish_identical_definitions(database, sandbox, controlled):
    context = CatalogContext(
        frozenset({"todo", "python", "database", "workflow"}),
        database=database,
        sandbox=sandbox,
        workflow=controlled,
    )
    registry = ToolRegistry(registrations(context, lambda name: lambda args, ctx: None))
    assert registry.definitions() == definitions(context)
    by_name = registry.entries
    assert ("python_analysis" in by_name) == sandbox
    assert "todo_write" in by_name
    assert ("create_workflow" in by_name) == controlled
    assert ("sub_agent" in by_name) == controlled
    assert ("database_query" in by_name) == database
    assert not {"apply_budget", "submit_analysis", "submit_database_result"}.intersection(by_name)


def test_generic_todo_checks_dependencies_and_stale_versions_without_retail_fields():
    plan = ExecutionPlan[TodoDefinition]()
    plan.apply(
        TODO_ACTION.validate_python(
            {
                "op": "replace",
                "baseVersion": 0,
                "selectTodoId": "t1",
                "items": [
                    {"id": "t1", "goal": "读取文件"},
                    {"id": "t2", "goal": "整理结果", "dependsOn": ["t1"]},
                ],
            }
        ),
        1,
    )
    with pytest.raises(TodoRejected, match="stale_plan_version"):
        plan.apply(
            TODO_ACTION.validate_python({"op": "select", "baseVersion": 0, "todoId": "t1"}), 2
        )
    with pytest.raises(TodoRejected, match="dependency_unmet"):
        plan.apply(
            TODO_ACTION.validate_python({"op": "complete", "baseVersion": 1, "todoId": "t2"}), 2
        )
    plan.apply(
        TODO_ACTION.validate_python(
            {
                "op": "complete",
                "baseVersion": 1,
                "todoId": "t1",
                "selectTodoId": "t2",
            }
        ),
        2,
    )
    assert plan.status == {"t1": "verified", "t2": "pending"}
    assert plan.selected == "t2"


@pytest.mark.anyio
async def test_generic_todo_runs_through_actual_runtime_without_retail():
    args = {"op": "replace", "baseVersion": 0, "items": [{"id": "t1", "goal": "整理文件"}]}
    models = FakeModels(
        [
            model_result(
                tool_calls=(call("todo_write", "plan", json.dumps(args)),),
                token="next",
                finish="tool_calls",
            ),
            model_result(text="计划已登记。"),
        ]
    )

    def no_retail(scope):
        pytest.fail("Generic planning must not load a Retail connection")

    service = RunService(
        cast(Any, models),
        cast(Any, FakeRouting(selected_route())),
        definition=AgentDefinition(builtin_tools=frozenset({"todo"})),
    )
    control = RunRegistry().register(OwnerKey.from_context(CHAT))
    result = await service.run(run_command(), CHAT, control, lambda event: None)
    assert result.status == "completed"
    observation = json.loads(models.requests[1][1].messages[-1].content)
    assert observation["status"] == "ok" and observation["version"] == 1
    assert observation["items"][0]["goal"] == "整理文件"


@pytest.mark.anyio
async def test_python_still_rejects_unowned_sources_without_a_retail_binding():
    context = CatalogContext(frozenset({"python"}), sandbox=True)
    (entry,) = registrations(context, lambda name: None)
    from .test_tool_dispatcher import context as execution_context

    with pytest.raises(ToolRejected, match="sandbox_source_denied"):
        entry.authorize({"code": "result = 1", "source_query_id": "unknown"}, execution_context())
