"""Per-Run state of registered Retail planning and verification tools."""

import json
import re
from dataclasses import dataclass
from typing import Literal

from compass_agent.harness.autonomy import (
    BUDGET_REASONS,
    AutonomyBudget,
    AutonomyLimits,
    AutonomyStopped,
    StopPolicy,
)
from compass_agent.schemas import (
    RunAutonomyAction,
    RunAutonomyResult,
    RunContractModel,
    RunExtensionPayload,
    RunResult,
    StreamExtension,
    ToolCall,
)
from compass_agent.settings import AutonomyModelProfile
from compass_agent.tools.builtin.todo_write.plan import TodoRejected
from compass_agent.tools.business.retail_sales.evidence import (
    RetailVerifier,
    VerificationFailure,
    VerifiedQuery,
    query_key,
)
from compass_agent.tools.business.retail_sales.handler import (
    PreparedRetailCall,
    RetailQueryResult,
    parse_tool_arguments,
    retail_observation,
)
from compass_agent.tools.business.retail_sales.plan import (
    TODO_ACTION,
    CompleteTodo,
    ExecutionPlan,
    ReplaceTodo,
    SelectTodo,
    SkipTodo,
)

type ActionKind = Literal[
    "replace", "select", "complete", "skip", "retail", "database", "submit", "unknown"
]
HUMAN_REASONS = {"usage_bound_violation"}
type ParsedAction = ReplaceTodo | SelectTodo | CompleteTodo | SkipTodo

PLAN_REQUEST = re.compile(
    r"^(?:请先列计划|先列计划|请先列出计划|请先制定计划)(?:$|[\s，。：、,.!?！？:]+)"
)


def plan_request(question: str) -> re.Match[str] | None:
    return PLAN_REQUEST.match(question.strip())


def extension(name: str, data: RunContractModel) -> StreamExtension:
    return StreamExtension(
        type="stream.extension",
        payload=RunExtensionPayload(
            name=f"compass.autonomy.{name}",
            version=1,
            required=False,
            data=data.model_dump(mode="json", by_alias=True),
        ),
    )


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    kind: ActionKind
    code: str
    ok: bool = False
    progress: bool = False
    observation: str | None = None
    todo_id: str | None = None
    failure: VerificationFailure | None = None


