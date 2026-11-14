"""Private workspace connection management for the Web BFF."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from compass_agent.api.models import Context, require
from compass_agent.data_connections.connections import (
    DataConnections,
    probe_connection,
    probe_data_connection,
)
from compass_agent.database import get_engine
from compass_agent.schemas import (
    DataConnectionList,
    DataConnectionSave,
    DataConnectionView,
    ModelErrorResponse,
    RetailConnectionSave,
    RetailConnectionStatus,
    RetailConnectionTest,
    RetailConnectionView,
    TenantScope,
)
from compass_agent.settings import get_settings

router = APIRouter(
    prefix="/v1/data",
    tags=["data-connections"],
    responses={status: {"model": ModelErrorResponse} for status in (401, 403, 404, 409, 422, 503)},
)


def managed_scope(context: Context) -> TenantScope:
    require(context, {"data_manage"}, "tenant")
    assert isinstance(context.scope, TenantScope)
    return context.scope


def retail_connections() -> DataConnections:
    return DataConnections(get_engine(), get_settings())


Scope = Annotated[TenantScope, Depends(managed_scope)]
Connections = Annotated[DataConnections, Depends(retail_connections)]


@router.get("/retail-connection", response_model=RetailConnectionStatus)
def retail_connection_status(scope: Scope, service: Connections) -> RetailConnectionStatus:
    return service.status(scope)


@router.patch("/retail-connection", response_model=RetailConnectionView)
def save_retail_connection(
    scope: Scope, service: Connections, command: RetailConnectionSave
) -> RetailConnectionView:
    return service.save(scope, command)


@router.post("/retail-connection/test", response_model=RetailConnectionTest)
async def test_retail_connection(scope: Scope, service: Connections) -> RetailConnectionTest:
    revision, binding = await run_in_threadpool(service.test_target, scope)
    return await probe_connection(revision, binding)


@router.get("/connections", response_model=DataConnectionList)
def list_data_connections(scope: Scope, service: Connections) -> DataConnectionList:
    return service.list(scope)


@router.post("/connections", response_model=DataConnectionView, status_code=201)
def create_data_connection(
    scope: Scope, service: Connections, command: DataConnectionSave
) -> DataConnectionView:
    return service.save_connection(scope, command, None)


@router.patch("/connections/{connection_id}", response_model=DataConnectionView)
def update_data_connection(
    connection_id: UUID, scope: Scope, service: Connections, command: DataConnectionSave
) -> DataConnectionView:
    return service.save_connection(scope, command, connection_id)


@router.post("/connections/{connection_id}/test", response_model=RetailConnectionTest)
async def test_data_connection(
    connection_id: UUID, scope: Scope, service: Connections
) -> RetailConnectionTest:
    connection, password = await run_in_threadpool(
        service.test_connection_target, scope, connection_id
    )
    return await probe_data_connection(scope, connection, password)
