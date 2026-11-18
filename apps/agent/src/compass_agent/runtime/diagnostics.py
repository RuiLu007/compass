"""Read-only operational DTOs; no prompts, tool content or upstream credentials."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from compass_agent.models.entities import ModelCall
from compass_agent.runtime.entities import (
    AgentAttempt,
    AgentCommand,
    AgentOutbox,
    AgentRunJob,
    AgentStep,
    ModelAttempt,
)
from compass_agent.schemas import RuntimeTimeline, RuntimeTimelineEntry, RuntimeUsage


class RuntimeDiagnostics:
    def __init__(self, store):
        self.store = store

    def timeline(self, run_id):
        run = self.store.read_run(run_id)
        schedule = run.config_snapshot.get("schedule", {})
        entries = []
        with Session(self.store.engine) as tx:
            for box in tx.scalars(
                select(AgentOutbox)
                .where(AgentOutbox.run_id == run_id)
                .order_by(AgentOutbox.run_version)
                .limit(2048)
            ):
                for packet in box.payload["packets"]:
                    event = packet["event"]
                    if event["type"] != "view.upsert" or event["payload"]["kind"] != "timeline":
                        continue
                    progress = event["payload"]["value"]["event"]
                    if (
                        progress["type"] == "stream.extension"
                        and progress["payload"]["name"] == "compass.runtime.scheduling"
                    ):
                        data = progress["payload"]["data"]
                        entries.append(
                            RuntimeTimelineEntry(
                                id=packet["packetId"],
                                kind="scheduling",
                                status=data["status"],
                                occurredAt=box.created_at,
                                reasonCode=data["reasonCode"],
                            )
                        )
            for step in tx.scalars(select(AgentStep).where(AgentStep.run_id == run_id)):
                entries.append(
                    RuntimeTimelineEntry(
                        id=str(step.id),
                        kind="step",
                        status=step.status,
                        occurredAt=step.created_at,
                        stepId=step.id,
                    )
                )
            for attempt in tx.scalars(select(AgentAttempt).where(AgentAttempt.run_id == run_id)):
                entries.append(
                    RuntimeTimelineEntry(
                        id=str(attempt.id),
                        kind="attempt",
                        status=attempt.status,
                        occurredAt=attempt.dispatched_at or attempt.created_at,
                        stepId=attempt.step_id,
                        attemptId=attempt.id,
                        epoch=attempt.execution_epoch,
                        reasonCode=attempt.error_code,
                    )
                )
            for call, attempt in tx.execute(
                select(ModelCall, ModelAttempt)
                .join(ModelAttempt, ModelAttempt.call_id == ModelCall.call_id)
                .where(ModelCall.run_id == run_id)
            ):
                entries.append(
                    RuntimeTimelineEntry(
                        id=str(attempt.id),
                        kind="model",
                        status=attempt.status,
                        occurredAt=attempt.dispatched_at or attempt.created_at,
                        attemptId=call.agent_attempt_id,
                        modelCallId=call.call_id,
                        reasonCode=attempt.error_code,
                    )
                )
            for command in tx.scalars(select(AgentCommand).where(AgentCommand.run_id == run_id)):
                grant = command.response.get("grant", {})
                entries.append(
                    RuntimeTimelineEntry(
                        id=str(command.id),
                        kind="command",
                        status=command.kind,
                        occurredAt=command.created_at,
                        workerId=grant.get("workerId"),
                        epoch=grant.get("epoch"),
                        reasonCode=command.response.get("ack", {}).get("status"),
                    )
                )
        entries.sort(key=lambda item: (item.occurred_at, item.id))
        return RuntimeTimeline(
            runId=run_id,
            scheduleId=schedule.get("scheduleId"),
            triggerId=schedule.get("triggerId"),
            entries=entries[:4096],
        )

    def usage(self, tenant_id):
        scheduler = self.store.scheduler
        assert scheduler is not None
        with Session(self.store.engine) as tx:
            jobs = {name: 0 for name in ("ready", "leased", "reclaiming", "parked", "done")}
            jobs.update(
                tx.execute(
                    select(AgentRunJob.status, func.count())
                    .where(AgentRunJob.tenant_id == tenant_id)
                    .group_by(AgentRunJob.status)
                ).all()
            )
            attempts = tx.scalars(
                select(ModelAttempt)
                .join(ModelCall, ModelCall.call_id == ModelAttempt.call_id)
                .where(ModelCall.tenant_id == tenant_id, ModelAttempt.origin == "live")
            ).all()
            settled_tokens = reserved_tokens = reserved_cost = unknown = settled_cost = 0
            unpriced = False
            for attempt in attempts:
                usage = attempt.usage or {}
                measured = (
                    usage.get("input_tokens") is not None and usage.get("output_tokens") is not None
                )
                uncertain = (
                    attempt.status in {"prepared", "dispatched", "unknown", "incomplete"}
                    or attempt.outcome_unknown
                    or not attempt.cleanup_confirmed
                    or not measured
                )
                if uncertain:
                    unknown += 1
                    reserved_tokens += attempt.reserved_tokens
                    reserved_cost += attempt.reserved_cost_microusd
                else:
                    settled_tokens += usage["input_tokens"] + max(
                        usage["output_tokens"], usage.get("reasoning_tokens") or 0
                    )
                    if attempt.actual_cost_microusd is None:
                        unpriced = True
                    else:
                        settled_cost += attempt.actual_cost_microusd
            return RuntimeUsage(
                tenantId=tenant_id,
                limits=scheduler.limits(tenant_id),
                jobs=jobs,
                backlog=sum(v for k, v in jobs.items() if k != "done"),
                occupied=jobs["leased"] + jobs["reclaiming"],
                settledTokens=settled_tokens,
                settledCostMicrousd=None if unpriced else settled_cost,
                unknownAttempts=unknown,
                reservedTokens=reserved_tokens,
                reservedCostMicrousd=reserved_cost,
            )
