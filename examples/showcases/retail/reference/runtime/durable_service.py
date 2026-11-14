"""Single-owner durable tasks; a browser connection only subscribes to committed state."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from threading import Lock
from uuid import UUID, uuid4, uuid5

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from compass_agent.execution_context import current_execution
from compass_agent.models.errors import ModelError
from compass_agent.observability.observer import Observation, Observer
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.runtime.entities import AgentRun
from compass_agent.runtime.hitl import HumanDecisions
from compass_agent.runtime.journal import BoundaryYield, ExecutionJournal, RecoveryStop
from compass_agent.runtime.message_reader import MessageReader
from compass_agent.runtime.observation import _log_progress, _log_terminal, _run_log
from compass_agent.runtime.private_state import digest
from compass_agent.runtime.redis_stream import OutboxPublisher, RedisStream
from compass_agent.runtime.store import TERMINAL, RuntimeStore
from compass_agent.schemas import TrustedContext


class CurrentAccess:
    def __init__(self, settings):
        self.url, self.token = settings.compass_web_authz_url, settings.compass_agent_service_token

    async def check(self, context, *, approval=False):
        await self.authorize(context, approval=approval)

    async def visibility(self, context):
        value = await self.authorize(context, visibility=True)
        assert value is not None
        return value

    async def authorize(self, context, *, approval=False, visibility=False):
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.post(
                    self.url,
                    headers={"Authorization": "Bearer " + self.token},
                    json={
                        "context": context.model_dump(mode="json"),
                        "approval": approval,
                        **({"visibility": True} if visibility else {}),
                    },
                )
                if response.status_code in {403, 404}:
                    raise ModelError("forbidden", 403)
                if response.status_code != 200 or response.json().get("allowed") is not True:
                    raise ModelError("authorization_unavailable", 503)
                if visibility:
                    value = response.json().get("visibility")
                    if (
                        not isinstance(value, dict)
                        or type(value.get("details")) is not bool
                        or not isinstance(value.get("revision"), str)
                    ):
                        raise ModelError("authorization_unavailable", 503)
                    return value
        except (httpx.HTTPError, ValueError):
            raise ModelError("authorization_unavailable", 503) from None


def stable_progress(value):
    if isinstance(value, dict):
        return {
            k: stable_progress(v)
            for k, v in value.items()
            if k not in {"startedAtMs", "endedAtMs", "elapsedMs", "durationMs"}
        }
    if isinstance(value, list):
        return [stable_progress(v) for v in value]
    return value


class ProgressSink:
    def __init__(self, service, run_id, epoch):
        self.service, self.run_id, self.epoch = service, run_id, epoch
        self.pending = []
        self.known = deepcopy(service.store.read_run(run_id).public_view["objects"])
        self.lock = asyncio.Lock()

    def __call__(self, event):
        control = self.service.controls.get(self.run_id)
        if control:
            _log_progress(control, event)
        data = event.model_dump(mode="json", by_alias=True)
        if (
            data["type"] == "stream.extension"
            and data["payload"]["name"] == "compass.execution.node"
        ):
            value = data["payload"]["data"]
            identifier = value["id"]
            previous = self.known.get(identifier)
            if previous and previous["revision"] >= value["revision"]:
                return
            if previous:
                value["startedAtMs"] = previous["value"]["startedAtMs"]
                prior_detail = previous["value"].get("detail")
                if prior_detail:
                    value["detail"] = {
                        **{key: field for key, field in prior_detail.items() if field is not None},
                        **(value.get("detail") or {}),
                    }
                if value.get("endedAtMs") is not None:
                    value["endedAtMs"] = max(value["endedAtMs"], value["startedAtMs"])
            object_value = {
                "objectId": identifier,
                "revision": value["revision"],
                "kind": "execution.node",
                "value": value,
            }
        else:
            identifier = "timeline:" + digest(stable_progress(data))
            if identifier in self.known:
                return
            ordinal = sum(o["kind"] == "timeline" for o in self.known.values()) + 1
            object_value = {
                "objectId": identifier,
                "revision": 1,
                "kind": "timeline",
                "value": {"ordinal": ordinal, "event": data},
            }
        self.known[identifier] = object_value
        if len(self.pending) >= 2048:
            raise ModelError("run_content_limit", 409)
        self.pending.append({"type": "view.upsert", "payload": object_value})

    async def flush(self):
        async with self.lock:
            if self.pending:
                events, self.pending = self.pending, []
                try:
                    await asyncio.to_thread(
                        self.service.store.progress, self.run_id, self.epoch, events
                    )
                except BaseException:
                    self.pending = events + self.pending
                    raise
            await self.service.publisher.flush(self.run_id)


class DurableRuntime:
    def __init__(self, engine, settings, registry: RunRegistry, *, schedule_jobs=False):
        self.settings, self.registry = settings, registry
        self.store = RuntimeStore(engine, settings.compass_runtime_state_key.get_secret_value())
        self.streams = RedisStream(
            settings.compass_redis_url, settings.compass_redis_password.get_secret_value()
        )
        self.publisher = OutboxPublisher(self.store, self.streams)
        self.publisher.require_owner = self.require_owner
        self.reader, self.hitl = MessageReader(self.store), HumanDecisions(self.store)
        self.access = CurrentAccess(settings)
        self.boot_id = uuid4()
        if schedule_jobs:
            from compass_agent.runtime.run_scheduler import RunScheduler

            self.store.scheduler = RunScheduler(self.store, settings)
            self.store.scheduler.owner_boot_id = self.boot_id
        self.owner = None
        self.tasks: dict[UUID, asyncio.Task] = {}
        self.pending_starts = {}
        self.controls = {}
        self.sinks: dict[UUID, ProgressSink] = {}
        self.closing = False
        self.ticker = None
        self.progress_ticker = None
        self.owner_healthy = False
        self.fault: Callable[[str, UUID], Awaitable[None]] | None = None
        self.recovered = False
        self.observer: Observer | None = None
        self.observations: dict[UUID, Observation] = {}
        self.observation_lock = Lock()

    def begin_observation(self, run_id, context, executor, carrier=None, *, execution=False):
        with self.observation_lock:
            with self.store.transaction() as tx:
                _, run = self.store.lock(tx, run_id)
                saved = run.config_snapshot.get("observability", {})
                if run.status in TERMINAL or (run.status != "created" and not execution):
                    return saved.get("headers", {})
                root = self.observations.get(run_id)
                if root is None and self.observer and self.observer.tracer:
                    root = self.observer.root(
                        run_id,
                        context,
                        saved.get("carrier") or carrier,
                        capture_content=False,
                    )
                    self.observations[run_id] = root
                    run.config_snapshot = {
                        **run.config_snapshot,
                        "observability": {"headers": root.headers(), "carrier": root.carrier()},
                    }
                if root:
                    executor.models.root_observation = root
                    return root.headers()
                return saved.get("headers", {})

    def require_owner(self):
        if not self.owner_healthy or self.closing:
            raise ModelError("runtime_owner_unavailable", 503)

    def acquire_owner(self):
        connection = self.store.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        if not connection.scalar(text("SELECT pg_try_advisory_lock(985001)")):
            connection.close()
            raise ModelError("runtime_owner_unavailable", 503)
        self.owner = connection
        self.owner_healthy = True

    async def recover(self):
        with Session(self.store.engine) as tx:
            runs = tx.scalars(select(AgentRun)).all()
        for run in runs:
            if run.recovery_status == "blocked":
                continue
            try:
                await self.streams.validate(run)
                await self.publisher.flush(run.id)
            except ModelError as error:
                if error.code == "history_reload_required" and run.status in TERMINAL:
                    continue
                if error.code in {"stream_data_lost", "stream_batch_invalid"}:
                    await asyncio.to_thread(self.store.blocked, run.id, error.code)
                    continue
                raise
        if self.store.scheduler:
            await self.recover_scheduled(boot=True)
            self.recovered = True
            return
        await asyncio.to_thread(self.store.recover_boot, self.boot_id)
        # A committed classroom effect is final. Reconcile its receipt without dispatching
        # work or requiring a permission that may have been revoked after the commit.
        for previous in runs:
            run = await asyncio.to_thread(self.store.read_run, previous.id)
            if run.status != "paused" or run.recovery_status == "blocked":
                continue
            receipt = await asyncio.to_thread(self.hitl.lookup_demo, run.id)
            if receipt:
                epoch = await asyncio.to_thread(self.store.claim, run.id, self.boot_id)
                answer = await asyncio.to_thread(self.hitl.record_effect, run.id, epoch, receipt)
                await asyncio.to_thread(
                    self.store.finish, run.id, epoch, "completed", answer=answer
                )
        self.recovered = True

    async def start(self):
        if not self.store.private.key:
            return
        try:
            self.store.private.seal(self.boot_id, "readiness", {})
            await asyncio.to_thread(self.acquire_owner)
            await self.streams.ready()
            await self.recover()
            await asyncio.to_thread(self.hitl.expire)
            await self.publisher.tick()
            self.store.ready = True
        except Exception:
            self.store.ready = False
        self.ticker = asyncio.create_task(self.maintain())
        self.progress_ticker = asyncio.create_task(self.publish_progress())

    async def publish_progress(self):
        while not self.closing:
            try:
                if self.owner_healthy:
                    for sink in tuple(self.sinks.values()):
                        if sink.pending:
                            await sink.flush()
            except Exception:
                # Keep pending events for retry; maintain() owns readiness and owner recovery.
                pass
            await asyncio.sleep(0.05)

    async def maintain(self):
        while not self.closing:
            try:
                if self.owner is not None and self.owner.invalidated:
                    self.owner_healthy = False
                    for control in tuple(self.controls.values()):
                        control.request_stop("shutdown")
                    await asyncio.to_thread(self.owner.close)
                    self.owner = None
                    self.recovered = False
                if self.owner is None:
                    await asyncio.to_thread(self.acquire_owner)
                assert self.owner is not None
                await asyncio.to_thread(self.owner.execute, text("SELECT 1"))
                self.owner_healthy = True
                await self.streams.ready()
                if not self.recovered:
                    await self.recover()
                if self.store.scheduler:
                    await self.recover_scheduled()
                await asyncio.to_thread(self.hitl.expire)
                await self.publisher.tick()
                self.store.ready = True
            except Exception:
                self.store.ready = False
                self.owner_healthy = False
                if self.owner is None or self.owner.invalidated:
                    self.owner_healthy = False
                    for control in tuple(self.controls.values()):
                        control.request_stop("shutdown")
            await asyncio.sleep(0.5)

    async def recover_scheduled(self, *, boot=False):
        scheduler = self.store.scheduler
        assert scheduler is not None
        candidates = await asyncio.to_thread(scheduler.reclaim_candidates, boot=boot)
        # Pause is fulfilled at a confirmed boundary, without interrupting an in-flight call.
        from compass_agent.runtime.entities import AgentRunJob

        with Session(self.store.engine) as tx:
            pauses = tx.scalars(
                select(AgentRun.id)
                .join(AgentRunJob, AgentRunJob.run_id == AgentRun.id)
                .where(
                    AgentRunJob.status == "leased",
                    AgentRun.control_intent == "pause",
                )
            ).all()
        candidates.extend(
            rid
            for rid in pauses
            if rid not in candidates and (rid not in self.tasks or self.tasks[rid].done())
        )
        for run_id in candidates:
            previous = await asyncio.to_thread(self.store.read_run, run_id)
            reason = (
                "agent_restarted"
                if boot
                else "user_paused"
                if previous.control_intent == "pause"
                else "lease_expired"
            )
            await asyncio.to_thread(
                scheduler.revoke, run_id, reason, force=boot or run_id in pauses
            )
            task = self.tasks.get(run_id)
            if task and not task.done():
                control = self.controls.get(run_id)
                if control:
                    control.request_stop("shutdown")
                task.cancel()
                # Drain old callbacks before admitting a new execution epoch.
                done, _ = await asyncio.wait((task,), timeout=0.1)
                if not done:
                    continue
            run = await asyncio.to_thread(self.store.read_run, run_id)
            error = None
            effect = await asyncio.to_thread(self.hitl.lookup_demo, run_id)
            if run.status != "cancelling" and not effect:
                try:
                    await self.access.check(
                        TrustedContext.model_validate(run.config_snapshot["context"])
                    )
                    journal = ExecutionJournal(self.store, self.publisher, run_id, None)
                    await asyncio.to_thread(journal.validate_checkpoint)
                except (ModelError, RecoveryStop) as failure:
                    error = failure.code if isinstance(failure, ModelError) else failure.reason
            await asyncio.to_thread(scheduler.settle_reclaimed, run_id, recovery_error=error)

    def schedule(self, run_id, executor, context):
        if self.store.scheduler:
            return
        self.start_boundary(run_id, executor, context)

    def start_boundary(self, run_id, executor, context, epoch=None):
        if self.store.scheduler and epoch is None:
            epoch = self.store.read_run(run_id).execution_epoch
        previous = self.tasks.get(run_id)
        if previous and not previous.done():
            self.pending_starts[run_id] = (executor, context, epoch)
            return
        for identifier, task in tuple(self.tasks.items()):
            if len(self.tasks) < 128:
                break
            if task.done():
                self.tasks.pop(identifier, None)
        task = asyncio.create_task(self.execute(run_id, executor, context, expected_epoch=epoch))
        self.tasks[run_id] = task

        def settled(_):
            pending = self.pending_starts.pop(run_id, None)
            if pending and not self.closing:
                self.start_boundary(run_id, *pending)

        task.add_done_callback(settled)

    async def execute(self, run_id, executor, context, *, expected_epoch=None):
        epoch = None
        run = None
        control = None
        token = None
        active_base = 0
        try:
            run = await asyncio.to_thread(self.store.read_run, run_id)
            if self.store.scheduler and (
                expected_epoch is None or run.execution_epoch != expected_epoch
            ):
                return
            active_base = run.active_ms
            context = TrustedContext.model_validate(run.config_snapshot["context"])
            existing_effect = await asyncio.to_thread(self.hitl.lookup_demo, run_id)
            if run.status in TERMINAL:
                return
            await asyncio.to_thread(
                self.begin_observation, run_id, context, executor, execution=True
            )
            if existing_effect is None:
                await self.access.check(context)
            self.require_owner()
            await self.publisher.flush(run_id)
            if (
                run.status in {"running", "cancelling"}
                and run.execution_epoch
                and run.owner_boot_id == self.boot_id
            ):
                epoch = run.execution_epoch
            else:
                if self.store.scheduler:
                    raise ModelError("lease_lost", 409)
                epoch = await asyncio.to_thread(self.store.claim, run_id, self.boot_id)
            await self.publisher.flush(run_id)
            if run.status == "cancelling":
                if existing_effect:
                    await asyncio.to_thread(self.hitl.record_effect, run_id, epoch, existing_effect)
                await asyncio.to_thread(self.store.finish, run_id, epoch, "canceled")
                await self.publisher.flush(run_id)
                return
            if existing_effect is not None:
                answer = await asyncio.to_thread(
                    self.hitl.record_effect, run_id, epoch, existing_effect
                )
                await asyncio.to_thread(
                    self.store.finish, run_id, epoch, "completed", answer=answer
                )
                await self.publisher.flush(run_id)
                return
            journal = ExecutionJournal(
                self.store, self.publisher, run_id, epoch, access=lambda: self.access.check(context)
            )
            journal.fault = self.fault
            if hasattr(executor.models, "repository"):
                journal.model_secrets = executor.models.repository.secrets
            await asyncio.to_thread(journal.validate_checkpoint)
            sink = ProgressSink(self, run_id, epoch)
            journal.flush_progress = sink.flush
            self.sinks[run_id] = sink
            token = current_execution.set(journal)
            control = self.registry.register(
                OwnerKey.from_context(context),
                run_id=run_id,
                lifetime_s=max(
                    0, run.config_snapshot.get("timeoutSeconds", 180) - active_base / 1000
                ),
            )
            self.controls[run_id] = control
            _run_log("analysis.run.started", control, status="running")
            approved = await asyncio.to_thread(self.hitl.approved, run_id)
            journal.approved_proposals = [
                {
                    **self.store.private.open(run_id, "proposal", wait.private_proposal),
                    "actionId": str(uuid5(wait.id, f"action:{wait.proposal_revision}")),
                }
                for wait in await asyncio.to_thread(self.hitl.approvals, run_id)
            ]
            if approved:
                approver = context.model_copy(update={"actor_user_id": approved[1].decider_user_id})
                await self.access.check(approver, approval=True)
                journal.approved_proposal = self.store.private.open(
                    run_id, "proposal", approved[1].private_proposal
                )
            if approved and approved[1].expectation["continuation"] == "apply_budget":
                action, wait = approved
                receipt = await asyncio.to_thread(self.hitl.apply_demo, run_id, epoch, action.id)
                if self.fault:
                    await self.fault("effect_committed", run_id)
                answer = await asyncio.to_thread(self.hitl.record_effect, run_id, epoch, receipt)
                await asyncio.to_thread(
                    self.store.finish, run_id, epoch, "completed", answer=answer
                )
            else:
                command = await asyncio.to_thread(self.store.history_command, run_id)
                result = await executor.run(command, context, control, sink)
                _log_terminal(control, result)
                if result.status == "completed" and result.file.status == "ready":
                    import base64
                    from hashlib import sha256

                    from compass_agent.tools.contracts import ArtifactRef

                    content = base64.b64decode(result.file.content_base64, validate=True)
                    ref = ArtifactRef(
                        str(uuid5(run_id, "csv:" + result.file.tool_call_id)),
                        result.file.filename,
                        "text/csv",
                        run_id,
                        UUID(result.file.step_id),
                        result.file.tool_call_id,
                        len(content),
                        sha256(content).hexdigest(),
                    )
                    await asyncio.to_thread(journal.artifact, ref, content)
                await sink.flush()
                if result.status == "waiting":
                    await asyncio.to_thread(
                        self.hitl.wait,
                        run_id,
                        epoch,
                        result,
                        getattr(journal, "pending_proposal", None),
                    )
                elif result.status == "cleanup_unconfirmed":
                    await asyncio.to_thread(
                        self.store.finish,
                        run_id,
                        epoch,
                        "cancelling",
                        reason="cleanup_unconfirmed",
                        cleanup="unconfirmed",
                    )
                    await asyncio.to_thread(self.hitl.require_reconciliation, run_id)
                elif control.stop_reason == "shutdown":
                    await self.pause_interrupted(run_id, epoch, "process_interrupted")
                else:
                    await asyncio.to_thread(
                        self.store.finish,
                        run_id,
                        epoch,
                        result.status,
                        answer=getattr(result, "final_answer", None),
                        reason=getattr(result, "error_code", None),
                        drafts=journal.pending_drafts,
                    )
            await self.publisher.flush(run_id)
        except BoundaryYield:
            pass
        except RecoveryStop as stop:
            if epoch:
                await self.pause_interrupted(run_id, epoch, stop.reason)
        except (Exception, asyncio.CancelledError) as error:
            if epoch:
                await self.pause_interrupted(
                    run_id, epoch, getattr(error, "code", "execution_interrupted")
                )
            elif run is not None:
                await self.pause_before_dispatch(
                    run_id, run.version, getattr(error, "code", "execution_interrupted")
                )
        finally:
            if control and epoch:
                await asyncio.to_thread(
                    self.store.record_active_time,
                    run_id,
                    epoch,
                    active_base + control.duration_ms,
                )
            if token is not None:
                current_execution.reset(token)
            self.controls.pop(run_id, None)
            self.sinks.pop(run_id, None)
            if control and run_id in self.registry.active:
                self.registry.active.pop(run_id, None)
                control.finished.set()
            root = self.observations.pop(run_id, None)
            if root:
                try:
                    settled = await asyncio.to_thread(self.store.read_run, run_id)
                    root.complete(settled.status, settled.reason_code)
                finally:
                    root.end()

    async def pause_before_dispatch(self, run_id, version, reason):
        # Admission may have committed just before Redis or current authorization fails.
        # Retain the accepted command and expose an explicit recovery boundary.
        def commit():
            with self.store.transaction() as tx:
                session, run = self.store.lock(tx, run_id)
                if (
                    run.version != version
                    or run.status not in {"created", "running", "paused"}
                    or run.owner_boot_id not in {None, self.boot_id}
                ):
                    return
                run.status, run.execution_epoch = "paused", None
                run.recovery_status, run.reason_code = "required", reason
                self.store.outbox(tx, session, run, [])

        await asyncio.to_thread(commit)
        try:
            await self.publisher.flush(run_id)
        except Exception:
            pass

    async def pause_interrupted(self, run_id, epoch, reason):
        def commit():
            with self.store.transaction() as tx:
                session, run = self.store.lock(tx, run_id)
                if run.execution_epoch != epoch or run.status in TERMINAL:
                    return
                if run.status != "cancelling":
                    run.status = "paused"
                if self.store.scheduler:
                    job = self.store.scheduler.job(tx, run_id)
                    if job and job.status == "leased":
                        job.status = "reclaiming"
                        self.store.scheduler.acknowledge(tx, run, "interrupted", reason)
                run.execution_epoch = None
                run.recovery_status = "required"
                run.reason_code = reason
                self.store.outbox(tx, session, run, [])

        await asyncio.to_thread(commit)
        if reason in {"execution_unknown", "cleanup_unconfirmed"}:
            await asyncio.to_thread(self.hitl.require_reconciliation, run_id)
        try:
            await self.publisher.flush(run_id)
        except Exception:
            pass

    async def close(self):
        self.closing = True
        self.store.ready = False
        tickers = [task for task in (self.ticker, self.progress_ticker) if task]
        for task in tickers:
            task.cancel()
        await asyncio.gather(*tickers, return_exceptions=True)
        for control in tuple(self.controls.values()):
            control.request_stop("shutdown")
        if self.tasks:
            done, pending = await asyncio.wait(self.tasks.values(), timeout=8)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        await self.streams.close()
        if self.owner is not None:
            if not self.owner.invalidated:
                await asyncio.to_thread(
                    self.owner.execute, text("SELECT pg_advisory_unlock(985001)")
                )
            await asyncio.to_thread(self.owner.close)
            self.owner = None
        self.owner_healthy = False
