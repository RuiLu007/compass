"""Public node contracts and actual runner group instrumentation."""

import asyncio
from uuid import uuid4

import pytest
from compass_agent.runtime.execution_trace import ExecutionTrace, preview
from compass_agent.schemas import (
    ExecutionDisplay,
    ExecutionEnvironment,
    ExecutionOrigin,
    RunExecutionNode,
    RunExtensionPayload,
    StepStarted,
    StepStartedPayload,
    StreamExtension,
    ToolCall,
    ToolCallFunction,
)
from compass_agent.workflows.script import execute_script
from pydantic import ValidationError


def test_source_environment_and_safe_preview_are_independent():
    node = RunExecutionNode(
        id=str(uuid4()),
        revision=1,
        kind="tool",
        status="running",
        title="查询商品",
        startedAtMs=1000,
        origin=ExecutionOrigin(kind="mcp", serverId="retail", toolName="query_products"),
        executor=ExecutionEnvironment(kind="sandbox", runtime="Python"),
        display=ExecutionDisplay(policy="preview", inputPreview='{"limit":3}'),
    )
    assert node.origin and node.origin.kind == "mcp"
    assert node.executor and node.executor.kind == "sandbox"
    for change in (
        {"parentId": node.id},
        {"dependsOn": [node.id]},
        {"origin": {"kind": "mcp", "toolName": "query_products"}},
        {"display": {"policy": "summary", "inputPreview": "private"}},
        {"scheduling": "parallel"},
        {"status": "completed"},
        {"endedAtMs": 999},
        {"privatePrompt": "private"},
    ):
        with pytest.raises(ValidationError):
            RunExecutionNode.model_validate({**node.model_dump(by_alias=True), **change})
    assert len((preview("中文" * 1000) or "").encode()) <= 950


def test_repeated_calls_have_unique_nodes_and_arguments_stay_private():
    events = []
    trace = ExecutionTrace(events.append)
    step = str(uuid4())
    trace.emit(
        StepStarted(
            type="step.started",
            payload=StepStartedPayload(stepId=step, stepIndex=1, modelCallId=str(uuid4())),
        )
    )
    calls = [
        ToolCall(
            id=f"call-{index}",
            function=ToolCallFunction(
                name="reuse_workflow", arguments='{"secret":"private-marker"}'
            ),
        )
        for index in range(2)
    ]
    trace.bind_calls(step, calls)
    for call in calls:
        identifier = trace.begin_call(step, call.id)
        assert identifier
        trace.finish(identifier)
    nodes = list(trace.nodes.values())
    workflows = [node for node in nodes if node.kind == "tool"]
    assert len(workflows) == 2 and workflows[0].id != workflows[1].id
    assert all(node.parent_id == trace.steps[step] for node in workflows)
    assert "private-marker" not in str(events)
    revisions: dict[str, int] = {}
    for event in events:
        if event.type != "stream.extension":
            continue
        node = RunExecutionNode.model_validate(event.payload.data)
        assert node.revision == revisions.get(node.id, 0) + 1
        revisions[node.id] = node.revision


def test_runner_reports_real_parallel_group_and_preserves_serial_calls():
    async def scenario():
        progress = []
        dispatched = []
        release = asyncio.Event()

        async def dispatch(profile, value):
            return {"profile": profile}

        async def grouped(profile, value, group):
            dispatched.append((profile, group))
            if group is not None:
                if len([entry for entry in dispatched if entry[1] == group]) == 2:
                    release.set()
                await asyncio.wait_for(release.wait(), 1)
            return await dispatch(profile, value)

        result = await execute_script(
            'await agent("lead", args); return await parallel('
            '[() => agent("one", args), () => agent("two", args)]);',
            {},
            dispatch,
            remaining=5,
            grouped_dispatch=grouped,
            group_event=lambda identifier, status: progress.append((identifier, status)),
        )
        assert result == [{"profile": "one"}, {"profile": "two"}]
        assert dispatched == [("lead", None), ("one", 1), ("two", 1)]
        assert progress == [(1, "running"), (1, "completed")]

    asyncio.run(scenario())


def test_tool_argument_preparation_emits_no_public_event_or_placeholder():
    events = []
    trace = ExecutionTrace(events.append)
    step = str(uuid4())
    trace.emit(
        StepStarted(
            type="step.started",
            payload=StepStartedPayload(stepId=step, stepIndex=1, modelCallId=str(uuid4())),
        )
    )
    events.clear()
    for status in ("preparing", "prepared"):
        trace.emit(
            StreamExtension(
                type="stream.extension",
                payload=RunExtensionPayload(
                    name="compass.tool.preparation",
                    version=1,
                    required=False,
                    data={"stepId": step, "toolIndex": 1, "status": status},
                ),
            )
        )
    assert events == []
    trace.bind_calls(
        step, [ToolCall(id="plan", function=ToolCallFunction(name="todo_write", arguments="{}"))]
    )
    assert len(events) == 1
    node = RunExecutionNode.model_validate(events[0].payload.data)
    assert node.title == "更新任务计划" and node.tool_call_id == "plan"
    assert trace.begin_call(step, "plan") == node.id
    trace.finish(node.id)
    assert trace.nodes[node.id].status == "completed"
