"""Fenced internal delivery and reconciliation through the Agent admission ledger."""

import asyncio
import json
from uuid import UUID

from sqlalchemy import select

from compass_agent.models.errors import ModelError
from compass_agent.scheduler.entities import AgentSchedule, AgentScheduleTrigger
from compass_agent.scheduler.lease import lock_role
from compass_agent.scheduler.repository import exhausted, verify_delivery
from compass_agent.scheduler.service import clear_delivery
from compass_agent.schemas import SessionSubmit, TenantScope, TriggerAdmission, TrustedContext

PERMANENT = {"schedule_permission_revoked", "schedule_delivery_rejected"}


class TriggerAdmissionService:
    def __init__(self, runtime):
        self.runtime = runtime

    def source(self, trigger_id):
        store = self.runtime.store
        with store.transaction() as tx:
            value = tx.execute(
                select(AgentScheduleTrigger, AgentSchedule)
                .join(AgentSchedule, AgentSchedule.id == AgentScheduleTrigger.schedule_id)
                .where(AgentScheduleTrigger.id == trigger_id)
            ).one_or_none()
            if value is None:
                raise ModelError("schedule_trigger_not_found", 404)
            trigger, schedule = value
            if not trigger.input_ciphertext:
                raise ModelError("scheduler_delivery_stale", 409)
            context = TrustedContext(
                actor_user_id=schedule.owner_user_id,
                scope=TenantScope(
                    kind="tenant", tenant_id=schedule.tenant_id, workspace_id=schedule.workspace_id
                ),
                purpose="chat",
            )
            payload = store.private.open(trigger.id, "schedule_trigger", trigger.input_ciphertext)
            return (
                context,
                SessionSubmit.model_validate_json(
                    json.dumps({"requestId": str(trigger.id), **payload})
                ),
                trigger.run_id,
            )

    async def submit(self, trigger_id, token):
        context, command, accepted = await asyncio.to_thread(self.source, trigger_id)
        environment = None
        if accepted is None:
            environment = await self.runtime.access.check(
                context, purpose="schedule_run", run_environment=True
            )
        return await asyncio.to_thread(
            self.apply, trigger_id, token, context, command, run_environment=environment
        )

    async def reconcile(self, trigger_id, token):
        context, command, _ = await asyncio.to_thread(self.source, trigger_id)
        return await asyncio.to_thread(
            self.apply, trigger_id, token, context, command, reconciliation=True
        )

    def apply(
        self, trigger_id, token, context, command, *, reconciliation=False, run_environment=None
    ):
        store = self.runtime.store
        assert store.scheduler is not None
        with store.transaction() as tx:
            # Use the role lock before the common admission and command locks.
            role = lock_role(tx)
            store.scheduler.admission(tx)
            previous = store.command(tx, context, trigger_id, store.submission_payload(command))
            trigger = tx.scalar(
                select(AgentScheduleTrigger)
                .where(AgentScheduleTrigger.id == trigger_id)
                .with_for_update()
            )
            if previous is not None:
                if previous.kind != "schedule_submit" or trigger.run_id != previous.run_id:
                    raise ModelError("request_conflict", 409)
                return TriggerAdmission(outcome="accepted", runId=previous.run_id)
            if trigger.run_id:
                raise ModelError("schedule_receipt_unavailable", 503)
            now = verify_delivery(
                tx, role, trigger, token, "reconcile_pending" if reconciliation else "dispatching"
            )
            if reconciliation:
                closed = exhausted(trigger, now) or trigger.reason_code in PERMANENT
                clear_delivery(trigger)
                if closed:
                    trigger.delivery_status = "delivery_failed"
                    return TriggerAdmission(outcome="closed")
                trigger.delivery_status, trigger.next_attempt_at = "retry_wait", now
                return TriggerAdmission(outcome="ready")
            if trigger.delivery_deadline_at <= now:
                raise ModelError("scheduler_delivery_stale", 409)
            receipt = store.submit_in_transaction(
                tx,
                command,
                context,
                run_environment=run_environment,
                schedule={"scheduleId": str(trigger.schedule_id), "triggerId": str(trigger_id)},
            )
            # Recheck the database clock after admission and initialization.
            accepted_at = verify_delivery(tx, role, trigger, token, "dispatching")
            if accepted_at >= trigger.delivery_deadline_at:
                raise ModelError("scheduler_delivery_stale", 409)
            clear_delivery(trigger)
            trigger.run_id = UUID(receipt["runId"])
            trigger.accepted_at = accepted_at
            trigger.delivery_status, trigger.reason_code = "submitted", None
            trigger.observed_run_status = "created"
            trigger.observed_at = accepted_at
            return TriggerAdmission(outcome="accepted", runId=trigger.run_id)
