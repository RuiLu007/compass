"""Business actions and turn handling for the Compass analysis harness."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid5

from compass_agent.harness.autonomy import AutonomyStopped
from compass_agent.harness.contracts import Step, ToolResult
from compass_agent.models.errors import ModelError
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    RunProgressEvent,
    ToolEmptyPayload,
    ToolFailedPayload,
    ToolFinished,
    ToolStarted,
    ToolStartedPayload,
    ToolSucceededPayload,
)
from compass_agent.tools.builtin.todo_write.plan import TodoRejected
from compass_agent.tools.business.database_query import DATABASE_QUERY
from compass_agent.tools.business.database_query.handler import execute as execute_database_query
from compass_agent.tools.business.retail_sales.contracts import RetailActionState, RetailRoute
from compass_agent.tools.business.retail_sales.export import (
    select_export_candidate,
)
from compass_agent.tools.business.retail_sales.handler import (
    PreparedRetailCall,
    RetailQueryResult,
    failed_retail_result,
    prepare_retail_call,
    retail_observation,
)
from compass_agent.tools.business.retail_sales.session import (
    ActionOutcome,
    RetailSession,
)
from compass_agent.tools.contracts import ExecutionError as WorkflowError
from compass_agent.tools.contracts import (
    ToolControl,
    json_bytes,
)

EmitProgress = Callable[[RunProgressEvent], None]
RetailQuery = Callable[[PreparedRetailCall, ToolControl], Awaitable[RetailQueryResult]]
SCRIPT_REPAIR_INSTRUCTION = (
    "重新调用 run_dynamic_workflow，修正 JS 函数体后再执行。"
    "直接使用 args、agent、parallel，执行成功后引用新的 executionId。"
)


class RetailActions:
    """Trusted built-in tool handlers; the Harness owns the inference loop."""

    def __init__(self, retail_query):
        self.retail_query = retail_query

    async def _classic_actions(
        self,
        state: RetailActionState,
        step: Step,
        route: RetailRoute,
        control: ToolControl,
        emit: EmitProgress,
        *,
        tool_index_offset: int = 0,
    ) -> None:
        tool_index = tool_index_offset
        for call in step.tool_calls:
            control.require_running()
            if call.function.name == DATABASE_QUERY:
                if call.function.name == DATABASE_QUERY and route.database_enabled:
                    tool_index += 1
                outcome = await self._database_action(
                    state,
                    step,
                    call,
                    tool_index,
                    route,
                    control,
                    emit,
                )
                state.record_tool_result(
                    step,
                    ToolResult(
                        call.id,
                        "ok" if outcome.ok else "error",
                        outcome.observation or f'{{"status":"error","code":"{outcome.code}"}}',
                    ),
                )
                continue
            tool_index += 1
            prepared = prepare_retail_call(
                call, None if route.database_only else route.retail_binding
            )
            emit(
                ToolStarted(
                    type="tool.started",
                    payload=ToolStartedPayload(
                        stepId=str(step.id),
                        toolCallId=call.id,
                        toolIndex=tool_index,
                        toolKind=prepared.tool_kind,
                        params=prepared.params,
                        inputPreview=prepared.params.model_dump_json(by_alias=True)
                        if prepared.params
                        else None,
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
            query_id = str(uuid5(control.id, str(step.id) + call.id))
            if outcome.status == "ok":
                state.retail_results[query_id] = outcome
            tool_result = ToolResult(
                tool_call_id=call.id,
                status=outcome.status,
                observation_json=retail_observation(outcome, query_id=query_id),
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
        return None

    async def _controlled_actions(
        self,
        state: RetailActionState,
        step: Step,
        route: RetailRoute,
        control: ToolControl,
        run: RetailSession,
        emit: EmitProgress,
        *,
        admitted: bool = False,
        tool_index_offset: int = 0,
    ) -> None:
        calls = step.tool_calls
        blocked = False
        tool_index = tool_index_offset
        for index, call in enumerate(calls, 1 + tool_index_offset):
            control.require_running()
            run.budget.require_continue()
            trace_id = state.trace.begin_call(str(step.id), call.id) if state.trace else None
            name = call.function.name
            if blocked:
                outcome = ActionOutcome("unknown", "blocked_by_prior_action")
            elif not route.tools_enabled:
                outcome = ActionOutcome("unknown", "retail_unavailable")
            elif route.database_only and name != DATABASE_QUERY:
                outcome = ActionOutcome("unknown", "unknown_tool")
            elif name == "retail_sales":
                prepared = prepare_retail_call(call, route.retail_binding)
                try:
                    todo_id = run.admit_query(prepared)
                except TodoRejected as error:
                    outcome = ActionOutcome("retail", error.code)
                else:
                    tool_index += 1
                    result = await control.perform(
                        lambda prepared=prepared, index=tool_index: self._controlled_query(
                            control, step, index, prepared, run, emit, counted=admitted
                        ),
                        timeout_s=8,
                    )
                    outcome = run.observe_query(prepared, result, str(step.id), todo_id)
                    if outcome.ok:
                        state.export_candidate = select_export_candidate(
                            state.export_candidate, step.id, call.id, result
                        )
            elif name == DATABASE_QUERY and run.plan.version:
                outcome = ActionOutcome("database", "database_plan_conflict")
            elif name == DATABASE_QUERY:
                if route.database_enabled:
                    if not admitted:
                        run.budget.start_tool()
                    tool_index += 1
                    key = (name, call.function.arguments)
                    run.stop.require_query(key)
                    run.stop.start_query(key)
                outcome = await self._database_action(
                    state,
                    step,
                    call,
                    tool_index,
                    route,
                    control,
                    emit,
                )
            elif state.hybrid and state.hybrid.owns_tool(name):
                if not admitted:
                    run.budget.start_tool()
                try:
                    data = await state.hybrid.tool(call)
                    outcome = ActionOutcome(
                        "unknown",
                        "ok",
                        ok=True,
                        progress=True,
                        observation=json_bytes(data).decode(),
                    )
                except (WorkflowError, ModelError, ValueError, RecursionError) as error:
                    code = str(error) if isinstance(error, WorkflowError) else "workflow_invalid"
                    outcome = ActionOutcome("unknown", code)
                    if name == "run_dynamic_workflow" and code == "script_failed":
                        outcome = ActionOutcome(
                            "unknown",
                            code,
                            observation=json_bytes(
                                {
                                    "status": "error",
                                    "code": code,
                                    "nextAction": SCRIPT_REPAIR_INSTRUCTION,
                                }
                            ).decode(),
                        )
            elif name == "todo_write":
                try:
                    action = run.parse(call)
                except (ValueError, RecursionError):
                    outcome = ActionOutcome("unknown", "invalid_parameters")
                else:
                    outcome = run.apply(action, step.index, counted=admitted)
            else:
                outcome = ActionOutcome("unknown", "unknown_tool")

            state.record_tool_result(
                step, ToolResult(call.id, "ok" if outcome.ok else "error", run.observation(outcome))
            )
            if state.trace and trace_id:
                state.trace.finish(
                    trace_id,
                    "completed" if outcome.ok else "failed",
                )
            emit(run.action_event(str(step.id), index, outcome))
            if outcome.kind == "database" and outcome.code == "retail_query_timeout":
                raise AutonomyStopped("execution_timeout")
            if not blocked:
                run.stop.record(ok=outcome.ok, progress=outcome.progress)
            blocked = not outcome.ok
        return None

    async def _database_action(self, state, step, call, index, route, control, emit):
        if not route.database_enabled or route.retail_binding is None:
            return ActionOutcome("database", "retail_unavailable")
        return await execute_database_query(
            state,
            step,
            call,
            index,
            route.retail_binding,
            control,
            emit,
        )

    async def _controlled_query(
        self,
        control: ToolControl,
        step: Step,
        index: int,
        prepared: PreparedRetailCall,
        run: RetailSession,
        emit: EmitProgress,
        *,
        counted: bool = False,
    ) -> RetailQueryResult:
        run.start_query(prepared, counted=counted)
        emit(
            ToolStarted(
                type="tool.started",
                payload=ToolStartedPayload(
                    stepId=str(step.id),
                    toolCallId=prepared.tool_call_id,
                    toolIndex=index,
                    toolKind=prepared.tool_kind,
                    params=prepared.params,
                    inputPreview=prepared.params.model_dump_json(by_alias=True)
                    if prepared.params
                    else None,
                ),
            )
        )
        result = await self.retail_query(prepared, control)
        control.require_running()
        if result.status == "ok" and not 1 <= len(result.rows) <= RUN_STREAM_LIMITS["fileRows"]:
            assert prepared.params is not None
            result = RetailQueryResult(
                "error",
                prepared.params.dimension,
                prepared.params,
                error_code="retail_query_failed",
            )
        emit(
            ToolFinished(
                type="tool.finished",
                payload=self._tool_finished(step, index, prepared.tool_call_id, result),
            )
        )
        return result

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
            "resultPreview": retail_observation(outcome),
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
