"""Test-only real HTTP scheduler and independently committed effect process.

Model selection is a fixture. Scheduling, leases, journals, PostgreSQL, Redis and
the two HTTP-only Worker processes use the production implementations.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic
from types import SimpleNamespace
from uuid import UUID

import httpx
from compass_agent.api import runtime_diagnostics, sessions, worker_runs
from compass_agent.api.runs import get_agent_executor
from compass_agent.execution_context import current_execution
from compass_agent.models.errors import ModelError
from compass_agent.runtime.control import RunRegistry
from compass_agent.runtime.durable_service import DurableRuntime
from compass_agent.runtime.entities import AgentRunJob
from compass_agent.schemas import AnalysisFileNone, RunCompleted, WorkerAdvanceCommand
from compass_agent.settings import Settings
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def audit(kind, **fields):
    path = os.environ["COMPASS_PROCESS_AUDIT"]
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(
            descriptor,
            (
                json.dumps(
                    {
                        "kind": kind,
                        "pid": os.getpid(),
                        "monotonic": monotonic(),
                        "time": datetime.now(UTC).isoformat(),
                        **fields,
                    }
                )
                + "\n"
            ).encode(),
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ProcessBoundaryRunner:
    def __init__(self):
        self.models = SimpleNamespace()

    async def run(self, command, context, control, emit):
        journal = current_execution.get()
        assert journal is not None

        async def calculate():
            audit("physical", runId=str(journal.run_id), step="sum", state="started")
            await asyncio.sleep(1)
            result = {"sum": sum(range(20))}
            audit("physical", runId=str(journal.run_id), step="sum", state="completed")
            return result

        first = await journal.call("tool", "local-sum", {}, calculate, dict)

        if command.question.startswith("process:agent-restart"):
            with Session(journal.store.engine) as tx:
                job = tx.get(AgentRunJob, journal.run_id)
                assert job is not None
                first_claim = job.claim_count == 1
            if first_claim:
                # The new advance receipt is running, while the only physical
                # operation has already committed its checkpoint in the prior advance.
                audit("agent_restart_boundary_held", runId=str(journal.run_id))
                await asyncio.Event().wait()

        async def finish():
            audit("physical", runId=str(journal.run_id), step="double", state="started")
            await asyncio.sleep(0.4)
            result = {"total": first["sum"] * 2}
            audit("physical", runId=str(journal.run_id), step="double", state="completed")
            return result

        async def unknown_effect():
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.post(
                    os.environ["COMPASS_PROCESS_EFFECT_URL"] + f"/effect/{journal.run_id}"
                )
                response.raise_for_status()
            audit("effect_response_lost", runId=str(journal.run_id))
            # The remote process committed its independent effect. Keep the caller
            # without a result until actual lease reclamation cancels this operation.
            await asyncio.Event().wait()

        if command.question.startswith("process:unknown"):
            await journal.call("tool", "remote-effect", {}, unknown_effect, dict)
            raise AssertionError("Unknown external outcome must not become a successful answer")
        result = await journal.call("tool", "local-double", {}, finish, dict)
        return RunCompleted(
            status="completed",
            finalAnswer=f"本轮计算结果为 {result['total']}。",
            file=AnalysisFileNone(status="none"),
        )


@asynccontextmanager
async def lifespan(application):
    engine = create_engine(os.environ["DATABASE_URL"])
    runtime = DurableRuntime(engine, Settings(), RunRegistry(), schedule_jobs=True)

    async def allowed(context, **kwargs):
        return None

    async def visibility(context):
        return {"details": True}

    runtime.access.check = allowed
    runtime.access.visibility = visibility
    application.state.durable_runtime = runtime
    scheduler = runtime.store.scheduler
    assert scheduler is not None
    claim, heartbeat, revoke, release = (
        scheduler.claim,
        scheduler.heartbeat,
        scheduler.revoke,
        scheduler.release,
    )

    def observed_claim(worker):
        value = claim(worker)
        if value:
            audit("claim", grant=value.model_dump(mode="json", by_alias=True))
        else:
            audit("idle_claim", workerId=worker.worker_id, bootId=str(worker.boot_id))
        return value

    def observed_heartbeat(worker, grant):
        value = heartbeat(worker, grant)
        audit("heartbeat", grant=value.model_dump(mode="json", by_alias=True))
        return value

    def observed_revoke(run_id, reason, **kwargs):
        value = revoke(run_id, reason, **kwargs)
        run = runtime.store.read_run(run_id)
        audit(
            "revoked",
            runId=str(run_id),
            reason=reason,
            epoch=str(run.execution_epoch) if run.execution_epoch else None,
        )
        return value

    def observed_release(worker, grant):
        value = release(worker, grant)
        run = runtime.store.read_run(grant.run_id)
        audit(
            "released",
            grant=grant.model_dump(mode="json", by_alias=True),
            epoch=str(run.execution_epoch) if run.execution_epoch else None,
        )
        return value

    scheduler.claim, scheduler.heartbeat = observed_claim, observed_heartbeat
    scheduler.revoke = observed_revoke
    scheduler.release = observed_release
    await runtime.start()
    try:
        yield
    finally:
        await runtime.close()
        engine.dispose()


app = FastAPI(lifespan=lifespan)
app.include_router(sessions.router)
app.include_router(worker_runs.router)
app.include_router(runtime_diagnostics.router)
app.dependency_overrides[get_agent_executor] = ProcessBoundaryRunner


async def process_executor(command: WorkerAdvanceCommand, request: Request):
    runtime = request.app.state.durable_runtime
    run = await asyncio.to_thread(runtime.store.read_run, command.grant.run_id)
    if (
        run.config_snapshot["command"]["question"].startswith("process:recover")
        and command.expected_checkpoint == 1
    ):
        observed = False
        while True:
            with Session(runtime.store.engine) as tx:
                job = tx.get(AgentRunJob, run.id)
                assert job is not None
                if job.claim_count > 1 or job.status == "parked":
                    break
            if not observed:
                audit("checkpoint_boundary_held", runId=str(run.id), epoch=str(command.grant.epoch))
                observed = True
            await asyncio.sleep(0.05)
    return ProcessBoundaryRunner()


app.dependency_overrides[worker_runs.get_worker_executor] = process_executor


@app.exception_handler(ModelError)
async def errors(request: Request, error: ModelError):
    return JSONResponse({"code": error.code}, status_code=error.status)


@app.get("/healthz")
async def ready(request: Request):
    return JSONResponse(
        {"ready": request.app.state.durable_runtime.store.ready},
        status_code=200 if request.app.state.durable_runtime.store.ready else 503,
    )


effect_app = FastAPI()


@effect_app.get("/healthz")
async def effect_health():
    return {"ready": True}


@effect_app.post("/effect/{run_id}")
async def effect(run_id: UUID):
    audit("external_effect_committed", runId=str(run_id))
    return {"committed": True}
