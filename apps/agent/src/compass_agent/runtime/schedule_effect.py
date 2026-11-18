"""Commit a confirmed same-database effect and its accepted Journal boundary."""

import asyncio
from dataclasses import asdict

from sqlalchemy.exc import SQLAlchemyError

from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import AgentAction, AgentAttempt, AgentWait
from compass_agent.runtime.journal import BoundaryYield, RecoveryStop
from compass_agent.runtime.private_state import digest
from compass_agent.tools.contracts import ExecutionReceipt, ToolRejected


def require_grant(tx, journal, session, run, attempt, action, execution, grant):
    journal.store.require_epoch(run, journal.epoch)
    if run.config_snapshot.get("schedule"):
        raise ModelError("schedule_write_disabled", 403)
    if run.status != "running" or run.control_intent:
        raise ModelError("run_stopped", 409)
    if (
        action is None
        or action.run_id != run.id
        or action.state != "pending"
        or attempt is None
        or attempt.status != "prepared"
        or attempt.execution_epoch != journal.epoch
    ):
        raise RecoveryStop("action_conflict")
    wait = tx.get(AgentWait, action.wait_id)
    if (
        wait is None
        or wait.status != "approved"
        or wait.decider_user_id != session.owner_user_id
        or wait.decider_user_id != execution.trusted.actor_user_id
        or wait.proposal_sha256 != action.proposal_sha256
        or wait.decided_at is None
        or wait.decided_at >= wait.expires_at
        or grant["owner"] != [str(v) for v in execution.owner]
    ):
        raise RecoveryStop("action_conflict")
    snapshot = journal.store.private.open(run.id, "proposal", wait.private_proposal)
    if digest(snapshot) != digest({k: v for k, v in grant.items() if k != "actionId"}):
        raise RecoveryStop("action_conflict")


def apply(service, journal, execution, grant, schedule_id, command, step_id, attempt_id, state):
    context = execution.trusted
    with service.store.transaction() as tx:
        old = service.previous(tx, context, schedule_id, command)
        session, run = service.store.lock(tx, journal.run_id, context)
        if old:
            raise RecoveryStop("schedule_receipt_conflict")
        service.store.require_epoch(run, journal.epoch)
        attempt = tx.get(AgentAttempt, attempt_id, with_for_update=True)
        action = tx.get(AgentAction, command.request_id, with_for_update=True)
        written = service.apply(
            tx,
            context,
            schedule_id,
            command,
            before_effect=lambda: require_grant(
                tx, journal, session, run, attempt, action, execution, grant
            ),
        )
        receipt = asdict(
            ExecutionReceipt(
                execution.call_id,
                "ok",
                "confirmed",
                written.model_dump(mode="json", by_alias=True),
            )
        )
        yield_boundary = journal.accept_in_transaction(
            tx,
            session,
            run,
            step_id,
            attempt_id,
            {"result": receipt, "continuation": None, "budget": {}},
            business_state=state,
            boundary=True,
            expected_attempt_status="prepared",
        )
        return receipt, yield_boundary


def verify_receipt(service, journal, context, schedule_id, command, attempt_id, saved):
    with service.store.transaction() as tx:
        old = service.previous(tx, context, schedule_id, command)
        action = tx.get(AgentAction, command.request_id)
        result = saved["result"]
        if (
            old is None
            or action is None
            or action.run_id != journal.run_id
            or action.state != "applied"
            or action.receipt.get("attemptId") != str(attempt_id)
            or action.receipt.get("outputDigest") != digest(result)
            or old.response != result["payload"]
        ):
            raise RecoveryStop("schedule_receipt_conflict")
        return ExecutionReceipt(**result)


async def execute_effect(service, journal, execution, grant, schedule_id, command, *, key):
    try:
        inputs = {
            "tool": "schedule_write",
            "args": grant["args"],
            "owner": grant["owner"],
            "actionId": grant["actionId"],
        }
        step, attempt, saved = await asyncio.to_thread(
            journal.prepare, "tool", key, inputs, {"environment": "in_process"}
        )
        if saved is not None:
            return await asyncio.to_thread(
                verify_receipt,
                service,
                journal,
                execution.trusted,
                schedule_id,
                command,
                attempt,
                saved,
            )
        await journal.gate()
        if journal.fault:
            await journal.fault("schedule_effect_before_transaction", journal.run_id)
        await service.write_access(execution.trusted, command, approval=True)
        task = asyncio.create_task(
            asyncio.to_thread(
                apply,
                service,
                journal,
                execution,
                grant,
                schedule_id,
                command,
                step,
                attempt,
                journal.business_state(),
            )
        )
        try:
            receipt, boundary = await asyncio.shield(task)
        except asyncio.CancelledError:
            # The same database owns both the effect and its receipt, even on an interrupted caller.
            await task
            raise
        if journal.fault:
            await journal.fault("schedule_effect_committed", journal.run_id)
        if boundary:
            raise BoundaryYield()
        return ExecutionReceipt(**receipt)
    except SQLAlchemyError:
        raise RecoveryStop("schedule_effect_interrupted") from None
    except ModelError as error:
        if error.status == 503:
            raise RecoveryStop(error.code) from None
        raise ToolRejected(error.code) from None
