"""Separate platform diagnostics and current-member tenant usage authorization."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Response

from compass_agent.api.models import Context, require
from compass_agent.api.sessions import Runtime
from compass_agent.runtime.diagnostics import RuntimeDiagnostics
from compass_agent.schemas import RuntimeTimeline, RuntimeUsage, TenantScope

router = APIRouter(prefix="/v1/analysis", tags=["runtime-diagnostics"])


@router.get("/runs/{run_id}/timeline", response_model=RuntimeTimeline)
async def timeline(run_id: UUID, context: Context, runtime: Runtime, response: Response):
    require(context, {"runtime_diagnostics"}, "platform")
    await runtime.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(RuntimeDiagnostics(runtime.store).timeline, run_id)


@router.get("/runtime/usage", response_model=RuntimeUsage)
async def usage(context: Context, runtime: Runtime, response: Response):
    require(context, {"runtime_usage"}, "tenant")
    await runtime.access.check(context)
    assert isinstance(context.scope, TenantScope)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(RuntimeDiagnostics(runtime.store).usage, context.scope.tenant_id)
