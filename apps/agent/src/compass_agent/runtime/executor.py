"""Finite ReAct runner for one authorized analysis Run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4, uuid5

from compass_agent.llm.policy import LogicalPolicy
from compass_agent.models.errors import ModelError
from compass_agent.models.service import ModelInvocationCanceled, ModelService
from compass_agent.routing.service import RoutingService, SelectedRoute
from compass_agent.runtime.artifact import (
    ExportCandidate,
    build_analysis_file,
    select_export_candidate,
)
from compass_agent.runtime.context import (
    AgentDefinition,
    ContextLimit,
    RunRoute,
    generate_input,
    initial_messages,
)
from compass_agent.runtime.control import (
    PhaseCanceled,
    ResourceUnconfirmed,
    RunControl,
    StopRequested,
)
from compass_agent.runtime.retail import (
    PreparedRetailCall,
    RetailQueryResult,
    binding_for,
    failed_retail_result,
    prepare_retail_call,
    query_retail,
    retail_observation,
)
from compass_agent.runtime.stream import json_bytes
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    AnalysisRunInput,
    ChatCallInput,
    ChatMessage,
    GenerateInput,
    RunCompleted,
    RunError,
    RunFailed,
    RunProgressEvent,
    RunResult,
    RunRouted,
    RunRoutedPayload,
    StepFinished,
    StepFinishedPayload,
    StepStarted,
    StepStartedPayload,
    ToolCall,
    ToolEmptyPayload,
    ToolFailedPayload,
    ToolFinished,
    ToolStarted,
    ToolStartedPayload,
    ToolSucceededPayload,
    TrustedContext,
)
from compass_agent.settings import Settings

StepStatus = Literal["running", "completed", "failed", "canceled"]
EmitProgress = Callable[[RunProgressEvent], None]
RetailQuery = Callable[[PreparedRetailCall, RunControl], Awaitable[RetailQueryResult]]


class RunFailure(Exception):
    def __init__(self, code: RunError):
        self.code: RunError = code
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
    export_candidate: ExportCandidate | None = field(default=None, repr=False)

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


class AgentExecutor:
    def __init__(
        self,
        models: ModelService,
        routing: RoutingService,
        settings: Settings,
        *,
        definition: AgentDefinition | None = None,
        retail_query: RetailQuery = query_retail,
    ):
        self.models = models
        self.routing = routing
        self.settings = settings
        self.definition = definition or AgentDefinition()
        self.retail_query = retail_query

    async def run(
        self,
        command: AnalysisRunInput,
        context: TrustedContext,
        control: RunControl,
        emit: EmitProgress,
    ) -> RunResult:
        state: AgentState | None = None
        candidate: RunResult = RunFailed(status="failed", errorCode="internal_error")
        try:
            selected = await control.perform(lambda: self._select_route(command, context, control))
            route = self._run_route(selected, context)
            state = AgentState(initial_messages(self.definition, command, route))
            emit(
                RunRouted(
                    type="run.routed",
                    payload=RunRoutedPayload(
                        providerId=str(route.provider_id),
                        modelLabel=route.model_label,
                        sceneId=route.scene_id,
                        toolsEnabled=route.tools_enabled,
                    ),
                )
            )

            for index in range(1, RUN_STREAM_LIMITS["steps"] + 1):
                control.require_running()
                step = state.start_step(index)
                emit(
                    StepStarted(
                        type="step.started",
                        payload=StepStartedPayload(
                            stepId=str(step.id),
                            stepIndex=step.index,
                            modelCallId=str(step.model_call_id),
                        ),
                    )
                )
                request = generate_input(
                    self.definition,
                    state.messages,
                    state.continuation_token,
                    route,
                )
                result = await control.perform(
                    lambda step=step, request=request: self._invoke_model(
                        step, request, route, context, control
                    )
                )
                self._require_complete(result)
                calls = tuple(result.tool_calls)
                if not calls:
                    answer = self._final_answer(result.text)
                    control.require_running()
                    candidate = RunCompleted(
                        status="completed",
                        finalAnswer=answer,
                        file=build_analysis_file(state.export_candidate, control.id),
                    )
                    self._finish_step(step, "completed", emit)
                    break
                if len(calls) > RUN_STREAM_LIMITS["toolsPerStep"]:
                    raise RunFailure("model_incomplete")
                if index == RUN_STREAM_LIMITS["steps"]:
                    raise RunFailure("step_limit")
                if not result.continuation_token:
                    raise RunFailure("model_incomplete")
                step.tool_calls = calls
                state.messages.append(
                    ChatMessage(
                        role="assistant",
                        content=result.text or None,
                        tool_calls=list(calls),
                    )
                )
                state.continuation_token = result.continuation_token
                for tool_index, call in enumerate(calls, start=1):
                    control.require_running()
                    prepared = prepare_retail_call(call, route.retail_binding)
                    emit(
                        ToolStarted(
                            type="tool.started",
                            payload=ToolStartedPayload(
                                stepId=str(step.id),
                                toolCallId=call.id,
                                toolIndex=tool_index,
                                toolKind=prepared.tool_kind,
                                params=prepared.params,
                            ),
                        )
                    )
                    outcome = (
                        failed_retail_result(prepared)
                        if prepared.safe_error is not None
                        else await control.perform(
                            lambda prepared=prepared: self.retail_query(prepared, control)
                        )
                    )
                    control.require_running()
                    tool_result = ToolResult(
                        tool_call_id=call.id,
                        status=outcome.status,
                        observation_json=retail_observation(outcome),
                    )
                    state.record_tool_result(step, tool_result)
                    state.export_candidate = select_export_candidate(
                        state.export_candidate,
                        step.id,
                        call.id,
                        outcome,
                    )
                    emit(
                        ToolFinished(
                            type="tool.finished",
                            payload=self._tool_finished(
                                step,
                                tool_index,
                                call.id,
                                outcome,
                            ),
                        )
                    )
                self._finish_step(step, "completed", emit)
            else:  # pragma: no cover - the sixth-step branch above is authoritative
                raise RunFailure("step_limit")
        except StopRequested:
            self._close_open_step(state, "canceled", emit)
        except ContextLimit:
            self._close_open_step(state, "failed", emit)
            candidate = RunFailed(status="failed", errorCode="context_limit")
        except ResourceUnconfirmed:
            control.mark_cleanup_unconfirmed()
            self._close_open_step(state, "canceled", emit)
        except RunFailure as error:
            self._close_open_step(state, "failed", emit)
            candidate = RunFailed(status="failed", errorCode=error.code)
        except ModelError as error:
            self._close_open_step(state, "failed", emit)
            candidate = RunFailed(status="failed", errorCode=self._model_error(error.code))
        except Exception:
            self._close_open_step(state, "failed", emit)
            candidate = RunFailed(status="failed", errorCode="internal_error")
        return control.commit(candidate)

    async def _select_route(
        self,
        command: AnalysisRunInput,
        context: TrustedContext,
        control: RunControl,
    ) -> SelectedRoute:
        routing_command = ChatCallInput(
            call_id=uuid5(control.id, "routing"),
            main_model=command.selection,
            input=GenerateInput(messages=[ChatMessage(role="user", content=command.question)]),
        )
        try:
            return await self.routing.select_route(
                routing_command,
                context,
                deadline=control.deadline_at,
                check=control.require_running,
            )
        except ModelInvocationCanceled as error:
            raise PhaseCanceled(cleanup_confirmed=error.cleanup_confirmed) from None

    def _run_route(self, selected: SelectedRoute, context: TrustedContext) -> RunRoute:
        capabilities = selected.provider.capabilities
        retail = None
        if (
            not selected.off_topic
            and selected.features.response_mode == "answer"
            and capabilities.tools.state == "supported"
        ):
            retail = binding_for(self.settings, context)
        return RunRoute(
            provider_id=selected.provider.id,
            binding_version=selected.provider.binding_version,
            model_label=selected.provider.alias,
            scene_id=selected.intent.scene_id,
            route_prompt=selected.prompt,
            stream=capabilities.stream.state == "supported",
            thinking=capabilities.thinking.state == "supported",
            retail_binding=retail,
        )

    async def _invoke_model(
        self,
        step: Step,
        request: GenerateInput,
        route: RunRoute,
        context: TrustedContext,
        control: RunControl,
    ):
        async def discard_delta(_event) -> None:
            return None

        try:
            # Routing leaves the request-scoped ModelService on its classifier stage.
            # Reset it before prepare() creates the logical generation observation.
            self.models.stage = "inference"
            prepared = await self.models.prepare(
                step.model_call_id,
                route.provider_id,
                request,
                context,
                deadline=control.deadline_at,
                expected_binding_version=route.binding_version,
            )
            return await self.models.run(
                prepared,
                request,
                emit=discard_delta if request.stream else None,
                logical_policy=LogicalPolicy(max_attempts=2),
                tool_output_validation="agent_dispatch",
            )
        except ModelInvocationCanceled as error:
            raise PhaseCanceled(cleanup_confirmed=error.cleanup_confirmed) from None

    @staticmethod
    def _require_complete(result) -> None:
        if result.error_code == "resource_cleanup_failed":
            raise ResourceUnconfirmed("model cleanup unconfirmed")
        if result.status != "completed":
            if result.status == "incomplete":
                raise RunFailure("model_incomplete")
            raise RunFailure("model_failed")
        expected = "tool_calls" if result.tool_calls else "stop"
        if result.finish_reason != expected:
            raise RunFailure("model_incomplete")

    @staticmethod
    def _final_answer(value: str) -> str:
        answer = value.strip()
        if not answer or len(json_bytes(answer)) > RUN_STREAM_LIMITS["answerBytes"]:
            raise RunFailure("model_incomplete")
        return answer

    @staticmethod
    def _model_error(code: str) -> RunError:
        if code in {
            "model_unavailable",
            "route_binding_missing",
            "chat_model_not_supported",
            "capability_not_supported",
        }:
            return "model_unavailable"
        return "model_failed"

    @staticmethod
    def _tool_finished(
        step: Step,
        tool_index: int,
        tool_call_id: str,
        outcome: RetailQueryResult,
    ):
        common = {
            "stepId": str(step.id),
            "toolCallId": tool_call_id,
            "toolIndex": tool_index,
        }
        if outcome.status == "ok":
            return ToolSucceededPayload(**common, status="ok", rowCount=len(outcome.rows))
        if outcome.status == "empty":
            return ToolEmptyPayload(**common, status="empty", rowCount=0)
        return ToolFailedPayload(
            **common,
            status="error",
            errorCode=outcome.error_code or "retail_query_failed",
        )

    @staticmethod
    def _finish_step(
        step: Step,
        status: Literal["completed", "failed", "canceled"],
        emit: EmitProgress,
    ) -> None:
        if step.status != "running":
            return
        step.status = status
        emit(
            StepFinished(
                type="step.finished",
                payload=StepFinishedPayload(stepId=str(step.id), status=status),
            )
        )

    def _close_open_step(
        self,
        state: AgentState | None,
        status: Literal["failed", "canceled"],
        emit: EmitProgress,
    ) -> None:
        if state is not None and state.steps:
            self._finish_step(state.steps[-1], status, emit)
