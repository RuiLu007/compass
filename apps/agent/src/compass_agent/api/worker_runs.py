"""Private Worker identity and opaque advancement; business data stays in Agent."""

import asyncio
import hmac
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from compass_agent.api.models import get_model_service
from compass_agent.api.runs import get_agent_executor
from compass_agent.api.sessions import Runtime
from compass_agent.models.errors import ModelError
from compass_agent.models.service import ModelService
from compass_agent.runtime.run_scheduler import WorkerIdentity
from compass_agent.schemas import (
    ExecutionGrant,
    TrustedContext,
    WorkerAdvanceAck,
    WorkerAdvanceCommand,
    WorkerBoot,
    WorkerLeaseCommand,
)

router = APIRouter(prefix="/internal/runtime/jobs", tags=["worker-runtime"])


def worker_identity(request: Request, runtime: Runtime) -> str:
    value = json.loads(runtime.settings.compass_worker_credentials.get_secret_value())
    if not isinstance(value, dict):
        raise ModelError("runtime_unavailable", 503)
    authorization = request.headers.get("authorization", "").encode()
    for identifier, token in value.items():
        if (
            isinstance(identifier, str)
            and 0 < len(identifier) <= 64
            and isinstance(token, str)
            and len(token) >= 16
            and token != runtime.settings.compass_agent_service_token
            and hmac.compare_digest(authorization, ("Bearer " + token).encode())
        ):
            return identifier
    raise ModelError("unauthenticated", 401)


WorkerId = Annotated[str, Depends(worker_identity)]


async def execution_context(command: WorkerAdvanceCommand, runtime: Runtime):
    run = await asyncio.to_thread(runtime.store.read_run, command.grant.run_id)
    return TrustedContext.model_validate(run.config_snapshot["context"])


WorkerContext = Annotated[TrustedContext, Depends(execution_context)]


def get_worker_executor(
    request: Request,
    context: WorkerContext,
    models: Annotated[ModelService, Depends(get_model_service)],
):
    return get_agent_executor(request, context, models)


@router.post(
    "/claim", response_model=ExecutionGrant, responses={204: {"description": "No available job"}}
)
async def claim(worker: WorkerId, command: WorkerBoot, runtime: Runtime, response: Response):
    runtime.require_owner()
    scheduler = runtime.store.scheduler
    if scheduler is None:
        raise ModelError("runtime_unavailable", 503)
    grant = await asyncio.to_thread(scheduler.claim, WorkerIdentity(worker, command.boot_id))
    response.headers["Cache-Control"] = "no-store"
    if grant is None:
        return Response(status_code=204, headers={"Retry-After": "1", "Cache-Control": "no-store"})
    return grant


@router.post("/heartbeat", response_model=ExecutionGrant)
async def heartbeat(worker: WorkerId, command: WorkerLeaseCommand, runtime: Runtime):
    runtime.require_owner()
    assert runtime.store.scheduler is not None
    return await asyncio.to_thread(
        runtime.store.scheduler.heartbeat,
        WorkerIdentity(worker, command.grant.boot_id),
        command.grant,
    )


@router.post("/advance", response_model=WorkerAdvanceAck)
async def advance(
    worker: WorkerId,
    command: WorkerAdvanceCommand,
    runtime: Runtime,
    context: WorkerContext,
    executor: Annotated[object, Depends(get_worker_executor)],
):
    runtime.require_owner()
    assert runtime.store.scheduler is not None
    identity = WorkerIdentity(worker, command.grant.boot_id)
    ack, start = await asyncio.to_thread(runtime.store.scheduler.prepare_advance, identity, command)
    if start:
        try:
            await runtime.access.check(context)
            runtime.start_boundary(command.grant.run_id, executor, context, command.grant.epoch)
        except Exception:
            await asyncio.to_thread(
                runtime.store.scheduler.revoke,
                command.grant.run_id,
                "execution_start_failed",
                force=True,
            )
            raise ModelError("runtime_unavailable", 503) from None
    return ack


@router.post("/release", status_code=204)
async def release(worker: WorkerId, command: WorkerLeaseCommand, runtime: Runtime):
    runtime.require_owner()
    assert runtime.store.scheduler is not None
    await asyncio.to_thread(
        runtime.store.scheduler.release,
        WorkerIdentity(worker, command.grant.boot_id),
        command.grant,
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
