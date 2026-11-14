"""State and injected ports consumed by the inference harness."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from compass_agent.execution_context import uuid4
from compass_agent.schemas import CallResponse, ChatMessage, ExecutionDetail, RunError, ToolCall

StepStatus = Literal["running", "completed", "failed", "canceled"]


class ModelRoute(Protocol):
    @property
    def provider_id(self) -> UUID: ...
    @property
    def binding_version(self) -> int: ...


class TraceSink(Protocol):
    calls: dict[tuple[str, str], str]

    def bind_calls(
        self, step_id: str, calls: tuple[ToolCall, ...], registered: Iterable[str] = ()
    ) -> None: ...
    def begin_call(self, step_id: str, call_id: str, **fields: Any) -> str | None: ...
    def model_content(
        self, step_id: str, response: CallResponse, *, node_id: str | None = None
    ) -> None: ...
    def tool_detail(
        self, identifier: str, *, input_json: str | None = None, output_json: str | None = None
    ) -> ExecutionDetail | None: ...
    def update(self, identifier: str, **fields: Any) -> None: ...
    def finish(self, identifier: str, status: str = "completed", **fields: Any) -> None: ...


class RunFailure(Exception):
    def __init__(self, code: RunError, *, reason: str | None = None):
        self.code: RunError = code
        self.reason = reason or code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    status: Literal["ok", "empty", "error"]
    observation_json: str = field(repr=False)


@dataclass(slots=True)
class Step:
    id: UUID
    index: int
    model_call_id: UUID
    status: StepStatus = "running"
    tool_calls: tuple[ToolCall, ...] = field(default=(), repr=False)
    tool_results: list[ToolResult] = field(default_factory=list, repr=False)


@dataclass(slots=True)
class AgentState:
    messages: list[ChatMessage] = field(repr=False)
    steps: list[Step] = field(default_factory=list)
    continuation_token: str | None = field(default=None, repr=False)
    trace: TraceSink | None = field(default=None, repr=False)

    def start_step(self, index: int) -> Step:
        step = Step(id=uuid4(), index=index, model_call_id=uuid4())
        self.steps.append(step)
        return step

    def record_tool_result(self, step: Step, result: ToolResult) -> None:
        position = len(step.tool_results)
        if position >= len(step.tool_calls) or step.tool_calls[position].id != result.tool_call_id:
            raise RunFailure("internal_error")
        step.tool_results.append(result)
        self.messages.append(
            ChatMessage(
                role="tool",
                tool_call_id=result.tool_call_id,
                content=result.observation_json,
            )
        )


class RepeatStep:
    """Task validation requests one bounded model retry before dispatch."""
