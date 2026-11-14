"""Bind task completion and tools to the generic Harness loop."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from compass_agent.harness.contracts import RunFailure
from compass_agent.harness.executor import AgentExecutor
from compass_agent.runtime.context import (
    AgentDefinition,
    RunRoute,
    generate_input,
)
from compass_agent.runtime.session import RuntimeSession
from compass_agent.runtime.task_state import TaskState
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    AnalysisFileNone,
    RunCompleted,
    RunProgressEvent,
    RunResult,
    TrustedContext,
)
from compass_agent.tools.contracts import (
    ToolControl,
)

if TYPE_CHECKING:
    from compass_agent.workflows.assets import WorkflowAssets
    from compass_agent.workflows.drafts import WorkflowDrafts


EmitProgress = Callable[[RunProgressEvent], None]


class RuntimeTurn(AgentExecutor):
    definition: AgentDefinition
    workflow_assets: WorkflowAssets | None = None
    workflow_drafts: WorkflowDrafts | None = None

    async def execute_turn(
        self,
        state: TaskState,
        route: RunRoute,
        context: TrustedContext,
        control: ToolControl,
        controlled: RuntimeSession | None,
        emit: EmitProgress,
        *,
        publish_text: bool = True,
        max_steps: int | None = None,
    ) -> RunResult:
        from compass_agent.execution_context import current_execution, execution_path

        if controlled and (journal := current_execution.get()):
            journal.state_providers[execution_path.get()] = controlled.checkpoint_state
        budget = controlled.budget if controlled else None

        def prepare():
            request = generate_input(
                self.definition,
                state.messages,
                state.continuation_token,
                route,
                controlled=controlled is not None,
            )
            if state.tools is not None:
                request = request.model_copy(update={"tools": state.tools.definitions()})
            return request, None

        async def handle(step, result, prepared):
            calls = tuple(result.tool_calls)
            if state.trace:
                state.trace.bind_calls(str(step.id), calls, ())
            if not calls:
                control.require_running()
                candidate = RunCompleted(
                    status="completed",
                    finalAnswer=self._final_answer(result.text),
                    file=AnalysisFileNone(status="none"),
                )
                return candidate
            if len(calls) > RUN_STREAM_LIMITS["toolsPerStep"]:
                raise RunFailure("model_incomplete")
            if not controlled and max_steps is not None and step.index >= max_steps:
                raise RunFailure("step_limit")
            return None

        async def dispatch(step, request):
            if state.tools is None:
                raise RunFailure("internal_error")
            return await state.tools.dispatch(step, request.tools)

        return await self.execute_tools(
            state,
            route,
            context,
            control,
            emit,
            prepare,
            handle,
            dispatch,
            budget=budget,
            publish_text=publish_text,
        )
