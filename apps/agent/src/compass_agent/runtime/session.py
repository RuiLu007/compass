"""Run-local budget and generic task progress, independent of business data."""

from compass_agent.harness.autonomy import BUDGET_REASONS, AutonomyBudget, StopPolicy
from compass_agent.schemas import (
    RunAutonomyAction,
    RunAutonomyResult,
    RunExtensionPayload,
    RunTodoItem,
    StreamExtension,
)
from compass_agent.tools.builtin.todo_write.plan import ExecutionPlan, TodoDefinition


def extension(name, data):
    return StreamExtension(
        type="stream.extension",
        payload=RunExtensionPayload(
            name=f"compass.autonomy.{name}",
            version=1,
            required=False,
            data=data.model_dump(mode="json", by_alias=True),
        ),
    )


class RuntimeSession:
    def __init__(self, profiles, limits, *, require_plan=False):
        self.budget = AutonomyBudget(profiles, limits)
        self.stop = StopPolicy()
        self.plan = ExecutionPlan[TodoDefinition]()
        self.require_plan = require_plan

    def checkpoint_state(self):
        return {
            "verifier": {},
            "plan": {
                **self.plan.snapshot(),
                "retired": sorted(self.plan.retired),
                "verified": dict(self.plan.verified),
                "skips": dict(self.plan.skips),
            },
            "budget": {
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
            },
        }

    def action_event(self, step_id, index, action, code, todo_id=None):
        return extension(
            "action",
            RunAutonomyAction(
                stepId=step_id,
                actionIndex=index,
                action=action,
                code=code,
                todoId=todo_id,
                planVersion=self.plan.version,
                selectedTodoId=self.plan.active,
                items=[
                    RunTodoItem(
                        id=item.id,
                        goal=item.goal,
                        status=self.plan.status[item.id],
                        dependsOn=item.depends_on,
                        optional=item.optional,
                        reason=self.plan.skips.get(item.id),
                    )
                    for item in self.plan.items.values()
                ],
            ),
        )

    def result_event(self, result, reason):
        status = result.status
        if status == "failed":
            reason = reason or result.error_code
            if reason in BUDGET_REASONS:
                status = "budget_exhausted"
        elif status == "canceled":
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
                budget=self.budget.snapshot(),
                partial=[],
            ),
        )
