"""Agent-owned queue, short quota transactions and database-clock execution leases."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import (
    AgentCommand,
    AgentOutbox,
    AgentRun,
    AgentRunJob,
    AgentSession,
    AgentTenantSchedule,
)
from compass_agent.runtime.private_state import digest
from compass_agent.schemas import ExecutionGrant, SchedulingPublic, WorkerAdvanceAck

ACTIVE_JOBS = ("leased", "reclaiming")
TERMINAL = {"completed", "failed", "canceled"}
ADVANCE_NAMESPACE = uuid5(NAMESPACE_URL, "compass.runtime.worker.advance.v1")


@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str
    boot_id: UUID


class RunScheduler:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        raw = json.loads(settings.compass_runtime_tenant_overrides)
        if not isinstance(raw, dict):
            raise ValueError("Runtime tenant overrides must be an object")
        self.overrides = {}
        for tenant, limits in raw.items():
            UUID(tenant)
            if not isinstance(limits, dict) or set(limits) - {"active", "backlog"}:
                raise ValueError("Invalid runtime tenant limits")
            if any(type(v) is not int or not 1 <= v <= 10000 for v in limits.values()):
                raise ValueError("Invalid runtime tenant capacity")
            self.overrides[tenant] = limits
        self.owner_boot_id: UUID | None = None

    @staticmethod
    def admission(tx):
        tx.execute(text("SELECT pg_advisory_xact_lock(984001)"))

    def limits(self, tenant_id):
        return {
            "active": self.settings.compass_runtime_tenant_active,
            "backlog": self.settings.compass_runtime_tenant_backlog,
            **self.overrides.get(str(tenant_id), {}),
        }

    @staticmethod
    def clock(tx):
        return tx.scalar(select(func.clock_timestamp()))

    @staticmethod
    def job(tx, run_id):
        return tx.scalar(select(AgentRunJob).where(AgentRunJob.run_id == run_id).with_for_update())

    @staticmethod
    def count(tx, statuses, tenant_id=None):
        query = (
            select(func.count()).select_from(AgentRunJob).where(AgentRunJob.status.in_(statuses))
        )
        if tenant_id is not None:
            query = query.where(AgentRunJob.tenant_id == tenant_id)
        return tx.scalar(query) or 0

    def require_capacity(self, tx, tenant_id):
        pending = ("ready", "leased", "reclaiming", "parked")
        if (
            self.count(tx, pending) >= self.settings.compass_runtime_max_backlog
            or self.count(tx, pending, tenant_id) >= self.limits(tenant_id)["backlog"]
        ):
            raise ModelError("runtime_backpressure", 429)

    def enqueue(self, tx, session, run, *, initial=False):
        job = self.job(tx, run.id)
        if job is None:
            self.require_capacity(tx, session.tenant_id)
            if tx.get(AgentTenantSchedule, session.tenant_id) is None:
                tx.add(AgentTenantSchedule(tenant_id=session.tenant_id))
                tx.flush()
            job = AgentRunJob(
                run_id=run.id,
                tenant_id=session.tenant_id,
                status="ready",
                ready_at=self.clock(tx),
                claim_count=0,
            )
            tx.add(job)
        elif job.status != "parked":
            raise ModelError("state_conflict", 409)
        else:
            job.status, job.ready_at = "ready", self.clock(tx)
        run.execution_epoch, run.control_intent, run.reason_code = None, None, "queued"
        if not initial:
            run.status, run.recovery_status = "paused", "ready"
        return self.event(run, job, "queued")

    @staticmethod
    def event(run, job, reason):
        labels = {
            "queued": "任务已接收，等待执行。",
            "claimed": "任务开始执行。",
            "lease_expired": "执行连接中断，正在检查恢复位置。",
            "agent_restarted": "服务已恢复，正在检查执行记录。",
            "worker_released": "执行资源正在释放。",
            "recovered": "已恢复完成的步骤，等待继续执行。",
            "parked": "任务已保存，等待下一步操作。",
            "done": "本次执行已结束。",
            "cleanup_unconfirmed": "正在核对执行结果和资源状态。",
        }
        value = SchedulingPublic(
            status=job.status,
            reasonCode=reason,
            claimCount=job.claim_count,
            occurredAt=datetime.now(UTC),
            summary=labels.get(reason, "任务状态已更新。"),
        ).model_dump(mode="json", by_alias=True)
        ordinal = 1 + sum(
            v["kind"] == "timeline" for v in run.public_view.get("objects", {}).values()
        )
        return {
            "type": "view.upsert",
            "payload": {
                "objectId": "timeline:" + str(uuid4()),
                "revision": 1,
                "kind": "timeline",
                "value": {
                    "ordinal": ordinal,
                    "event": {
                        "type": "stream.extension",
                        "payload": {
                            "name": "compass.runtime.scheduling",
                            "version": 1,
                            "required": False,
                            "data": value,
                        },
                    },
                },
            },
        }

    @staticmethod
    def clear_lease(job):
        job.worker_id = job.worker_boot_id = job.lease_expires_at = None

    def grant(self, run, job):
        return ExecutionGrant(
            runId=run.id,
            workerId=job.worker_id,
            bootId=job.worker_boot_id,
            epoch=run.execution_epoch,
            expiresAt=job.lease_expires_at,
            checkpointNo=run.checkpoint_no or 0,
        )

    def require_execution(self, tx, run, epoch):
        job = self.job(tx, run.id)
        if (
            job is None
            or job.status != "leased"
            or run.execution_epoch != epoch
            or job.lease_expires_at <= self.clock(tx)
        ):
            raise ModelError("lease_lost", 409)
        return job

    def require_grant(self, tx, run, worker, grant):
        job = self.require_execution(tx, run, grant.epoch)
        if (
            run.id != grant.run_id
            or job.worker_id != worker.worker_id
            or job.worker_id != grant.worker_id
            or job.worker_boot_id != worker.boot_id
            or job.worker_boot_id != grant.boot_id
        ):
            raise ModelError("lease_lost", 409)
        return job

    def claim(self, worker):
        if not self.store.ready:
            raise ModelError("runtime_unavailable", 503)
        with self.store.transaction() as tx:
            self.admission(tx)
            previous = tx.scalar(
                select(AgentRunJob.run_id).where(
                    AgentRunJob.worker_id == worker.worker_id,
                    AgentRunJob.status.in_(ACTIVE_JOBS),
                )
            )
            if previous:
                _, run = self.store.lock(tx, previous)
                job = self.job(tx, run.id)
                if (
                    job.status == "leased"
                    and job.worker_boot_id == worker.boot_id
                    and job.lease_expires_at > self.clock(tx)
                    and run.execution_epoch
                ):
                    return self.grant(run, job)
                return None
            if self.count(tx, ACTIVE_JOBS) >= self.settings.compass_runtime_max_active:
                return None
            candidates = tx.execute(
                select(AgentRunJob.run_id, AgentRun.session_id, AgentRunJob.tenant_id)
                .join(AgentRun, AgentRun.id == AgentRunJob.run_id)
                .join(AgentTenantSchedule, AgentTenantSchedule.tenant_id == AgentRunJob.tenant_id)
                .where(
                    AgentRunJob.status == "ready", AgentRunJob.ready_at <= func.clock_timestamp()
                )
                .order_by(
                    AgentTenantSchedule.last_claimed_at.asc().nullsfirst(),
                    AgentRunJob.tenant_id,
                    AgentRunJob.ready_at,
                    AgentRunJob.run_id,
                )
            ).all()
            for run_id, session_id, tenant_id in candidates:
                if self.count(tx, ACTIVE_JOBS, tenant_id) >= self.limits(tenant_id)["active"]:
                    continue
                session = tx.scalar(
                    select(AgentSession)
                    .where(AgentSession.id == session_id)
                    .with_for_update(skip_locked=True)
                )
                if session is None:
                    continue
                run = tx.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update(skip_locked=True)
                )
                if run is None:
                    continue
                job = self.job(tx, run_id)
                if (
                    job.status != "ready"
                    or run.status not in {"created", "paused"}
                    or run.recovery_status == "blocked"
                    or run.control_intent
                    or run.cleanup_status == "unconfirmed"
                    or tx.scalar(
                        select(AgentOutbox.id)
                        .where(AgentOutbox.run_id == run_id, AgentOutbox.delivered_at.is_(None))
                        .limit(1)
                    )
                ):
                    continue
                clock = self.clock(tx)
                run.execution_epoch, run.owner_boot_id = uuid4(), self.owner_boot_id
                run.status, run.reason_code, run.recovery_status = "running", None, "ready"
                run.started_at = run.started_at or clock
                job.status, job.worker_id, job.worker_boot_id = (
                    "leased",
                    worker.worker_id,
                    worker.boot_id,
                )
                job.lease_expires_at = clock + timedelta(
                    seconds=self.settings.compass_runtime_lease_seconds
                )
                job.first_claimed_at = job.first_claimed_at or clock
                job.claim_count += 1
                tx.get(AgentTenantSchedule, tenant_id).last_claimed_at = clock
                self.store.outbox(tx, session, run, [self.event(run, job, "claimed")])
                return self.grant(run, job)
            return None

    def heartbeat(self, worker, grant):
        with self.store.transaction() as tx:
            _, run = self.store.lock(tx, grant.run_id)
            job = self.require_grant(tx, run, worker, grant)
            if run.status != "running":
                raise ModelError("lease_lost", 409)
            job.lease_expires_at = self.clock(tx) + timedelta(
                seconds=self.settings.compass_runtime_lease_seconds
            )
            return self.grant(run, job)

    @staticmethod
    def command_identity(command):
        grant = command.grant
        key = uuid5(
            ADVANCE_NAMESPACE, f"{grant.run_id}:{grant.epoch}:{command.expected_checkpoint}"
        )
        payload = {
            "runId": str(grant.run_id),
            "workerId": grant.worker_id,
            "bootId": str(grant.boot_id),
            "epoch": str(grant.epoch),
            "expectedCheckpoint": command.expected_checkpoint,
        }
        return key, digest(payload)

    def prepare_advance(self, worker, command):
        with self.store.transaction() as tx:
            session, run = self.store.lock(tx, command.grant.run_id)
            key, sha = self.command_identity(command)
            previous = tx.scalar(
                select(AgentCommand).where(
                    AgentCommand.run_id == run.id, AgentCommand.request_id == key
                )
            )
            if previous:
                if (
                    previous.kind != "worker_advance"
                    or previous.request_sha256 != sha
                    or previous.response["grant"]["workerId"] != worker.worker_id
                    or previous.response["grant"]["bootId"] != str(worker.boot_id)
                ):
                    raise ModelError("request_conflict", 409)
                if previous.response["state"] == "committed":
                    return WorkerAdvanceAck.model_validate(previous.response["ack"]), False
            if not self.store.ready:
                raise ModelError("runtime_unavailable", 503)
            self.require_grant(tx, run, worker, command.grant)
            if previous:
                if previous.response["state"] != "running":
                    raise ModelError("command_interrupted", 409)
                return WorkerAdvanceAck.model_validate(previous.response["ack"]), False
            if run.status != "running" or run.control_intent:
                raise ModelError("run_not_runnable", 409)
            if (run.checkpoint_no or 0) != command.expected_checkpoint:
                raise ModelError("checkpoint_conflict", 409)
            ack = WorkerAdvanceAck(
                commandId=key,
                status="running",
                checkpointNo=run.checkpoint_no or 0,
                reasonCode=None,
            )
            tx.add(
                AgentCommand(
                    id=uuid4(),
                    tenant_id=session.tenant_id,
                    workspace_id=session.workspace_id,
                    actor_user_id=session.owner_user_id,
                    request_id=key,
                    session_id=session.id,
                    run_id=run.id,
                    kind="worker_advance",
                    request_sha256=sha,
                    response={
                        "schemaVersion": 1,
                        "grant": command.grant.model_dump(mode="json", by_alias=True),
                        "expectedCheckpoint": command.expected_checkpoint,
                        "state": "running",
                        "ack": ack.model_dump(mode="json", by_alias=True),
                    },
                    created_at=self.clock(tx),
                )
            )
            return ack, True

    def acknowledge(self, tx, run, status, reason=None):
        rows = tx.scalars(
            select(AgentCommand).where(
                AgentCommand.run_id == run.id, AgentCommand.kind == "worker_advance"
            )
        ).all()
        for row in rows:
            if row.response["state"] != "running":
                continue
            response = dict(row.response)
            response["state"] = "interrupted" if status == "interrupted" else "committed"
            response["ack"] = WorkerAdvanceAck(
                commandId=row.request_id,
                status=status,
                checkpointNo=run.checkpoint_no or 0,
                reasonCode=reason,
            ).model_dump(mode="json", by_alias=True)
            row.response = response

    def accepted_boundary(self, tx, run, step):
        if step.parent_step_id is None:
            self.acknowledge(tx, run, "continue")
            return True
        return False

    def synchronize(self, tx, session, run, events):
        """Close scheduling and advance receipts in the same transaction as the Run boundary."""
        job = self.job(tx, run.id)
        if job is None:
            return events
        if job.status not in ACTIVE_JOBS:
            if run.status in TERMINAL and job.status != "done":
                job.status = "done"
                self.clear_lease(job)
                return [*events, self.event(run, job, "done")]
            return events
        if run.status in TERMINAL or run.status in {"waiting", "paused"}:
            if run.cleanup_status == "unconfirmed":
                job.status = "reclaiming"
                self.acknowledge(tx, run, "interrupted", "cleanup_unconfirmed")
                return [*events, self.event(run, job, "cleanup_unconfirmed")]
            # Keep the slot until the supervisor drains the old task.
            if job.status == "reclaiming":
                return events
            job.status = "done" if run.status in TERMINAL else "parked"
            self.clear_lease(job)
            run.execution_epoch = None
            self.acknowledge(tx, run, job.status)
            return [*events, self.event(run, job, job.status)]
        return events

    def revoke(self, run_id, reason, *, force=False):
        with self.store.transaction() as tx:
            session, run = self.store.lock(tx, run_id)
            job = self.job(tx, run_id)
            if job is None or job.status not in ACTIVE_JOBS:
                return False
            if job.status == "reclaiming":
                return True
            if not force and job.lease_expires_at > self.clock(tx):
                return False
            job.status = "reclaiming"
            run.execution_epoch, run.recovery_status, run.reason_code = None, "required", reason
            self.acknowledge(tx, run, "interrupted", reason)
            self.store.outbox(tx, session, run, [self.event(run, job, reason)])
            return True

    def release(self, worker, grant):
        with self.store.transaction() as tx:
            session, run = self.store.lock(tx, grant.run_id)
            job = self.require_grant(tx, run, worker, grant)
            job.status = "reclaiming"
            run.execution_epoch, run.reason_code = None, "worker_released"
            self.acknowledge(tx, run, "interrupted", "worker_released")
            self.store.outbox(tx, session, run, [self.event(run, job, "worker_released")])

    def reclaim_candidates(self, *, boot=False):
        with Session(self.store.engine) as tx:
            query = (
                select(AgentRunJob.run_id)
                .join(AgentRun, AgentRun.id == AgentRunJob.run_id)
                .where(AgentRunJob.status.in_(ACTIVE_JOBS))
            )
            if not boot:
                query = query.where(
                    (AgentRunJob.status == "reclaiming")
                    | (AgentRunJob.lease_expires_at <= func.clock_timestamp())
                )
            return list(tx.scalars(query))

    def settle_reclaimed(self, run_id, *, recovery_error=None):
        from compass_agent.runtime.entities import (
            AgentAction,
            AgentAttempt,
            AgentDemoEffect,
            AgentMessage,
            AgentStep,
            AgentWait,
        )
        from compass_agent.runtime.store import message_snapshot, settled_nodes
        from compass_agent.tools.sandbox.entities import SandboxAllocation

        with self.store.transaction() as tx:
            session, run = self.store.lock(tx, run_id)
            job = self.job(tx, run_id)
            if job is None or job.status != "reclaiming":
                return
            committed = tx.execute(
                select(AgentAction, AgentDemoEffect)
                .join(AgentDemoEffect, AgentDemoEffect.action_id == AgentAction.id)
                .where(AgentAction.run_id == run_id)
            ).first()
            if committed:
                action, receipt = committed
                if action.proposal_sha256 != receipt.proposal_sha256:
                    recovery_error = "effect_conflict"
                else:
                    action.state, action.receipt = (
                        "applied",
                        {
                            "actionId": str(action.id),
                            "state": "applied",
                            "deltaCent": str(receipt.delta_cent),
                        },
                    )
                    run.cleanup_status, run.recovery_status = "confirmed", "ready"
                    for wait in tx.scalars(
                        select(AgentWait).where(
                            AgentWait.run_id == run_id,
                            AgentWait.kind == "reconciliation",
                            AgentWait.status == "pending",
                        )
                    ):
                        wait.status, wait.decided_at = "canceled", self.clock(tx)
            allocation = tx.scalar(
                select(SandboxAllocation.id)
                .where(
                    SandboxAllocation.run_id == run_id,
                    SandboxAllocation.state.not_in(("released", "retained")),
                )
                .limit(1)
            )
            unknown = tx.scalar(
                select(AgentAttempt.id)
                .join(AgentStep, AgentStep.id == AgentAttempt.step_id)
                .where(
                    AgentAttempt.run_id == run_id,
                    AgentAttempt.status.in_(("dispatched", "unknown", "incomplete")),
                    AgentStep.kind != "workflow_host",
                )
                .limit(1)
            )
            effect = tx.scalar(
                select(AgentAction.id)
                .where(AgentAction.run_id == run_id, AgentAction.state == "unknown")
                .limit(1)
            )
            unknown_tool = tx.scalar(
                select(AgentAttempt.id)
                .join(AgentStep, AgentStep.id == AgentAttempt.step_id)
                .where(
                    AgentAttempt.run_id == run_id,
                    AgentAttempt.status.in_(("dispatched", "unknown", "incomplete")),
                    AgentStep.kind.in_(("tool", "mcp", "sandbox")),
                )
                .limit(1)
            )
            if allocation or effect or unknown_tool or run.cleanup_status == "unconfirmed":
                if run.recovery_status != "blocked" or run.reason_code != "cleanup_unconfirmed":
                    run.recovery_status, run.reason_code = "blocked", "cleanup_unconfirmed"
                    self.store.outbox(
                        tx, session, run, [self.event(run, job, "cleanup_unconfirmed")]
                    )
                return
            events = []
            wait = tx.scalar(
                select(AgentWait.id)
                .where(AgentWait.run_id == run_id, AgentWait.status == "pending")
                .limit(1)
            )
            if run.status == "cancelling" and not unknown:
                run.status, run.finished_at, run.cleanup_status = (
                    "canceled",
                    self.clock(tx),
                    "confirmed",
                )
                run.reason_code, run.recovery_status = "user_canceled", "ready"
                job.status = "done"
                self.clear_lease(job)
                events.extend(settled_nodes(run, "canceled"))
                assistant = tx.scalar(
                    select(AgentMessage).where(
                        AgentMessage.run_id == run_id,
                        AgentMessage.channel == "conversation",
                        AgentMessage.role == "assistant",
                    )
                )
                value = message_snapshot(assistant)
                value["revision"] += 1
                value["status"] = "incomplete"
                events.append({"type": "message.upsert", "payload": value})
            elif unknown or recovery_error or run.recovery_status == "blocked":
                run.status = "paused" if run.status != "cancelling" else "cancelling"
                run.recovery_status, run.reason_code = (
                    "blocked",
                    recovery_error or "execution_unknown",
                )
                job.status = "parked"
            elif run.control_intent == "pause" or wait or run.status in TERMINAL:
                job.status = "done" if run.status in TERMINAL else "parked"
                if run.control_intent == "pause":
                    run.status, run.reason_code = "paused", "user_paused"
                    run.cleanup_status, run.recovery_status = "confirmed", "ready"
                elif wait:
                    run.status, run.cleanup_status = "waiting", "confirmed"
            else:
                job.status, job.ready_at = "ready", self.clock(tx)
                run.status, run.reason_code, run.recovery_status = "paused", "queued", "ready"
                run.cleanup_status = "confirmed"
            self.clear_lease(job)
            self.acknowledge(tx, run, "interrupted", "lease_lost")
            reason = "recovered" if job.status == "ready" else job.status
            self.store.outbox(tx, session, run, [*events, self.event(run, job, reason)])