class RetailSession:
    def __init__(
        self,
        question: str,
        profiles: tuple[AutonomyModelProfile, ...],
        limits: AutonomyLimits,
        *,
        require_plan: bool = False,
    ) -> None:
        self.budget = AutonomyBudget(profiles, limits)
        self.stop = StopPolicy()
        self.plan = ExecutionPlan()
        self.verifier = RetailVerifier()
        prefix = plan_request(question)
        self.require_plan = require_plan or prefix is not None
        self.missing_data = False

    def checkpoint_state(self):
        """Plain, versioned business state for the execution persistence port."""
        from pydantic import TypeAdapter

        plan = {
            "version": self.plan.version,
            "firstStep": self.plan.first_step,
            "items": {key: value.model_dump(mode="json") for key, value in self.plan.items.items()},
            "status": self.plan.status.copy(),
            "skips": self.plan.skips.copy(),
            "verified": self.plan.verified.copy(),
            "retired": sorted(self.plan.retired),
            "selected": self.plan.selected,
            "active": self.plan.active,
        }
        verifier = {
            "queries": [
                TypeAdapter(VerifiedQuery).dump_python(value, mode="json")
                for value in self.verifier.by_id.values()
            ],
            "missingData": self.missing_data,
        }
        budget = {
            key: getattr(self.budget, key)
            for key in (
                "steps",
                "tools",
                "tokens",
                "cost_microusd",
                "attempts",
                "unknown_attempts",
                "reason",
            )
        }
        return {"plan": plan, "verifier": verifier, "budget": budget}

    @staticmethod
    def parse(call: ToolCall) -> ParsedAction:
        value = parse_tool_arguments(call.function.arguments, 4096)
        return TODO_ACTION.validate_python(value)

    def apply(
        self, action: ParsedAction, step_index: int, *, counted: bool = False
    ) -> ActionOutcome:
        if not counted:
            self.budget.start_tool()
        todo_id = None if isinstance(action, ReplaceTodo) else action.todo_id
        if action.base_version != self.plan.version:
            return ActionOutcome(action.op, "stale_plan_version", todo_id=todo_id)
        try:
            if isinstance(action, ReplaceTodo):
                self.plan.replace(action, step_index)
            elif isinstance(action, SelectTodo):
                self.plan.ready(action.todo_id)
                self.plan.selected = action.todo_id
                self.plan.active = action.todo_id
            elif isinstance(action, SkipTodo):
                self.plan.skip(action)
            else:
                if action.query_id and action.query_id not in self.verifier.by_id:
                    raise TodoRejected("evidence_not_found")
                self.plan.complete(action)
                if action.query_id:
                    self.plan.verified[action.todo_id] = action.query_id
        except TodoRejected as error:
            return ActionOutcome(action.op, error.code, todo_id=todo_id)
        return ActionOutcome(
            action.op, "ok", ok=True, progress=isinstance(action, CompleteTodo), todo_id=todo_id
        )

    def admit_query(self, prepared: PreparedRetailCall) -> str | None:
        if prepared.safe_error is not None or prepared.params is None:
            raise TodoRejected(prepared.safe_error or "invalid_parameters")
        self.stop.require_query(query_key(prepared.params))
        return self.plan.selected

    def start_query(self, prepared: PreparedRetailCall, *, counted: bool = False) -> None:
        assert prepared.params is not None
        if not counted:
            self.budget.start_tool()
        self.stop.start_query(query_key(prepared.params))

    def observe_query(
        self,
        prepared: PreparedRetailCall,
        outcome: RetailQueryResult,
        step: str,
        todo_id: str | None,
    ) -> ActionOutcome:
        if todo_id is not None:
            self.plan.selected = None
        if outcome.error_code == "retail_query_timeout":
            raise AutonomyStopped("execution_timeout")
        try:
            evidence, fresh = self.verifier.observe(prepared, outcome, step)
        except VerificationFailure as error:
            if todo_id is not None:
                self.plan.status[todo_id] = "failed"
                self.plan.active = None
            return ActionOutcome("retail", error.code, failure=error, todo_id=todo_id)
        if (
            outcome.status == "empty"
            or not outcome.base_covered
            or (outcome.params.compare_to and not outcome.comparison_covered)
        ):
            self.missing_data = True
        completion = (
            {
                "op": "complete",
                "baseVersion": self.plan.version,
                "todoId": todo_id,
                "queryId": evidence.id,
            }
            if todo_id is not None and outcome.status == "ok"
            else None
        )
        return ActionOutcome(
            "retail",
            "query_verified",
            ok=True,
            progress=fresh,
            observation=retail_observation(
                outcome, query_id=evidence.id, todo_completion=completion
            ),
            todo_id=todo_id,
        )

    def observation(self, outcome: ActionOutcome) -> str:
        if outcome.observation is not None:
            return outcome.observation
        value = {
            "status": "ok" if outcome.ok else "error",
            "code": outcome.code,
            "planVersion": self.plan.version,
            "selectedTodoId": self.plan.selected,
            "items": [item.model_dump(by_alias=True) for item in self.plan.projection()],
            "queryIds": list(self.verifier.by_id),
        }
        if outcome.failure:
            value["verification"] = {
                "level": outcome.failure.level,
                "layer": outcome.failure.layer,
            }
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def action_event(self, step_id: str, index: int, outcome: ActionOutcome) -> StreamExtension:
        return extension(
            "action",
            RunAutonomyAction(
                stepId=step_id,
                actionIndex=index,
                action=outcome.kind,
                code=outcome.code,
                todoId=outcome.todo_id,
                level=outcome.failure.level if outcome.failure else None,
                layer=outcome.failure.layer if outcome.failure else None,
                planVersion=self.plan.version,
                selectedTodoId=self.plan.active,
                items=self.plan.projection(),
            ),
        )

    def result_event(self, result: RunResult, reason: str | None) -> StreamExtension:
        budget = self.budget.snapshot()
        status = result.status
        if result.status == "failed":
            reason = reason or result.error_code
            if reason in BUDGET_REASONS:
                status = "budget_exhausted"
            elif reason in HUMAN_REASONS:
                status = "human_required"
        elif result.status == "canceled":
            reason = result.reason
        else:
            reason = status
        return extension(
            "result",
            RunAutonomyResult(
                outcome=status,
                reason=reason,
                planVersion=self.plan.version,
                firstPlanStep=self.plan.first_step,
                budget=budget,
                partial=self.verifier.partial()
                if status in {"failed", "budget_exhausted", "human_required"}
                and reason != "verification_rejected"
                else [],
            ),
        )
