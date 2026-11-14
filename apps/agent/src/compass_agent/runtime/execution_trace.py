"""Run-local public execution tree; independent from messages and tool observations."""

import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from time import time

from compass_agent.execution_context import uuid4
from compass_agent.llm.contracts import AgentCallResponse
from compass_agent.runtime.execution_detail import detail_json, detail_text
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    CallResponse,
    ExecutionDetail,
    ExecutionDisplay,
    ExecutionEnvironment,
    ExecutionOrigin,
    RunExecutionNode,
    RunExtensionPayload,
    RunProgressEvent,
    StepFinished,
    StepStarted,
    StreamExtension,
    ToolCall,
    ToolFinished,
    ToolStarted,
)
from compass_agent.tools.contracts import preview

ACTIVE = {"preparing", "running"}
TITLES = {
    "database_query": "查询数据库",
    "todo_write": "更新任务计划",
    "python_analysis": "执行 Python 分析",
}


class ExecutionTrace:
    def __init__(self, emit: Callable[[RunProgressEvent], None]):
        self._emit = emit
        self.nodes: dict[str, RunExecutionNode] = {}
        self._scope: ContextVar[str | None] = ContextVar("execution_parent", default=None)
        self.steps: dict[str, str] = {}
        self.calls: dict[tuple[str, str], str] = {}
        self.active_step: str | None = None

    @property
    def parent(self) -> str | None:
        return self._scope.get() or self.steps.get(self.active_step or "")

    @contextmanager
    def scope(self, node_id: str) -> Iterator[None]:
        token = self._scope.set(node_id)
        try:
            yield
        finally:
            self._scope.reset(token)

    def start(self, kind: str, title: str, **fields) -> str:
        if len(self.nodes) >= RUN_STREAM_LIMITS["executionNodes"]:
            raise ValueError("execution node budget exceeded")
        identifier = str(uuid4())
        self._publish(
            RunExecutionNode.model_validate(
                {
                    "id": identifier,
                    "parentId": self.parent,
                    "revision": 1,
                    "kind": kind,
                    "status": "running",
                    "title": title,
                    "startedAtMs": int(time() * 1000),
                    **fields,
                }
            )
        )
        return identifier

    def update(self, identifier: str, **fields) -> None:
        previous = self.nodes[identifier]
        values = previous.model_dump(by_alias=True)
        values.update(fields)
        values["revision"] = previous.revision + 1
        if values["status"] not in ACTIVE and values["endedAtMs"] is None:
            values["endedAtMs"] = max(previous.started_at_ms, int(time() * 1000))
        self._publish(RunExecutionNode.model_validate(values))

    def finish(self, identifier: str, status: str = "completed", **fields) -> None:
        if self.nodes[identifier].status in ACTIVE:
            self.update(identifier, status=status, **fields)

    def settle(self, status: str) -> None:
        # A missing completion remains visible even when the parent Run succeeds.
        terminal = "canceled" if status == "canceled" else "incomplete"
        for identifier in reversed(self.nodes):
            self.finish(
                identifier,
                terminal,
                summary="执行已停止" if terminal == "canceled" else "执行结果未确认",
            )

    def _publish(self, node: RunExecutionNode) -> None:
        self.nodes[node.id] = node
        self._emit(
            StreamExtension(
                type="stream.extension",
                payload=RunExtensionPayload(
                    name="compass.execution.node",
                    version=1,
                    required=False,
                    data=node.model_dump(mode="json", by_alias=True, exclude_none=True),
                ),
            )
        )

    def bind_calls(self, step_id: str, calls: Sequence[ToolCall], registered=()) -> None:
        for call in calls:
            name = call.function.name
            visible_name = (
                name
                if (re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", name) and not name.startswith("mcp_"))
                else None
            )
            fields: dict = {
                "toolCallId": call.id,
                "title": TITLES.get(name) or visible_name or "调用工具",
                "origin": ExecutionOrigin(kind="builtin", toolName=visible_name),
                "executor": ExecutionEnvironment(kind="service"),
            }
            identifier = self.start(
                fields.pop("kind", "tool"),
                fields.pop("title"),
                parentId=self.steps[step_id],
                status="preparing",
                stepId=step_id,
                **fields,
            )
            self.calls[step_id, call.id] = identifier

    def model_content(
        self, step_id: str, response: CallResponse, *, node_id: str | None = None
    ) -> None:
        identifier = node_id or self.steps.get(step_id)
        thinking = response.analysis_thinking if isinstance(response, AgentCallResponse) else ""
        narration = response.text if response.tool_calls else ""
        if identifier and (thinking.strip() or narration.strip()):
            self.update(
                identifier,
                detail=ExecutionDetail(
                    thinking=detail_text(thinking) if thinking.strip() else None,
                    narration=detail_text(narration) if narration.strip() else None,
                ),
            )

    def tool_detail(
        self, identifier: str, *, input_json: str | None = None, output_json: str | None = None
    ) -> ExecutionDetail | None:
        current = self.nodes[identifier].detail or ExecutionDetail()
        input_text = detail_json(input_json) or current.input
        output_text = detail_json(output_json) or current.output
        return (
            ExecutionDetail(input=input_text, output=output_text)
            if input_text or output_text
            else None
        )

    def begin_call(self, step_id: str, call_id: str, **fields) -> str | None:
        identifier = self.calls.get((step_id, call_id))
        if identifier and self.nodes[identifier].status == "preparing":
            self.update(identifier, status="running", **fields)
        return identifier

    def current_call(self, kind: str) -> str | None:
        return next(
            (
                identifier
                for (step, _), identifier in self.calls.items()
                if step == self.active_step
                and self.nodes[identifier].kind == kind
                and self.nodes[identifier].status == "running"
            ),
            None,
        )

    def emit(self, event: RunProgressEvent) -> None:
        """Keep v1 execution/file checks; project their approved data into the new tree."""
        # Partial model tool arguments are internal progress, not public execution nodes.
        if isinstance(event, StreamExtension) and event.payload.name == "compass.tool.preparation":
            return
        self._emit(event)
        if isinstance(event, StepStarted):
            info = event.payload
            self.active_step = info.step_id
            self.steps[info.step_id] = self.start(
                "model",
                "分析请求",
                parentId=None,
                stepId=info.step_id,
                modelCallId=info.model_call_id,
                executor=ExecutionEnvironment(kind="service"),
            )
        elif isinstance(event, StepFinished):
            self.finish(
                self.steps[event.payload.step_id],
                event.payload.status,
            )
            self.active_step = None
        elif isinstance(event, ToolStarted):
            info = event.payload
            identifier = self.calls.get((info.step_id, info.tool_call_id))
            if identifier is None:
                identifier = self.start(
                    "tool",
                    "执行查询",
                    parentId=self.steps[info.step_id],
                    stepId=info.step_id,
                    toolCallId=info.tool_call_id,
                )
                self.calls[info.step_id, info.tool_call_id] = identifier
            title = TITLES.get(info.tool_kind, "检查工具请求")
            if info.params:
                title = (
                    "比较渠道实付金额"
                    if info.params.dimension == "channel"
                    else f"查询 {info.params.channel_code} 渠道商品明细"
                )
            self.update(
                identifier,
                status="running",
                title=title,
                origin=ExecutionOrigin(kind="builtin", toolName=info.tool_kind),
                executor=ExecutionEnvironment(kind="service"),
                display=ExecutionDisplay(
                    policy="preview", inputPreview=preview(info.input_preview), language="json"
                ),
                detail=self.tool_detail(identifier, input_json=info.input_preview),
            )
        elif isinstance(event, ToolFinished):
            info = event.payload
            identifier = self.calls[info.step_id, info.tool_call_id]
            display = self.nodes[identifier].display.model_copy(
                update={"output_preview": preview(info.result_preview)}
            )
            self.finish(
                identifier,
                {"ok": "completed", "empty": "empty", "error": "failed"}[info.status],
                display=display,
                detail=self.tool_detail(identifier, output_json=info.result_preview),
            )
