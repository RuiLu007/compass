"""Content-free lifecycle logging shared by durable and compatibility transports."""

import logging

from compass_agent.observability.privacy import tool_label
from compass_agent.runtime.control import RunControl
from compass_agent.schemas import RunProgressEvent

logger = logging.getLogger("compass_agent.api.runs")


def observe_tool_receipts(root, events):
    """Project newly committed terminal tool receipts. Recovery can replay handler entry."""
    if root is None:
        return
    for event in events:
        obj = event["payload"]
        if obj["kind"] != "timeline":
            continue
        progress = obj["value"]["event"]
        payload = progress["payload"]
        if (
            progress["type"] != "stream.extension"
            or payload.get("name") != "compass.tool.execution"
        ):
            continue
        result = payload["data"]
        if result["cleanup"] == "not_started" or result["status"] in {"waiting", "rejected"}:
            continue
        with root.child(
            "compass.tool.execute",
            step_id=result["stepId"],
            tool=tool_label(result["toolName"]),
            tool_status=result["status"],
            tool_operation=result.get("operation"),
            tool_evidence="execution_receipt",
        ) as span:
            span.native("type", "tool")
            if result["status"] not in {"ok", "empty"}:
                span.complete("canceled" if result["status"] == "canceled" else "failed")


PROGRESS_LOG_EVENTS = {
    "run.routed": "analysis.run.routed",
    "step.started": "analysis.step.started",
    "tool.started": "analysis.tool.started",
    "tool.finished": "analysis.tool.finished",
    "step.finished": "analysis.step.finished",
    "stream.extension": "analysis.stream.extension",
}


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
