"""Compose generic model routing, authorized tools and durable Run execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from traceback import extract_tb
from uuid import uuid5

from sqlalchemy.exc import DBAPIError

from compass_agent.harness.autonomy import AutonomyBudget, AutonomyLimits, AutonomyStopped
from compass_agent.harness.context import ContextLimit
from compass_agent.harness.contracts import RunFailure
from compass_agent.harness.verification import (
    RunVerifier,
    VerificationContext,
    VerificationRejected,
    verify_result,
)
from compass_agent.models.errors import ModelError
from compass_agent.models.service import ModelInvocationCanceled, ModelService
from compass_agent.routing.service import RoutingService, SelectedRoute
from compass_agent.runtime.context import AgentDefinition, RunRoute, initial_messages
from compass_agent.runtime.control import RunControl
from compass_agent.runtime.execution_trace import ExecutionTrace
from compass_agent.runtime.session import RuntimeSession
from compass_agent.runtime.task_state import TaskState
from compass_agent.runtime.tool_bindings import RunToolBindings
from compass_agent.runtime.turn import RuntimeTurn
from compass_agent.schemas import (
    AnalysisRunInput,
    ChatCallInput,
    ChatMessage,
    GenerateInput,
    Intent,
    RunCompleted,
    RunError,
    RunExtensionPayload,
    RunFailed,
    RunProgressEvent,
    RunResult,
    RunRouted,
    RunRoutedPayload,
    RunWaiting,
    StreamExtension,
    TrustedContext,
)
from compass_agent.settings import AutonomyModelProfile
from compass_agent.tools.builtin.create_workflow import NAME as CREATE_WORKFLOW
from compass_agent.tools.catalog import definitions as catalog_definitions
from compass_agent.tools.catalog_contracts import CatalogContext
from compass_agent.tools.contracts import (
    PhaseCanceled,
    RegisteredTool,
    ResourceUnconfirmed,
    StopRequested,
    ToolControl,
    ToolRejected,
)
from compass_agent.tools.loader import ToolLoader
from compass_agent.tools.registry import has_catalog_room, validate_catalog

logger = logging.getLogger(__name__)


def _failure_diagnostic(emit, control, stage: str, error: Exception, reason: str) -> None:
    cause = error.orig if isinstance(error, DBAPIError) else error
    logger.error(
        "analysis.run.exception run_id=%s stage=%s exception=%s reason=%s frames=%s",
        control.id,
        stage,
        type(cause).__name__,
        reason,
        [(frame.filename, frame.lineno, frame.name) for frame in extract_tb(error.__traceback__)],
    )
    emit(
        StreamExtension(
            type="stream.extension",
            payload=RunExtensionPayload(
                name="compass.runtime.failure",
                version=1,
                required=False,
                data={"stage": stage, "exceptionType": type(cause).__name__, "reasonCode": reason},
            ),
        )
    )


EmitProgress = Callable[[RunProgressEvent], None]
ToolsProvider = Callable[
    [TrustedContext, ToolControl, EmitProgress], Awaitable[tuple[RegisteredTool, ...]]
]


class RunService(RuntimeTurn):
    def __init__(
        self,
        models: ModelService,
        routing: RoutingService,
        *,
        definition: AgentDefinition | None = None,
        tool_loader: ToolLoader | None = None,
        tools_provider: ToolsProvider | None = None,
        autonomy_profiles: tuple[AutonomyModelProfile, ...] | None = None,
        autonomy_limits: AutonomyLimits | None = None,
        verifiers: tuple[RunVerifier, ...] = (),
        task_verifiers: Mapping[str, tuple[RunVerifier, ...]] | None = None,
        workflow_assets=None,
        workflow_drafts=None,
    ):
        super().__init__(models)
        self.tool_loader = tool_loader or ToolLoader()
        self.routing, self.definition = routing, definition or AgentDefinition()
        self.tools_provider = tools_provider
        self.workflow_assets, self.workflow_drafts = workflow_assets, workflow_drafts
        self.autonomy_profiles = autonomy_profiles
        self.autonomy_limits = autonomy_limits or AutonomyLimits()
        self.verifiers, self.task_verifiers = verifiers, dict(task_verifiers or {})

    async def run(
        self,
        command: AnalysisRunInput,
        context: TrustedContext,
        control: RunControl,
        emit: EmitProgress,
        *,
        verifiers: tuple[RunVerifier, ...] = (),
    ) -> RunResult:
        trace = ExecutionTrace(emit)
        emit = trace.emit
        state: TaskState | None = None
        discovered = None
        limits = self.autonomy_limits
        if command.constraints and command.constraints.max_steps is not None:
            limits = replace(limits, steps=min(limits.steps, command.constraints.max_steps))
        controlled = (
            RuntimeSession(self.autonomy_profiles, limits)
            if self.autonomy_profiles is not None
            else None
        )
        from compass_agent.execution_context import current_execution

        journal = current_execution.get()
        stop_reason: str | None = None
        candidate: RunResult = RunFailed(status="failed", errorCode="internal_error")
        stage = "tool_setup"
        try:
            # Retained user assets are readable, but retired execution contracts are unavailable.
            if command.workflow_asset_ref is not None:
                raise RunFailure("workflow_unavailable")
            discovered = await control.perform(lambda: self.tool_loader.discover(context, control))
            provider = self.tools_provider
            provided = (
                await control.perform(lambda: provider(context, control, emit)) if provider else ()
            )
            entries = (*self.definition.tools, *provided)
            declarations = [entry.definition for entry in entries if entry.definition is not None]
            snapshot = (
                await control.perform(lambda: asyncio.to_thread(journal.catalog_declarations))
                if journal and self.workflow_assets is not None
                else None
            )
            declarations.extend(
                catalog_definitions(
                    CatalogContext(
                        self.definition.builtin_tools,
                        sandbox=self.tool_loader.sandbox is not None,
                    )
                )
            )
            if discovered:
                declarations.extend(
                    entry.definition for entry in discovered.entries if entry.definition
                )
            workflow_definitions = catalog_definitions(
                CatalogContext(frozenset({"workflow"}), workflow=True)
            )
            workflow = (
                "workflow" in self.definition.builtin_tools
                and self.workflow_assets is not None
                and (
                    CREATE_WORKFLOW in snapshot
                    if snapshot is not None
                    else has_catalog_room([*declarations, *workflow_definitions])
                )
            )
            if workflow:
                declarations.extend(workflow_definitions)
            validate_catalog(declarations)
            budget = controlled.budget if controlled else None
            stage = "routing"
            selected = await control.perform(
                lambda: self._select_route(
                    command,
                    context,
                    control,
                    budget,
                    command.question,
                    intent_hint=None,
                )
            )
            stage = "tool_setup"
            capabilities = selected.provider.capabilities
            supported = capabilities.tools.state == "supported"
            route = RunRoute(
                provider_id=selected.provider.id,
                binding_version=selected.provider.binding_version,
                model_label=selected.provider.alias,
                scene_id=selected.intent.scene_id,
                route_prompt=selected.prompt,
                stream=capabilities.stream.state == "supported",
                thinking=capabilities.thinking.state == "supported",
                deepseek=selected.provider.vendor == "deepseek"
                and (journal is None or getattr(journal, "deepseek_thinking", False)),
                tools_enabled=supported and bool(declarations),
            )
            state = TaskState(
                initial_messages(self.definition, command, route, workflow=workflow and supported)
            )
            state.trace = trace
            bindings = RunToolBindings(
                self,
                state,
                route,
                context,
                control,
                controlled,
                emit,
                self.tool_loader,
                discovered,
                supported,
                entries=entries,
                workflow=workflow,
            )
            state.tools = bindings
            if workflow and supported:
                await control.perform(lambda: bindings.with_workflows(declarations, snapshot))
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
            hooks = self.verifiers + self.task_verifiers.get(route.scene_id, ()) + verifiers
            stage = "execution"
            candidate = await self.execute_turn(
                state,
                route,
                context,
                control,
                controlled,
                emit,
                publish_text=not hooks,
                max_steps=command.constraints.max_steps if command.constraints else None,
            )
            if isinstance(candidate, RunCompleted):
                candidate = candidate.model_copy(update={"artifacts": list(state.tools.artifacts)})
            if hooks and isinstance(candidate, RunCompleted | RunWaiting):
                stage = "verification"
                proposed = candidate
                try:
                    await control.perform(
                        lambda: verify_result(
                            proposed,
                            VerificationContext(control.id, context, route.scene_id),
                            hooks,
                        )
                    )
                except VerificationRejected:
                    candidate = RunFailed(status="failed", errorCode="internal_error")
                    stop_reason = "verification_rejected"
        except StopRequested:
            self._close_open_step(state, "canceled", emit)
        except (AutonomyStopped, TimeoutError) as error:
            self._close_open_step(state, "failed", emit)
            stop_reason = (
                error.reason if isinstance(error, AutonomyStopped) else "execution_timeout"
            )
            code: RunError = "internal_error"
            if stop_reason == "execution_timeout":
                code = "run_timeout"
            elif stop_reason == "max_steps":
                code = "step_limit"
            elif stop_reason == "model_budget_unavailable":
                code = "model_unavailable"
            candidate = RunFailed(status="failed", errorCode=code)
        except ToolRejected as error:
            self._close_open_step(state, "failed", emit)
            candidate = RunFailed(
                status="failed",
                errorCode=(
                    "tool_catalog_limit"
                    if error.code == "tool_catalog_limit"
                    else "tool_setup_failed"
                ),
            )
        except ContextLimit:
            self._close_open_step(state, "failed", emit)
            candidate = RunFailed(status="failed", errorCode="context_limit")
        except ResourceUnconfirmed:
            control.mark_cleanup_unconfirmed()
            self._close_open_step(state, "canceled", emit)
        except RunFailure as error:
            self._close_open_step(state, "failed", emit)
            _failure_diagnostic(emit, control, stage, error, error.reason)
            candidate = RunFailed(status="failed", errorCode=error.code)
        except ModelError as error:
            self._close_open_step(state, "failed", emit)
            if error.code in {"intent_invalid", "intent_unavailable"}:
                stop_reason = error.code
            _failure_diagnostic(emit, control, stage, error, error.code)
            code = (
                "workflow_failed"
                if error.code == "workflow_replay_mismatch"
                else self._model_error(error.code)
            )
            candidate = RunFailed(status="failed", errorCode=code)
        except Exception as error:
            self._close_open_step(state, "failed", emit)
            sqlstate = (
                getattr(error.orig, "sqlstate", None) if isinstance(error, DBAPIError) else None
            )
            reason = (
                "database_schema_mismatch"
                if sqlstate in {"42703", "42P01"}
                else "unexpected_exception"
            )
            _failure_diagnostic(emit, control, stage, error, reason)
            candidate = RunFailed(status="failed", errorCode="internal_error")
        finally:
            if discovered is not None:
                await discovered.close()
        result = control.commit(candidate)
        trace.settle(result.status)
        if controlled:
            emit(controlled.result_event(result, stop_reason))
        return result

    async def _select_route(self, command, context, control, budget, question, **options):
        from compass_agent.execution_context import recorded

        return await recorded(
            "routing",
            "routing",
            {
                "question": question,
                "selection": command.selection.model_dump(mode="json"),
                **{
                    key: value.model_dump(mode="json") if isinstance(value, Intent) else value
                    for key, value in options.items()
                },
            },
            lambda: self._select_route_live(command, context, control, budget, question, **options),
            SelectedRoute,
            budget=budget,
        )

    async def _select_route_live(
        self,
        command: AnalysisRunInput,
        context: TrustedContext,
        control: RunControl,
        budget: AutonomyBudget | None,
        question: str,
        *,
        intent_hint: Intent | None,
    ) -> SelectedRoute:
        routing_command = ChatCallInput(
            call_id=uuid5(control.id, "routing"),
            main_model=command.selection,
            input=GenerateInput(messages=[ChatMessage(role="user", content=question)]),
        )
        try:
            return await self.routing.select_route(
                routing_command,
                context,
                deadline=control.deadline_at,
                check=control.require_running,
                intent_hint=intent_hint,
                **({"attempt_budget": budget} if budget is not None else {}),
            )
        except ModelInvocationCanceled as error:
            raise PhaseCanceled(cleanup_confirmed=error.cleanup_confirmed) from None
