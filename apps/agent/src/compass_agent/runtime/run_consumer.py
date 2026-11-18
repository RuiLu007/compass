"""Local execution slots drive the existing durable boundary receipts."""

import asyncio
import logging

from sqlalchemy.exc import SQLAlchemyError

from compass_agent.models.errors import ModelError
from compass_agent.runtime.run_scheduler import AdvanceCommand, ConsumerSlot
from compass_agent.schemas import TrustedContext

logger = logging.getLogger(__name__)


async def run_slot(runtime, slot, build_executor, stop):
    scheduler = runtime.store.scheduler
    assert scheduler is not None
    identity = ConsumerSlot(f"{runtime.boot_id}:{slot}", runtime.boot_id)
    while not stop.is_set():
        if runtime.store.ready:
            try:
                grant = await asyncio.to_thread(scheduler.claim, identity)
                if grant:
                    logger.info("run.claimed run_id=%s epoch=%s", grant.run_id, grant.epoch)
                    await consume(runtime, identity, grant, build_executor, stop)
                    continue
            except (SQLAlchemyError, ModelError):
                pass
            except Exception as error:
                logger.warning("run.consumer_failed exception=%s", type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.25)
        except TimeoutError:
            pass


async def consume(runtime, identity, grant, build_executor, stop):
    scheduler = runtime.store.scheduler
    assert scheduler is not None
    finished = asyncio.Event()

    async def monitor():
        due = asyncio.get_running_loop().time()
        while not finished.is_set():
            try:
                run = await asyncio.to_thread(runtime.store.read_run, grant.run_id)
                if run.execution_epoch != grant.epoch:
                    return
                control = runtime.controls.get(grant.run_id)
                if control and run.status == "cancelling":
                    control.request_stop("user")
                if asyncio.get_running_loop().time() >= due:
                    await asyncio.to_thread(scheduler.heartbeat, identity, grant)
                    due = asyncio.get_running_loop().time() + min(
                        3, runtime.settings.compass_runtime_lease_seconds / 3
                    )
            except ModelError:
                control = runtime.controls.get(grant.run_id)
                if control:
                    control.request_stop("shutdown")
                return
            except SQLAlchemyError:
                # The next boundary is fenced by the DB clock even if this poll was unavailable.
                pass
            try:
                await asyncio.wait_for(finished.wait(), timeout=0.25)
            except TimeoutError:
                pass

    heartbeat = asyncio.create_task(monitor())
    try:
        checkpoint = grant.checkpoint_no
        while not stop.is_set() and not heartbeat.done():
            run = await asyncio.to_thread(runtime.store.read_run, grant.run_id)
            if run.execution_epoch != grant.epoch:
                break
            if run.control_intent or run.status == "cancelling":
                break
            command = AdvanceCommand(grant=grant, expectedCheckpoint=checkpoint)
            ack, start = await asyncio.to_thread(scheduler.prepare_advance, identity, command)
            if start:
                context = TrustedContext.model_validate(run.config_snapshot["context"])
                budget = run.config_snapshot.get("budget")
                if budget is not None and budget.get("version") != 1:
                    await runtime.pause_interrupted(run.id, grant.epoch, "budget_policy_mismatch")
                    break
                executor = build_executor(budget)
                runtime.start_boundary(run.id, executor, context, grant.epoch)
            if ack.status == "continue":
                checkpoint = ack.checkpoint_no
            elif ack.status != "running":
                break
            task = runtime.tasks.get(run.id)
            if task and not task.done():
                await asyncio.wait((task,), timeout=0.25)
            else:
                await asyncio.sleep(0.05)
    except (ModelError, SQLAlchemyError):
        # A committed receipt or lease expiry decides recovery after an uncertain DB response.
        pass
    finally:
        task = runtime.tasks.get(grant.run_id)
        if task and not task.done():
            if stop.is_set():
                control = runtime.controls.get(grant.run_id)
                if control:
                    control.request_stop("shutdown")
            # A pause is settled at the boundary. A cross-replica cancel is signalled by monitor.
            await asyncio.wait((task,), timeout=8)
        finished.set()
        await asyncio.gather(heartbeat, return_exceptions=True)
        if task is None or task.done():
            try:
                await asyncio.to_thread(scheduler.release, identity, grant)
            except (ModelError, SQLAlchemyError):
                pass
