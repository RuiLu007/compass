"""Authorized schedule management and paginated, owner-scoped history."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from compass_agent.api.models import Context, Service, require
from compass_agent.api.sessions import Runtime
from compass_agent.scheduler.service import ScheduleService
from compass_agent.schemas import (
    ScheduleCreate,
    ScheduleDelete,
    SchedulePage,
    SchedulePreview,
    SchedulePreviewResult,
    ScheduleStatus,
    ScheduleUpdate,
    ScheduleView,
    ScheduleWriteReceipt,
    TriggerPage,
    TrustedContext,
)

router = APIRouter(prefix="/v1/analysis/schedules", tags=["schedules"])


def schedule_context(context: Context):
    require(context, {"schedule_manage"}, "tenant")
    return context


def schedule_service(runtime: Runtime, models: Service):
    return ScheduleService(runtime.store, runtime.access, models, runtime.settings)


ScheduleContext = Annotated[TrustedContext, Depends(schedule_context)]
Schedules = Annotated[ScheduleService, Depends(schedule_service)]


@router.get("", response_model=SchedulePage)
async def list_schedules(
    context: ScheduleContext,
    service: Schedules,
    q: Annotated[str | None, Query(max_length=120)] = None,
    status: ScheduleStatus | None = None,
    cursor: UUID | None = None,
):
    return await service.list(context, q, status, cursor)


@router.post("", status_code=201, response_model=ScheduleWriteReceipt)
async def create_schedule(command: ScheduleCreate, context: ScheduleContext, service: Schedules):
    return await service.write(context, None, command)


@router.post("/preview", response_model=SchedulePreviewResult)
async def preview_schedule(command: SchedulePreview, context: ScheduleContext, service: Schedules):
    return await service.preview(context, command)


@router.get("/{schedule_id}", response_model=ScheduleView)
async def get_schedule(schedule_id: UUID, context: ScheduleContext, service: Schedules):
    return await service.get(context, schedule_id)


@router.patch("/{schedule_id}", response_model=ScheduleWriteReceipt)
async def update_schedule(
    schedule_id: UUID, command: ScheduleUpdate, context: ScheduleContext, service: Schedules
):
    return await service.write(context, schedule_id, command)


@router.delete("/{schedule_id}", response_model=ScheduleWriteReceipt)
async def delete_schedule(
    schedule_id: UUID, command: ScheduleDelete, context: ScheduleContext, service: Schedules
):
    return await service.write(context, schedule_id, command)


@router.get("/{schedule_id}/triggers", response_model=TriggerPage)
async def schedule_triggers(
    schedule_id: UUID, context: ScheduleContext, service: Schedules, cursor: UUID | None = None
):
    return await service.history(context, schedule_id, cursor)
