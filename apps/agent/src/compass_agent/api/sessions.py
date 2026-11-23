"""Durable Session commands, Message JSON and the shared v2 Run SSE protocol."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from compass_agent.api.runs import Executor, RunContext
from compass_agent.context_runtime.source_access import project_sources
from compass_agent.models.errors import ModelError
from compass_agent.runtime.commands import context_reset, control
from compass_agent.runtime.durable_service import DurableRuntime
from compass_agent.runtime.journal import ExecutionJournal
from compass_agent.runtime.public_projection import stream_position
from compass_agent.runtime.public_visibility import visible_response
from compass_agent.runtime.session_management import update_session
from compass_agent.runtime.store import TERMINAL
from compass_agent.schemas import (
    AgentResponse,
    CitationDetail,
    ControlCommand,
    ControlReceipt,
    DecisionCommand,
    ExternalRunEvent,
    ReconcileCommand,
    RunCitations,
    SemanticOpenResult,
    SemanticResultDecision,
    SemanticResults,
    SessionAccepted,
    SessionDeleted,
    SessionList,
    SessionListItem,
    SessionMutation,
    SessionRename,
    SessionSubmit,
    TrustedContext,
    WaitView,
)

router = APIRouter(prefix="/v1/analysis", tags=["sessions"])
internal_router = APIRouter(prefix="/v1/internal/analysis", tags=["runtime-events"])


def runtime(request: Request) -> DurableRuntime:
    value = request.app.state.durable_runtime
    if value is None:
        raise ModelError("runtime_unavailable", 503)
    return value


Runtime = Annotated[DurableRuntime, Depends(runtime)]


@router.get("/runs/{run_id}/citations", response_model=RunCitations)
async def citations(run_id: UUID, context: RunContext, service: Runtime, response: Response):
    from compass_agent.context_runtime.citations import read_citations

    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(read_citations, service.store, run_id, context)


@router.get("/runs/{run_id}/citations/{citation_id}", response_model=CitationDetail)
async def citation_detail(
    run_id: UUID, citation_id: UUID, context: RunContext, service: Runtime, response: Response
):
    from compass_agent.context_runtime.citations import read_citations

    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(read_citations, service.store, run_id, context, citation_id)


@router.get("/runs/{run_id}/semantic-results", response_model=SemanticResults)
async def semantic_results(run_id: UUID, context: RunContext, service: Runtime, response: Response):
    from compass_agent.semantic.results import list_results

    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(list_results, service.store, context, run_id)


@router.post("/runs/{run_id}/semantic-results", response_model=SemanticOpenResult)
async def semantic_result_decision(
    run_id: UUID,
    command: SemanticResultDecision,
    context: RunContext,
    service: Runtime,
    response: Response,
):
    from compass_agent.semantic.results import decide_result

    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(decide_result, service.store, context, run_id, command)


@router.post("/sessions/submit", status_code=202, response_model=SessionAccepted)
async def submit(
    command: SessionSubmit,
    context: RunContext,
    service: Runtime,
    executor: Executor,
    response: Response,
    request: Request,
):
    environment = await service.access.check(context, run_environment=True)
    receipt = await asyncio.to_thread(
        service.store.submit, command, context, run_environment=environment
    )
    run_id = UUID(receipt["runId"])
    run = await asyncio.to_thread(service.store.read_run, run_id, context)
    response.headers.update(
        await asyncio.to_thread(
            service.begin_observation, run_id, context, executor, request.headers
        )
    )
    if run.status == "created":
        service.schedule(run_id, executor, context)
    response.headers["Cache-Control"] = "no-store"
    return receipt


@router.post(
    "/sessions/retry", status_code=202, response_model=SessionAccepted, include_in_schema=False
)
async def retry_submission(request: Request, context: RunContext, service: Runtime):
    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError

    await service.access.check(context)
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ModelError("invalid_parameters", 422)
        receipt = await asyncio.to_thread(service.store.accepted_retry, data, context)
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from None
    except ValueError:
        raise ModelError("invalid_parameters", 422) from None
    return Response(
        content=SessionAccepted.model_validate(receipt).model_dump_json(by_alias=True),
        media_type="application/json",
        status_code=202,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/sessions", response_model=SessionList)
async def sessions(
    context: RunContext,
    service: Runtime,
    response: Response,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None, max_length=500),
):
    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(service.reader.sessions, context, limit=limit, cursor=cursor)


@router.get("/sessions/{session_id}", response_model=SessionListItem)
async def session_metadata(
    session_id: UUID, context: RunContext, service: Runtime, response: Response
):
    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(service.reader.session, session_id, context)


@router.get("/sessions/{session_id}/messages", response_model=AgentResponse)
async def messages(
    session_id: UUID,
    context: RunContext,
    service: Runtime,
    response: Response,
    cursor: str | None = Query(None, max_length=500),
    runId: UUID | None = None,
):
    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    visibility = await service.access.visibility(context)
    result = await asyncio.to_thread(
        service.reader.messages, session_id, context, cursor=cursor, run_id=runId
    )
    projected = await asyncio.to_thread(project_sources, service.store, result, context)
    return visible_response(projected, details=visibility["details"])


@router.patch("/sessions/{session_id}", response_model=SessionListItem)
async def rename_session(
    session_id: UUID,
    command: SessionRename,
    context: RunContext,
    service: Runtime,
    response: Response,
):
    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(update_session, service.store, session_id, command, context)


@router.delete("/sessions/{session_id}", response_model=SessionDeleted)
async def delete_session(
    session_id: UUID,
    command: SessionMutation,
    context: RunContext,
    service: Runtime,
    response: Response,
):
    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(update_session, service.store, session_id, command, context)


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
async def events(run_id: UUID, context: RunContext, service: Runtime, after: str = "0-0"):
    visibility = await service.access.visibility(context)
    run = await asyncio.to_thread(service.store.read_run, run_id, context)
    live_since = (
        datetime.now(UTC).isoformat()
        if run.config_snapshot.get("contextRuntime") and run.status not in TERMINAL
        else None
    )
    try:
        await service.streams.validate(run, after)
    except ModelError as error:
        if error.code == "stream_data_lost":
            await asyncio.to_thread(service.store.blocked, run_id, error.code)
        raise

    async def stream():
        cursor, started, checked, sent = after, asyncio.get_running_loop().time(), 0, 0
        heartbeat_at = started
        while asyncio.get_running_loop().time() - started < 60 and sent < 4 * 1024 * 1024:
            clock = asyncio.get_running_loop().time()
            if clock - checked >= 5:
                try:
                    current = await service.access.visibility(context)
                except ModelError:
                    return
                if current != visibility:
                    return
                checked = clock
            run = await asyncio.to_thread(service.store.read_run, run_id, context)
            await service.streams.validate(run, cursor)
            rows = await service.streams.rows(run_id, cursor, limit=16)
            visible = []
            for seq, entry in rows:
                if stream_position(seq) > stream_position(
                    service.streams.confirmed.get(run_id, "0-0")
                ):
                    break
                if entry.get("outboxId") and stream_position(seq) > stream_position(
                    run.projected_seq
                ):
                    break
                visible.append({**entry["packet"], "seq": seq})
            if visible:
                response = AgentResponse.model_validate(
                    {
                        "protocol": "compass.run",
                        "version": 2,
                        "sessionId": str(run.session_id),
                        "mode": "delta",
                        "packets": visible,
                        "cursors": [{"runId": str(run_id), "seq": visible[-1]["seq"]}],
                        "page": None,
                    }
                )
                if run.config_snapshot.get("contextRuntime"):
                    response = await asyncio.to_thread(
                        project_sources, service.store, response, context, live_since=live_since
                    )
                response = visible_response(response, details=visibility["details"])
                body = response.model_dump_json(by_alias=True)
                while len(body.encode()) > 256 * 1024 and len(visible) > 1:
                    visible.pop()
                    response = response.model_copy(
                        update={
                            "packets": response.packets[: len(visible)],
                            "cursors": [
                                response.cursors[0].model_copy(update={"seq": visible[-1]["seq"]})
                            ],
                        }
                    )
                    body = response.model_dump_json(by_alias=True)
                if len(body.encode()) > 256 * 1024:
                    raise ModelError("response_too_large", 503)
                cursor = visible[-1]["seq"]
                frame = f"id: {run_id}:{cursor}\nevent: agent\ndata: {body}\n\n".encode()
                sent += len(frame)
                yield frame
            if (
                run.public_view["run"]["status"] in TERMINAL
                and cursor == run.projected_seq
                and not await asyncio.to_thread(service.store.pending, run_id)
            ):
                return
            if not visible:
                if clock >= heartbeat_at:
                    yield b": keepalive\n\n"
                    heartbeat_at = clock + 5
                # Read available packets promptly without waiting to fill the 16-item limit.
                await asyncio.sleep(0.05)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/context-reset", response_model=ControlReceipt)
async def reset(
    session_id: UUID,
    command: ControlCommand,
    context: RunContext,
    service: Runtime,
    response: Response,
):
    await service.access.check(context)
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(context_reset, service.store, session_id, command, context)


async def apply_control(run_id, kind, command, context, service, executor=None):
    await service.access.check(context)
    if kind == "resume":
        run = await asyncio.to_thread(service.store.read_run, run_id, context)
        journal = ExecutionJournal(service.store, service.publisher, run_id, run.execution_epoch)
        await asyncio.to_thread(journal.validate_checkpoint)
        await service.streams.validate(run)
    receipt = await asyncio.to_thread(control, service.store, run_id, kind, command, context)
    if kind == "cancel" and run_id in service.controls:
        service.controls[run_id].request_stop("user")
    if kind == "resume" and executor is not None:
        service.schedule(run_id, executor, context)
    return receipt


@router.post("/runs/{run_id}/pause", response_model=ControlReceipt)
async def pause(
    run_id: UUID, command: ControlCommand, context: RunContext, service: Runtime, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    return await apply_control(run_id, "pause", command, context, service)


@router.post("/runs/{run_id}/resume", response_model=ControlReceipt)
async def resume(
    run_id: UUID,
    command: ControlCommand,
    context: RunContext,
    service: Runtime,
    executor: Executor,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    return await apply_control(run_id, "resume", command, context, service, executor)


@router.post("/runs/{run_id}/decisions", response_model=ControlReceipt)
async def decide(
    run_id: UUID,
    command: DecisionCommand,
    context: RunContext,
    service: Runtime,
    executor: Executor,
    response: Response,
):
    await service.wait_access(
        context, run_id, command.wait_id, approval=command.decision == "approve"
    )
    receipt = await asyncio.to_thread(
        service.hitl.decide,
        run_id,
        command,
        context,
        may_approve=command.decision == "approve",
        boot_id=service.boot_id,
    )
    if receipt.get("code") == "approval_expired":
        response.status_code = 409
    elif command.decision == "approve":
        service.schedule(run_id, executor, context)
    response.headers["Cache-Control"] = "no-store"
    return receipt


@router.get("/runs/{run_id}/waits/{wait_id}", response_model=WaitView)
async def wait(
    run_id: UUID, wait_id: UUID, context: RunContext, service: Runtime, response: Response
):
    await service.access.check(context)
    may_approve = False
    try:
        await service.wait_access(context, run_id, wait_id, approval=True)
        may_approve = True
    except ModelError as error:
        if error.status != 403:
            raise
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(
        service.hitl.get, run_id, wait_id, context, may_approve=may_approve
    )


@router.post("/runs/{run_id}/reconcile", response_model=ControlReceipt)
async def reconcile(
    run_id: UUID,
    command: ReconcileCommand,
    context: RunContext,
    service: Runtime,
    executor: Executor,
    response: Response,
):
    await service.access.check(context)
    receipt = await asyncio.to_thread(service.hitl.reconcile, run_id, command, context)
    if receipt["code"] == "effect_confirmed":
        service.schedule(run_id, executor, context)
    response.headers["Cache-Control"] = "no-store"
    return receipt


@internal_router.post("/runs/{run_id}/events", response_model=ControlReceipt)
async def external_event(run_id: UUID, event: ExternalRunEvent, request: Request, service: Runtime):
    import hmac

    from compass_agent.api.models import get_model_service
    from compass_agent.api.runs import get_agent_executor

    secret = service.settings.compass_runtime_event_token.get_secret_value()
    if not secret or not hmac.compare_digest(
        request.headers.get("authorization", "").encode(), ("Bearer " + secret).encode()
    ):
        raise ModelError("unauthenticated", 401)
    receipt = await asyncio.to_thread(
        service.hitl.external_event, run_id, event, boot_id=service.boot_id
    )
    run = await asyncio.to_thread(service.store.read_run, run_id)
    context = TrustedContext.model_validate(run.config_snapshot["context"])
    executor = get_agent_executor(request, context, get_model_service(request))
    service.schedule(run_id, executor, context)
    return Response(
        content=ControlReceipt.model_validate(receipt).model_dump_json(by_alias=True),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )
