"""Private Agent Run API with versioned SSE and a separate cancellation control plane."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect

from compass_agent.model_api import get_model_service, require, trusted_context
from compass_agent.models.errors import ModelError
from compass_agent.models.service import ModelService
from compass_agent.routing.service import RoutingService
from compass_agent.runtime.control import (
    OwnerKey,
    RunCapacityExceeded,
    RunControl,
    RunRegistry,
    RunRegistryClosed,
    TerminalRunSummary,
)
from compass_agent.runtime.executor import AgentExecutor
from compass_agent.runtime.stream import RunWriter
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    AnalysisCancelAck,
    AnalysisCancelAlreadyTerminal,
    AnalysisRunInput,
    ModelErrorResponse,
    RunEmpty,
    RunFailed,
    RunFinished,
    RunProgressEvent,
    RunStarted,
    TrustedContext,
)
from compass_agent.settings import get_settings

logger = logging.getLogger(__name__)

PROGRESS_LOG_EVENTS = {
    "run.routed": "analysis.run.routed",
    "step.started": "analysis.step.started",
    "tool.started": "analysis.tool.started",
    "tool.finished": "analysis.tool.finished",
    "step.finished": "analysis.step.finished",
    "stream.extension": "analysis.stream.extension",
}

router = APIRouter(
    prefix="/v1/analysis",
    tags=["analysis"],
    responses={
        401: {"model": ModelErrorResponse},
        403: {"model": ModelErrorResponse},
        404: {"model": ModelErrorResponse},
        422: {"model": ModelErrorResponse},
        429: {"model": ModelErrorResponse},
        503: {"model": ModelErrorResponse},
    },
)


def get_run_registry(request: Request) -> RunRegistry:
    return request.app.state.run_registry


Context = Annotated[TrustedContext, Depends(trusted_context)]
Registry = Annotated[RunRegistry, Depends(get_run_registry)]


class RunStreamResponse(StreamingResponse):
    """Always listen for disconnect independently of a potentially blocked send."""

    def __init__(
        self,
        *args,
        on_unstarted_close: Callable[[], Awaitable[None]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.on_unstarted_close = on_unstarted_close

    async def __call__(self, scope, receive, send) -> None:
        stream = asyncio.create_task(self.stream_response(send))
        disconnect = asyncio.create_task(self.listen_for_disconnect(receive))
        try:
            done, _ = await asyncio.wait({stream, disconnect}, return_when=asyncio.FIRST_COMPLETED)
            if disconnect in done:
                stream.cancel()
                await asyncio.gather(stream, return_exceptions=True)
            else:
                disconnect.cancel()
                await asyncio.gather(disconnect, return_exceptions=True)
                try:
                    stream.result()
                except OSError as error:
                    raise ClientDisconnect() from error
        finally:
            for task in (stream, disconnect):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stream, disconnect, return_exceptions=True)
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                try:
                    await close()
                except (Exception, asyncio.CancelledError):
                    pass
            if self.on_unstarted_close is not None:
                await self.on_unstarted_close()
        if self.background is not None:
            await self.background()


def _owner(context: TrustedContext) -> OwnerKey:
    require(context, {"chat"}, "tenant")
    return OwnerKey.from_context(context)


def _run_log(event_name: str, control: RunControl, **fields) -> None:
    # Every caller supplies only IDs or closed enums.  Content-bearing request,
    # tool, observation, continuation and connection values never enter here.
    logger.info(
        event_name,
        extra={
            "event_name": event_name,
            "run_id": str(control.id),
            **{key: value for key, value in fields.items() if value is not None},
        },
    )


def _log_progress(control: RunControl, event: RunProgressEvent) -> None:
    payload = event.payload
    fields = {
        "step_id": getattr(payload, "step_id", None),
        "model_call_id": getattr(payload, "model_call_id", None),
        "tool_call_id": getattr(payload, "tool_call_id", None),
        "status": getattr(payload, "status", None),
        "error_code": getattr(payload, "error_code", None),
    }
    if fields["step_id"] is not None:
        fields["step_id"] = str(fields["step_id"])
    if fields["model_call_id"] is not None:
        fields["model_call_id"] = str(fields["model_call_id"])
    _run_log(PROGRESS_LOG_EVENTS[event.type], control, **fields)


def _log_terminal(control: RunControl, result) -> None:
    _run_log(
        "analysis.run.finished",
        control,
        status=result.status,
        error_code=getattr(result, "error_code", None),
        stop_reason=getattr(result, "reason", None),
        cleanup=getattr(
            result,
            "cleanup",
            "unconfirmed" if result.status == "cleanup_unconfirmed" else "confirmed",
        ),
        duration_ms=control.duration_ms,
    )


def _log_cancel(control: RunControl, ack, reason: str) -> None:
    _run_log(
        "analysis.run.cancel",
        control,
        status=ack.status,
        terminal_status=getattr(ack, "terminal_status", None),
        stop_reason=reason,
        cleanup="pending" if ack.status == "accepted" else "confirmed",
    )


def require_run_context(context: Context) -> TrustedContext:
    _owner(context)
    return context


RunContext = Annotated[TrustedContext, Depends(require_run_context)]


def get_agent_executor(
    _context: RunContext,
    service: Annotated[ModelService, Depends(get_model_service)],
) -> AgentExecutor:
    return AgentExecutor(service, RoutingService(service), get_settings())


Executor = Annotated[AgentExecutor, Depends(get_agent_executor)]


async def _observed_runner(
    request: Request,
    executor: AgentExecutor,
    command: AnalysisRunInput,
    context: TrustedContext,
    control: RunControl,
    emit,
):
    observation = getattr(request.state, "model_observation", None)
    try:
        result = await executor.run(command, context, control, emit)
        if observation:
            observation.complete(result.status, getattr(result, "error_code", None))
        _log_terminal(control, result)
        return result
    except BaseException as error:
        if control.terminal is None:
            if isinstance(error, asyncio.CancelledError):
                control.mark_cleanup_unconfirmed()
                control.request_stop("shutdown")
            result = control.commit(RunFailed(status="failed", errorCode="internal_error"))
        else:
            result = control.terminal
        if observation:
            observation.complete(
                "canceled" if isinstance(error, asyncio.CancelledError) else "failed",
                getattr(error, "code", "internal_error"),
            )
        _log_terminal(control, result)
        return result
    finally:
        if observation:
            observation.end()


@router.post(
    "/runs",
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string", "description": "compass.run v1 SSE packets"}
                }
            }
        }
    },
)
async def start_run(
    body: AnalysisRunInput,
    request: Request,
    context: RunContext,
    registry: Registry,
    executor: Executor,
):
    owner = _owner(context)
    try:
        control = registry.register(owner)
    except RunCapacityExceeded:
        raise ModelError("runtime_overloaded", 429) from None
    except RunRegistryClosed:
        raise ModelError("unavailable", 503) from None

    observer = getattr(request.app.state, "observation", None)
    if observer and observer.tracer:
        root = observer.root(control.id, context, request.headers, capture_content=False)
        request.state.model_observation = root
        executor.models.root_observation = root

    events_started = False

    def finish_without_runner() -> None:
        if control.terminal is not None:
            return
        ack = control.request_stop("disconnected")
        _log_cancel(control, ack, "disconnected")
        result = control.commit(RunFailed(status="failed", errorCode="internal_error"))
        observation = getattr(request.state, "model_observation", None)
        if observation:
            observation.complete(result.status, getattr(result, "error_code", None))
            observation.end()
        _log_terminal(control, result)

    async def finish_if_unstarted() -> None:
        if not events_started:
            finish_without_runner()

    async def events():
        nonlocal events_started
        events_started = True
        writer = RunWriter(body.request_id, control.id)
        progress: asyncio.Queue[RunProgressEvent] = asyncio.Queue(
            maxsize=RUN_STREAM_LIMITS["events"]
        )
        runner: asyncio.Task | None = None
        pending: asyncio.Task | None = None

        def emit(event: RunProgressEvent) -> None:
            _log_progress(control, event)
            progress.put_nowait(event)

        try:
            # Iteration starts only after FastAPI has accepted the response; no routing,
            # model or SQL work exists before this first packet is yielded.
            _run_log("analysis.run.started", control, status="running")
            yield writer.write(RunStarted(type="run.started", payload=RunEmpty()))
            runner = request.app.state.model_tasks.create(
                _observed_runner(request, executor, body, context, control, emit)
            )
            assert runner is not None
            while True:
                if runner.done():
                    while not progress.empty():
                        yield writer.write(progress.get_nowait())
                    result = runner.result()
                    yield writer.write(RunFinished(type="run.finished", payload=result))
                    return
                pending = asyncio.create_task(progress.get())
                done, _ = await asyncio.wait(
                    {runner, pending},
                    timeout=RUN_STREAM_LIMITS["heartbeatMs"] / 1000,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if pending in done:
                    yield writer.write(pending.result())
                    pending = None
                    continue
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                pending = None
                if runner in done:
                    continue
                yield writer.heartbeat()
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if control.terminal is None:
                if runner is None:
                    finish_without_runner()
                else:
                    ack = control.request_stop("disconnected")
                    _log_cancel(control, ack, "disconnected")

    return RunStreamResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
        on_unstarted_close=finish_if_unstarted,
    )


@router.post("/runs/{run_id}/cancel", response_model=AnalysisCancelAck)
async def cancel_run(
    run_id: UUID,
    _body: RunEmpty,
    response: Response,
    context: RunContext,
    registry: Registry,
):
    owner = _owner(context)
    item = registry.lookup(run_id)
    if item is None or item.owner != owner:
        raise ModelError("not_found", 404)
    if isinstance(item, TerminalRunSummary):
        response.status_code = 200
        ack = AnalysisCancelAlreadyTerminal(
            runId=str(run_id),
            status="already_terminal",
            terminalStatus=item.status,
        )
        logger.info(
            "analysis.run.cancel",
            extra={
                "event_name": "analysis.run.cancel",
                "run_id": str(run_id),
                "status": ack.status,
                "terminal_status": item.status,
                "stop_reason": "user",
                "cleanup": ("unconfirmed" if item.status == "cleanup_unconfirmed" else "confirmed"),
            },
        )
        return AnalysisCancelAck(root=ack)
    response.status_code = 202
    ack = item.request_stop("user")
    _log_cancel(item, ack, "user")
    return AnalysisCancelAck(root=ack)
