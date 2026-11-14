"""Optimistic, idempotent Run controls and Session context boundaries."""

from sqlalchemy import select

from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import (
    AgentAttempt,
    AgentMessage,
    AgentRun,
    AgentSession,
    AgentStep,
    AgentWait,
)
from compass_agent.runtime.hitl import approval_event
from compass_agent.runtime.store import TERMINAL, message_snapshot, now, owner_filter, settled_nodes


def control(store, run_id, kind, command, context):
    payload = {"kind": kind, "runId": str(run_id), **command.model_dump(mode="json", by_alias=True)}
    with store.transaction(admission=kind == "resume") as tx:
        old = store.command(tx, context, command.request_id, payload)
        session, run = store.lock(tx, run_id, context)
        if old:
            return old.response
        if run.version != command.expected_version:
            raise ModelError("version_conflict", 409, run.version)
        if run.status in TERMINAL:
            raise ModelError("run_terminal", 409)
        events = []
        if kind == "pause":
            if run.status != "running":
                raise ModelError("state_conflict", 409)
            run.control_intent = "pause"
        elif kind == "resume":
            if run.status == "waiting":
                raise ModelError("decision_required", 409)
            legacy_created = (
                store.scheduler is not None
                and run.status == "created"
                and store.scheduler.job(tx, run_id) is None
            )
            if (
                (run.status != "paused" and not legacy_created)
                or run.recovery_status == "blocked"
                or run.cleanup_status == "unconfirmed"
            ):
                raise ModelError("resume_blocked", 409)
            if not store.ready:
                raise ModelError("runtime_unavailable", 503)
            run.control_intent, run.reason_code = None, None
            if store.scheduler:
                events.append(store.scheduler.enqueue(tx, session, run))
        elif kind == "cancel":
            run.status, run.control_intent = "cancelling", None
            unknown = tx.scalar(
                select(AgentAttempt.id)
                .where(
                    AgentAttempt.run_id == run.id,
                    AgentAttempt.status.in_(["dispatched", "unknown", "incomplete"]),
                )
                .limit(1)
            )
            if unknown and run.execution_epoch is None:
                run.cleanup_status, run.reason_code = "unconfirmed", "cleanup_unconfirmed"
            pending = tx.scalars(
                select(AgentWait).where(AgentWait.run_id == run.id, AgentWait.status == "pending")
            ).all()
            for wait in pending:
                wait.status, wait.decided_at = "canceled", now()
                events.append(approval_event(wait, reason="user_canceled"))
            if run.execution_epoch is None and run.cleanup_status != "unconfirmed":
                run.status, run.finished_at, run.cleanup_status = "canceled", now(), "confirmed"
                for step in tx.scalars(
                    select(AgentStep).where(
                        AgentStep.run_id == run.id,
                        AgentStep.status.in_(["waiting", "running"]),
                    )
                ):
                    step.status, step.finished_at = "canceled", now()
                    step.revision += 1
                events.extend(settled_nodes(run, "canceled"))
                message = tx.scalars(
                    select(AgentMessage).where(
                        AgentMessage.run_id == run.id,
                        AgentMessage.role == "assistant",
                        AgentMessage.channel == "conversation",
                    )
                ).one()
                value = message_snapshot(message)
                value["revision"] += 1
                value["status"] = "incomplete"
                events.append({"type": "message.upsert", "payload": value})
        else:
            raise ModelError("validation_failed", 422)
        if store.scheduler and kind == "cancel":
            job = store.scheduler.job(tx, run.id)
            if job and job.status in {"leased", "reclaiming"}:
                job.status, run.execution_epoch = "reclaiming", None
                store.scheduler.acknowledge(tx, run, "interrupted", "user_canceled")
        store.outbox(tx, session, run, events)
        result = {
            "sessionId": str(session.id),
            "runId": str(run.id),
            "version": run.version,
            "status": run.status,
            "code": None,
        }
        store.record_command(
            tx, context, command.request_id, kind, payload, session.id, run.id, result
        )
        return result


def context_reset(store, session_id, command, context):
    payload = {
        "kind": "reset",
        "sessionId": str(session_id),
        **command.model_dump(mode="json", by_alias=True),
    }
    with store.transaction() as tx:
        old = store.command(tx, context, command.request_id, payload)
        session = tx.scalar(
            select(AgentSession)
            .where(AgentSession.id == session_id, *owner_filter(context))
            .with_for_update()
        )
        if session is None:
            raise ModelError("not_found", 404)
        if old:
            return old.response
        if session.version != command.expected_version:
            raise ModelError("version_conflict", 409)
        last = tx.scalar(
            select(AgentRun)
            .where(AgentRun.session_id == session.id)
            .order_by(AgentRun.ordinal.desc())
            .limit(1)
        )
        if last and last.status not in TERMINAL:
            raise ModelError("session_busy", 409)
        session.context_start_ordinal = last.ordinal + 1 if last else 1
        session.version += 1
        session.updated_at = now()
        result = {
            "sessionId": str(session.id),
            "runId": None,
            "version": session.version,
            "status": "reset",
            "code": None,
        }
        store.record_command(
            tx, context, command.request_id, "reset", payload, session.id, None, result
        )
        return result
