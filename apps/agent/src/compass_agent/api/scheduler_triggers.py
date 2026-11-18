"""Dedicated Scheduler identity; requests contain no user identity or Run input."""

import hmac
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from compass_agent.api.sessions import Runtime
from compass_agent.models.errors import ModelError
from compass_agent.scheduler.admission import TriggerAdmissionService
from compass_agent.schemas import TriggerAdmission, TriggerDelivery

router = APIRouter(prefix="/internal/scheduler/triggers", tags=["scheduler-delivery"])


def delivery_service(runtime: Runtime, authorization: Annotated[str | None, Header()] = None):
    token = runtime.settings.compass_scheduler_token.get_secret_value()
    if (
        len(token) < 16
        or token == runtime.settings.compass_agent_service_token
        or not authorization
        or not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode())
    ):
        raise ModelError("unauthenticated", 401)
    return TriggerAdmissionService(runtime)


Delivery = Annotated[TriggerAdmissionService, Depends(delivery_service)]


@router.post("/{trigger_id}/submit", status_code=202, response_model=TriggerAdmission)
async def submit_trigger(trigger_id: UUID, command: TriggerDelivery, service: Delivery):
    return await service.submit(trigger_id, command.delivery_token)


@router.post("/{trigger_id}/reconcile", response_model=TriggerAdmission)
async def reconcile_trigger(trigger_id: UUID, command: TriggerDelivery, service: Delivery):
    return await service.reconcile(trigger_id, command.delivery_token)
