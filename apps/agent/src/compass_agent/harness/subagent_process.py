"""Child process entry point. Model and tool operations cross a bounded JSON pipe."""

import asyncio
import json
import sys
from uuid import UUID

from compass_agent.harness.context import model_input
from compass_agent.harness.contracts import AgentState, RunFailure, ToolResult
from compass_agent.harness.executor import AgentLoop
from compass_agent.harness.prompts import DATABASE_PROMPT
from compass_agent.llm.contracts import AgentCallResponse
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    AnalysisFileNone,
    ChatMessage,
    RunCompleted,
    ToolDefinition,
)

FRAME_LIMIT = 512 * 1024
MAX_STEPS = 8


def receive():
    line = sys.stdin.buffer.readline(FRAME_LIMIT + 1)
    if not line or len(line) > FRAME_LIMIT:
        raise ValueError("invalid child frame")
    return json.loads(line)


def send(value):
    line = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if len(line) >= FRAME_LIMIT:
        raise ValueError("child frame limit")
    sys.stdout.buffer.write(line + b"\n")
    sys.stdout.buffer.flush()


def exchange(value):
    send(value)
    return receive()


class ChildExecutor(AgentLoop):
    async def _invoke_model(self, step, request, route, context, control, budget, *args, **kwargs):
        value = exchange(
            {"type": "model", "request": request.model_dump(mode="json"), "index": step.index}
        )
        return AgentCallResponse.model_validate(value)


class ChildControl:
    id = UUID(int=0)
    deadline_at = float("inf")
    phase_deadline: float | None = None
    cleanup_confirmed = True
    stop_reason = None
    remaining = float("inf")

    def mark_cleanup_unconfirmed(self):
        self.cleanup_confirmed = False

    def require_running(self):
        pass

    async def perform(self, operation, **kwargs):
        return await operation()


async def run(initial):
    state = AgentState(
        [
            ChatMessage(
                role="system",
                content=(
                    "你是独立的分析子 Agent。只完成当前子任务，返回简明、可供主 Agent "
                    "继续使用的事实与结论，如有数据缺口请明确说明。"
                    f"每轮最多发出 {RUN_STREAM_LIMITS['toolsPerStep']} 个工具调用。\n"
                    + DATABASE_PROMPT
                ),
            ),
            ChatMessage(role="user", content=initial["prompt"]),
        ]
    )
    tools = [ToolDefinition.model_validate(value) for value in initial["tools"]]
    executor = ChildExecutor()

    def prepare():
        request = model_input(
            state.messages,
            state.continuation_token,
            tools=tools,
            controlled=initial["controlled"],
        )
        return request, None

    async def accept(step, result, _prepared):
        if not result.tool_calls:
            return RunCompleted(
                status="completed",
                finalAnswer=executor._final_answer(result.text),
                file=AnalysisFileNone(status="none"),
            )
        if len(result.tool_calls) > RUN_STREAM_LIMITS["toolsPerStep"]:
            raise RunFailure("model_incomplete")
        if step.index >= MAX_STEPS:
            raise RunFailure("step_limit")
        return None

    async def dispatch(step, _request):
        results = exchange(
            {
                "type": "tools",
                "index": step.index,
                "calls": [call.model_dump(mode="json") for call in step.tool_calls],
            }
        )
        for result in results:
            state.record_tool_result(step, ToolResult(**result))

    value = await executor.execute_tools(
        state,
        None,
        None,
        ChildControl(),
        lambda event: None,
        prepare,
        accept,
        dispatch,
        publish_text=False,
    )
    assert isinstance(value, RunCompleted)
    send({"type": "done", "answer": value.final_answer})


if __name__ == "__main__":
    try:
        asyncio.run(run(receive()))
    except RunFailure as error:
        send({"type": "error", "code": error.code})
    except (OSError, ValueError):
        send({"type": "error", "code": "subagent_protocol_error"})
