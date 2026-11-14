"""Read-only preflight for retiring fixed-case execution contracts."""

import json

from compass_agent.database import get_engine
from compass_agent.runtime.entities import AgentRun, AgentToolBinding, AgentWait
from sqlalchemy import select
from sqlalchemy.orm import Session

RETIRED = frozenset(
    {
        "retail_sales",
        "load_weekly_review",
        "build_weekly_review",
        "find_workflows",
        "run_dynamic_workflow",
        "reuse_workflow",
    }
)


def affected_runs(engine):
    with Session(engine) as tx:
        active = tx.scalars(
            select(AgentRun).where(AgentRun.status.not_in(("completed", "failed", "canceled")))
        ).all()
        return [
            {"runId": str(run.id), "status": run.status}
            for run in active
            if (
                run.config_snapshot.get("command", {}).get("showcase")
                or any(
                    wait.expectation.get("continuation") == "apply_budget"
                    for wait in tx.scalars(select(AgentWait).where(AgentWait.run_id == run.id))
                )
                or set(
                    tx.scalars(
                        select(AgentToolBinding.alias).where(AgentToolBinding.run_id == run.id)
                    )
                ).intersection(RETIRED)
            )
        ]


def main():
    pending = affected_runs(get_engine())
    print(json.dumps({"ready": not pending, "retiredExecutionRuns": pending}, ensure_ascii=False))
    raise SystemExit(1 if pending else 0)


if __name__ == "__main__":
    main()
