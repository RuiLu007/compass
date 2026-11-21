"""Authenticate scoped requests to the Web document owner and validate the fixed binding."""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from compass_agent.models.errors import ModelError
from compass_agent.semantic import client as transport

Kind = Literal["semantic", "detail", "knowledge", "history", "external"]


class SearchSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenantId: UUID
    workspaceId: UUID
    runId: UUID
    actorUserId: UUID
    knownAt: AwareDatetime
    timezone: str
    collectionDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    connectionId: UUID
    connectionRevision: int = Field(ge=1)
    dataRelease: str = Field(min_length=1, max_length=240)
    kinds: list[Kind] = Field(max_length=5)
    skillEnabled: bool
    scenePack: Literal["retail"] | None = None
    maxToolCalls: int | None = Field(default=None, ge=0, le=400)


def enabled(settings, workspace_id):
    return str(workspace_id) in {
        v.strip() for v in getattr(settings, "compass_search_workspaces", "").split(",")
    }


def request(settings, trusted, run_id, op, args=None):
    return transport.request(settings, trusted, run_id, op, args, namespace="documents")


def accept(settings, trusted, run_id):
    try:
        pin = SearchSnapshot.model_validate(request(settings, trusted, run_id, "pin"))
        ZoneInfo(pin.timezone)
        if (pin.tenantId, pin.workspaceId, pin.actorUserId, pin.runId) != (
            trusted.scope.tenant_id,
            trusted.scope.workspace_id,
            trusted.actor_user_id,
            run_id,
        ) or pin.knownAt > datetime.now(pin.knownAt.tzinfo):
            raise ValueError("invalid document binding")
        return pin.model_dump(mode="json", exclude_none=True)
    except (ValidationError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ModelError("context_policy_unavailable", 503) from exc


def verified_access(settings, trusted, run_id, *, refs=(), kinds=()):
    result = request(
        settings, trusted, run_id, "verify", {"refs": list(refs), "kinds": list(kinds)}
    )
    if result.get("allowed") is not True:
        return None
    signature = result.get("accessDigest")
    if not isinstance(signature, str) or not re.fullmatch("[0-9a-f]{64}", signature):
        raise ModelError("source_authorization_unavailable", 503)
    return signature


def verify(settings, trusted, run_id, *, refs=(), kinds=()):
    return verified_access(settings, trusted, run_id, refs=refs, kinds=kinds) is not None


def access_digest(settings, trusted, run_id, tool):
    kinds = (
        ["detail"]
        if tool == "database_query"
        else []
        if tool in {"search_documents", "read_document"}
        else ["semantic"]
    )
    signature = verified_access(settings, trusted, run_id, kinds=kinds)
    if signature is None:
        raise ModelError("source_access_denied", 403)
    return signature


def restrict_connections(targets, snapshot):
    if not snapshot:
        return targets
    return tuple(
        b
        for b in targets
        if (str(b.connection_id), b.revision, str(b.tenant_id), str(b.workspace_id))
        == (
            snapshot["connectionId"],
            snapshot["connectionRevision"],
            snapshot["tenantId"],
            snapshot["workspaceId"],
        )
    )
