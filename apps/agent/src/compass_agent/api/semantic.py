"""Generate editable governance suggestions from BFF-authorized source extracts."""

import asyncio
import json
from typing import Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from compass_agent.api.models import Context, Service, require
from compass_agent.database import get_engine
from compass_agent.models.errors import ModelError
from compass_agent.promptops.reader import PromptReader
from compass_agent.routing.service import RoutingService
from compass_agent.schemas import ChatMessage, GenerateInput, ModelErrorResponse

router = APIRouter(
    prefix="/v1/semantic",
    tags=["semantic"],
    responses={s: {"model": ModelErrorResponse} for s in (403, 409, 422, 503)},
)


class Material(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: UUID
    position: str = Field(min_length=1, max_length=400)
    material: str = Field(max_length=2400)
    source_version: str = Field(min_length=1, max_length=160)


class SemanticAnalysis(BaseModel):
    sources: list[Material] = Field(min_length=1, max_length=8)
    task: Literal["discover", "review"] = "discover"
    draft: str = Field(default="", max_length=4000)


class Candidate(BaseModel):
    source_ref: UUID
    position: str = Field(max_length=400)
    name: str = Field(min_length=1, max_length=160)
    summary: str = Field(max_length=1600)
    formula: str = Field(max_length=1600)
    confidence: float = Field(ge=0, le=1)
    missing: list[str] = Field(max_length=16)
    suggestions: list[str] = Field(max_length=16)


class SemanticSuggestions(BaseModel):
    candidates: list[Candidate] = Field(max_length=12)


def instruction():
    with Session(get_engine()) as tx:
        generation = PromptReader.pin_current(tx)
        return PromptReader.load_exact(
            tx, generation, ("shared/fragments/semantic-governance.md",)
        )[0].body


@router.post("/suggestions", response_model=SemanticSuggestions)
async def suggestions(
    value: SemanticAnalysis, context: Context, models: Service, response: Response
):
    require(context, {"chat"}, "tenant")
    response.headers["Cache-Control"] = "no-store"
    source = {str(v.source_ref): v for v in value.sources}
    provider_id, _ = await RoutingService(models).provider("flash")
    input = GenerateInput(
        messages=[
            ChatMessage(role="system", content=await asyncio.to_thread(instruction)),
            ChatMessage(role="user", content=value.model_dump_json()),
        ],
        max_tokens=2048,
    )
    job = await models.prepare(
        uuid4(), provider_id, input, context, deadline=asyncio.get_running_loop().time() + 45
    )
    result = await models.run(job, input)
    if result.status != "completed":
        raise ModelError("upstream_unavailable", 503)
    try:
        parsed = SemanticSuggestions.model_validate(json.loads(cast(str, result.text)))
        for item in parsed.candidates:
            material = source[str(item.source_ref)]
            item.position = material.position
        return parsed
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ModelError("upstream_unavailable", 503) from exc


class BindingTrial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding: dict
    parameters: dict
    expected: list[dict[str, str | None]] = Field(min_length=1, max_length=50)


class TrialResult(BaseModel):
    matched: bool
    rows: int
    target_hash: str


@router.post("/trial", response_model=TrialResult)
async def binding_trial(value: BindingTrial, context: Context, response: Response):
    from decimal import Decimal, InvalidOperation

    from compass_agent.data_connections.connections import DataConnections
    from compass_agent.runtime.control import OwnerKey, RunRegistry
    from compass_agent.schemas import TenantScope
    from compass_agent.semantic.execution import calculate, choose_connection
    from compass_agent.settings import get_settings
    from compass_agent.tools.contracts import ToolRejected

    require(context, {"chat"}, "tenant")
    response.headers["Cache-Control"] = "no-store"
    assert isinstance(context.scope, TenantScope)
    connections = await asyncio.to_thread(
        DataConnections(get_engine(), get_settings()).bindings_for_run, context.scope
    )
    if value.parameters.get("end", "") <= value.parameters.get("start", "") or any(
        set(row) != {"group_key", "status", "value"} for row in value.expected
    ):
        raise ModelError("invalid_parameters", 422)
    try:
        conn = choose_connection(connections, value.binding)
        control = RunRegistry().register(OwnerKey.from_context(context), lifetime_s=8)
        actual, _ = await control.perform(
            lambda: calculate(conn, value.binding, value.parameters, control)
        )

        def comparable(rows):
            return sorted(
                (
                    (
                        r["group_key"],
                        r["status"],
                        None if r["value"] is None else Decimal(str(r["value"])),
                    )
                    for r in rows
                ),
                key=lambda v: (v[0] is not None, str(v[0])),
            )

        matched = comparable(value.expected) == comparable(actual)
    except (ToolRejected, ValueError, KeyError, InvalidOperation, TimeoutError) as err:
        raise ModelError("validation_failed", 422) from err
    return TrialResult(matched=matched, rows=len(actual), target_hash=value.binding["target_hash"])
