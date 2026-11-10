"""`compass.run` v1 packet encoder and finite stream budget."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    RUN_STREAM_PROTOCOL,
    RUN_STREAM_VERSION,
    RunCompleted,
    RunFinished,
    RunPacket,
    RunStreamEvent,
    StreamBatch,
    StreamExtension,
)


def json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def check_json_depth(root: Any) -> None:
    pending = [(root, 0)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, (dict, list)):
            if depth >= RUN_STREAM_LIMITS["jsonDepth"]:
                raise ValueError("JSON depth exceeded")
            values = value.values() if isinstance(value, dict) else value
            pending.extend((child, depth + 1) for child in values)


class RunWriter:
    def __init__(self, request_id: UUID, run_id: UUID):
        self.request_id = str(request_id)
        self.run_id = str(run_id)
        self.seq = 0
        self.bytes = 0
        self.events = 0
        self.finished = False

    def write(self, event: RunStreamEvent) -> bytes:
        if self.finished or (self.seq == 0) != (event.type == "run.started"):
            raise ValueError("invalid start or terminal order")
        packet = RunPacket(
            protocol=RUN_STREAM_PROTOCOL,
            version=RUN_STREAM_VERSION,
            requestId=self.request_id,
            runId=self.run_id,
            seq=self.seq + 1,
            event=event,
        )
        items = event.payload if isinstance(event, StreamBatch) else [event]
        for item in items:
            if isinstance(item, StreamExtension) and (
                item.payload.required
                or len(json_bytes(item.payload.data)) > RUN_STREAM_LIMITS["extensionBytes"]
            ):
                raise ValueError("unsupported required extension or oversized payload")
            if (
                isinstance(item, RunFinished)
                and isinstance(item.payload, RunCompleted)
                and (
                    not item.payload.final_answer.strip()
                    or len(json_bytes(item.payload.final_answer)) > RUN_STREAM_LIMITS["answerBytes"]
                )
            ):
                raise ValueError("invalid final answer")
        wire = packet.model_dump(mode="json", by_alias=True)
        check_json_depth(wire)
        frame = b"data: " + json_bytes(wire) + b"\n\n"
        size_limit = (
            RUN_STREAM_LIMITS["batchBytes"]
            if isinstance(event, StreamBatch)
            else RUN_STREAM_LIMITS["frameBytes"]
        )
        if len(frame) > size_limit or self.events + len(items) > RUN_STREAM_LIMITS["events"]:
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
        if self.bytes + len(frame) > RUN_STREAM_LIMITS["streamBytes"]:
            raise ValueError("stream budget exceeded")
        self.bytes += len(frame)
