"""M1 stream contract reference. Export Web artifacts or emit a sample SSE stream."""

import json
import sys
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue

ProtocolName = Literal["compass.run"]
ProtocolVersion = Literal[1]
PROTOCOL: ProtocolName = get_args(ProtocolName)[0]
VERSION: ProtocolVersion = get_args(ProtocolVersion)[0]
LIMITS = {
    "frameBytes": 256 * 1024,
    "streamBytes": 2 * 1024 * 1024,
    "events": 96,
    "batchItems": 16,
    "batchBytes": 32 * 1024,
    "batchWaitMs": 100,
    "heartbeatMs": 15_000,
    "extensionBytes": 4 * 1024,
    "jsonDepth": 20,
    "answerBytes": 32 * 1024,
    "fileBytes": 128 * 1024,
    "fileRows": 200,
    "steps": 6,
    "toolsPerStep": 4,
}
UUID = Annotated[str, Field(pattern=r"^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$")]
ToolId = Annotated[str, Field(min_length=1, max_length=200)]
Day = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
RowCount = Annotated[int, Field(ge=1, le=LIMITS["fileRows"])]
ToolError = Literal[
    "unknown_tool",
    "invalid_parameters",
    "retail_unavailable",
    "retail_query_failed",
    "retail_query_timeout",
    "result_limit",
]
RunError = Literal[
    "model_failed",
    "model_incomplete",
    "model_unavailable",
    "run_timeout",
    "context_limit",
    "step_limit",
    "internal_error",
]
StopCause = Literal["user", "disconnected", "shutdown"]
CleanupCause = StopCause | Literal["deadline", "execution_error"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Empty(Strict):
    pass


class Routed(Strict):
    providerId: UUID
    modelLabel: Annotated[str, Field(min_length=1, max_length=160)]
    sceneId: Annotated[str, Field(pattern=r"^SC-[0-9]{2}$")]
    toolsEnabled: bool


class StepStart(Strict):
    stepId: UUID
    stepIndex: Annotated[int, Field(ge=1, le=LIMITS["steps"])]
    modelCallId: UUID


class Period(Strict):
    start: Day
    end: Day


class ChannelParams(Strict):
    dimension: Literal["channel"]
    period: Period
    compareTo: Period | None


class ProductParams(Strict):
    dimension: Literal["product"]
    period: Period
    compareTo: Period | None
    channelCode: Annotated[str, Field(pattern=r"^[A-Z0-9_]{1,24}$")]


RetailParams = Annotated[ChannelParams | ProductParams, Field(discriminator="dimension")]


class ToolKey(Strict):
    stepId: UUID
    toolCallId: ToolId
    toolIndex: Annotated[int, Field(ge=1, le=LIMITS["toolsPerStep"])]


class ToolStart(ToolKey):
    toolKind: Literal["retail_sales", "unavailable"]
    params: RetailParams | None


class ToolOk(ToolKey):
    status: Literal["ok"]
    rowCount: RowCount


class ToolEmpty(ToolKey):
    status: Literal["empty"]
    rowCount: Literal[0]


class ToolFailed(ToolKey):
    status: Literal["error"]
    errorCode: ToolError


ToolFinish = Annotated[ToolOk | ToolEmpty | ToolFailed, Field(discriminator="status")]


class StepFinish(Strict):
    stepId: UUID
    status: Literal["completed", "failed", "canceled"]


class FileNone(Strict):
    status: Literal["none"]


class FileUnavailable(Strict):
    status: Literal["unavailable"]
    code: Literal["file_limit", "file_generation_failed"]


class FileReady(Strict):
    status: Literal["ready"]
    stepId: UUID
    toolCallId: ToolId
    filename: Annotated[str, Field(pattern=r"^retail-products-[a-f0-9-]{36}\.csv$")]
    mediaType: Literal["text/csv; charset=utf-8"]
    rowCount: RowCount
    byteLength: Annotated[int, Field(ge=1, le=LIMITS["fileBytes"])]
    contentBase64: Annotated[
        str, Field(min_length=4, max_length=4 * ((LIMITS["fileBytes"] + 2) // 3))
    ]


AnalysisFile = Annotated[FileNone | FileUnavailable | FileReady, Field(discriminator="status")]


class Completed(Strict):
    status: Literal["completed"]
    finalAnswer: Annotated[str, Field(min_length=1, max_length=LIMITS["answerBytes"])]
    file: AnalysisFile


class Failed(Strict):
    status: Literal["failed"]
    errorCode: RunError


class Canceled(Strict):
    status: Literal["canceled"]
    reason: StopCause
    cleanup: Literal["confirmed"]


class CleanupUnconfirmed(Strict):
    status: Literal["cleanup_unconfirmed"]
    reason: CleanupCause
    cleanup: Literal["unconfirmed"]


RunResult = Annotated[
    Completed | Failed | Canceled | CleanupUnconfirmed, Field(discriminator="status")
]


class Extension(Strict):
    name: Annotated[str, Field(max_length=96, pattern=r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")]
    version: Annotated[int, Field(ge=1, le=65535)]
    required: bool
    data: dict[str, JsonValue]


class RunStarted(Strict):
    type: Literal["run.started"]
    payload: Empty


class RunRouted(Strict):
    type: Literal["run.routed"]
    payload: Routed


class StepStarted(Strict):
    type: Literal["step.started"]
    payload: StepStart


class ToolStarted(Strict):
    type: Literal["tool.started"]
    payload: ToolStart


class ToolFinished(Strict):
    type: Literal["tool.finished"]
    payload: ToolFinish


class StepFinished(Strict):
    type: Literal["step.finished"]
    payload: StepFinish


class StreamExtension(Strict):
    type: Literal["stream.extension"]
    payload: Extension


ProgressEvent = Annotated[
    RunRouted | StepStarted | ToolStarted | ToolFinished | StepFinished | StreamExtension,
    Field(discriminator="type"),
]


class StreamBatch(Strict):
    type: Literal["stream.batch"]
    payload: Annotated[list[ProgressEvent], Field(min_length=1, max_length=LIMITS["batchItems"])]


class RunFinished(Strict):
    type: Literal["run.finished"]
    payload: RunResult


StreamEvent = Annotated[
    RunStarted
    | RunRouted
    | StepStarted
    | ToolStarted
    | ToolFinished
    | StepFinished
    | StreamExtension
    | StreamBatch
    | RunFinished,
    Field(discriminator="type"),
]


class RunPacket(Strict):
    protocol: ProtocolName
    version: ProtocolVersion
    requestId: UUID
    runId: UUID
    seq: Annotated[int, Field(ge=1, le=LIMITS["events"])]
    event: StreamEvent


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def check_json_depth(root) -> None:
    pending = [(root, 0)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, (dict, list)):
            if depth >= LIMITS["jsonDepth"]:
                raise ValueError("JSON depth exceeded")
            values = value.values() if isinstance(value, dict) else value
            pending.extend((child, depth + 1) for child in values)


class RunWriter:
    def __init__(self, request_id: str, run_id: str):
        self.request_id = request_id
        self.run_id = run_id
        self.seq = self.bytes = self.events = 0
        self.finished = False

    def write(self, event: StreamEvent) -> bytes:
        if self.finished or (self.seq == 0) != (event.type == "run.started"):
            raise ValueError("invalid start or terminal order")
        packet = RunPacket(
            protocol=PROTOCOL,
            version=VERSION,
            requestId=self.request_id,
            runId=self.run_id,
            seq=self.seq + 1,
            event=event,
        )
        items = event.payload if isinstance(event, StreamBatch) else [event]
        for item in items:
            if isinstance(item, StreamExtension) and (
                item.payload.required
                or len(json_bytes(item.payload.data)) > LIMITS["extensionBytes"]
            ):
                raise ValueError("unsupported required extension or oversized payload")
            if (
                isinstance(item, RunFinished)
                and isinstance(item.payload, Completed)
                and (
                    not item.payload.finalAnswer.strip()
                    or len(json_bytes(item.payload.finalAnswer)) > LIMITS["answerBytes"]
                )
            ):
                raise ValueError("invalid final answer")
        wire = packet.model_dump(mode="json")
        check_json_depth(wire)
        frame = b"data: " + json_bytes(wire) + b"\n\n"
        size_limit = (
            LIMITS["batchBytes"] if isinstance(event, StreamBatch) else LIMITS["frameBytes"]
        )
        if len(frame) > size_limit or self.events + len(items) > LIMITS["events"]:
            raise ValueError("packet budget exceeded")
        self._count_bytes(frame)
        self.seq += 1
        self.events += len(items)
        self.finished = isinstance(event, RunFinished)
        return frame

    def heartbeat(self) -> bytes:
        if self.finished:
            raise ValueError("stream finished")
        frame = b": keepalive\n\n"
        self._count_bytes(frame)
        return frame

    def _count_bytes(self, frame: bytes) -> None:
        if self.bytes + len(frame) > LIMITS["streamBytes"]:
            raise ValueError("stream budget exceeded")
        self.bytes += len(frame)


def export(target: Path) -> None:
    schema = RunPacket.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    defs = schema["$defs"]
    # Export named anchors used by Web as well as the top-level packet.
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "Compass Run Stream", "version": str(VERSION)},
        "paths": {},
        "components": {
            "schemas": {
                **defs,
                "RunPacket": {
                    key: value for key, value in schema.items() if key not in {"$defs", "$schema"}
                },
            }
        },
    }
    (target / "schema.json").write_bytes(json_bytes(schema))
    (target / "openapi.json").write_text(
        json.dumps(openapi, ensure_ascii=False).replace("#/$defs/", "#/components/schemas/"),
        encoding="utf-8",
    )
    (target / "limits.ts").write_text(
        f"export const PROTOCOL = {json.dumps(PROTOCOL)};\n"
        f"export const VERSION = {VERSION};\n"
        f"export const LIMITS = {json.dumps(LIMITS)} as const;\n",
        encoding="utf-8",
    )


def sample() -> None:
    out = RunWriter("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
    step = "00000000-0000-4000-8000-000000000003"
    call = "00000000-0000-4000-8000-000000000004"
    events = [
        RunStarted(type="run.started", payload=Empty()),
        StepStarted(
            type="step.started", payload=StepStart(stepId=step, stepIndex=1, modelCallId=call)
        ),
        StepFinished(type="step.finished", payload=StepFinish(stepId=step, status="completed")),
        RunFinished(
            type="run.finished",
            payload=Completed(
                status="completed",
                finalAnswer="没有查询工具轮次的示例。",
                file=FileNone(status="none"),
            ),
        ),
    ]
    for event in events:
        sys.stdout.buffer.write(out.write(event))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--export":
        export(Path(sys.argv[2]))
    elif sys.argv[1:] == ["--sample"]:
        sample()
    else:
        raise SystemExit("usage: stream_contract.py --export DIR | --sample")
