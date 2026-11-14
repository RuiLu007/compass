"""Personal MCP configuration, isolated by the trusted BFF actor and workspace."""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from compass_agent.api.models import Context, require
from compass_agent.database import get_engine
from compass_agent.models.errors import ModelError
from compass_agent.schemas import (
    McpConnectionDelete,
    McpConnectionList,
    McpConnectionSave,
    McpConnectionView,
    McpDiscoveredTool,
    McpDiscoveryResult,
    ModelErrorResponse,
    TrustedContext,
)
from compass_agent.settings import get_settings
from compass_agent.tools.contracts import ToolRejected
from compass_agent.tools.mcp.client import EndpointPolicy, McpSession
from compass_agent.tools.mcp.repository import McpRepository, owner_key

router = APIRouter(
    prefix="/v1/mcp",
    tags=["personal-mcp"],
    responses={s: {"model": ModelErrorResponse} for s in (401, 403, 404, 409, 422, 503)},
)


def managed_context(context: Context) -> TrustedContext:
    require(context, {"mcp_manage"}, "tenant")
    return context


def repository() -> McpRepository:
    return McpRepository(get_engine(), get_settings().compass_model_encryption_key)


def endpoint_policy() -> EndpointPolicy:
    settings = get_settings()
    return EndpointPolicy(
        settings.compass_mcp_allowed_origins, settings.compass_mcp_local_fixture_origins
    )


Managed = Annotated[TrustedContext, Depends(managed_context)]
Repository = Annotated[McpRepository, Depends(repository)]


@router.get("/connections", response_model=McpConnectionList)
def list_connections(context: Managed, repo: Repository):
    return McpConnectionList(items=repo.list(owner_key(context)))


def save(repo, context, command, identifier):
    try:
        endpoint_policy().validate(command.endpoint)
    except ToolRejected:
        raise ModelError("endpoint_not_allowed") from None
    return repo.save(owner_key(context), command, identifier)


@router.post("/connections", response_model=McpConnectionView, status_code=201)
def create_connection(context: Managed, repo: Repository, command: McpConnectionSave):
    return save(repo, context, command, None)


@router.patch("/connections/{connection_id}", response_model=McpConnectionView)
def update_connection(
    connection_id: UUID, context: Managed, repo: Repository, command: McpConnectionSave
):
    return save(repo, context, command, connection_id)


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(
    connection_id: UUID, context: Managed, repo: Repository, command: McpConnectionDelete
):
    repo.delete(owner_key(context), connection_id, command.revision)
    return Response(status_code=204)


@router.post("/connections/{connection_id}/test", response_model=McpDiscoveryResult)
async def discover(connection_id: UUID, context: Managed, repo: Repository):
    (config,) = await asyncio.to_thread(repo.load, owner_key(context), connection_id)
    session = McpSession(config, endpoint_policy())
    try:
        await session.start(asyncio.Semaphore(1))
        return McpDiscoveryResult(
            revision=config.revision,
            tools=[
                McpDiscoveredTool(
                    name=t.name,
                    description=(t.description or "")[:2000],
                    input_schema=t.input_schema,
                    output_schema=t.output_schema,
                )
                for t in session.tools
            ],
        )
    except ToolRejected:
        raise ModelError("unavailable", 503) from None
    finally:
        await session.close()
