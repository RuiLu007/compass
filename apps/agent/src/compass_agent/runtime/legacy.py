"""Finite v1 wire adapter over the durable Session/Run execution path."""

import asyncio
import base64
import csv
import io
from typing import get_args
from uuid import UUID, uuid4

from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from compass_agent.runtime.commands import control
from compass_agent.runtime.entities import AgentMessage, AgentWait
from compass_agent.runtime.execution_detail import legacy_node
from compass_agent.runtime.store import TERMINAL
from compass_agent.runtime.stream import RunWriter
from compass_agent.schemas import (
    ControlCommand,
    RunEmpty,
    RunError,
    RunFinished,
    RunProgressEvent,
    RunResult,
    RunStarted,
    SessionSubmit,
)


def terminal_result(service, run):
    with Session(service.store.engine) as tx:
        if run.status == "waiting":
            wait = tx.scalars(
                select(AgentWait).where(AgentWait.run_id == run.id, AgentWait.status == "pending")
            ).one()
            return {
                "status": "waiting",
                "runId": str(run.id),
                "waitId": str(wait.id),
                "nodeKey": "budget_decision"
                if wait.expectation["continuation"] == "apply_budget"
                else "tool_approval",
                "continuation": wait.expectation["continuation"],
                "proposal": wait.public_proposal,
            }
        if run.status == "canceled":
            return {"status": "canceled", "reason": "user", "cleanup": "confirmed"}
        if run.status != "completed":
            reason = run.reason_code if run.reason_code in get_args(RunError) else "internal_error"
            return {"status": "failed", "errorCode": reason}
        message = tx.scalars(
            select(AgentMessage).where(
                AgentMessage.run_id == run.id,
                AgentMessage.channel == "conversation",
                AgentMessage.role == "assistant",
            )
        ).one()
        assert message.public_content is not None
        artifacts, file = [], {"status": "none"}
        for obj in run.public_view["objects"].values():
            if obj["kind"] != "artifact":
                continue
            ref = obj["value"]
            if ref["mediaType"] == "image/png":
                artifacts.append({k: v for k, v in ref.items() if k != "sha256"})
            else:
                from compass_agent.runtime.entities import AgentArtifact

                item = tx.get(AgentArtifact, UUID(ref["id"]))
                assert item is not None
                rows = max(
                    1, sum(1 for _ in csv.reader(io.StringIO(item.content.decode("utf-8-sig")))) - 1
                )
                file = {
                    "status": "ready",
                    "stepId": ref["stepId"],
                    "toolCallId": ref["toolCallId"],
                    "filename": ref["name"],
                    "mediaType": "text/csv; charset=utf-8",
                    "rowCount": rows,
                    "byteLength": len(item.content),
                    "contentBase64": base64.b64encode(item.content).decode(),
                }
        return {
            "status": "completed",
            "finalAnswer": message.public_content["text"],
            "file": file,
            "artifacts": artifacts,
        }


async def start_legacy(service, body, context, executor, carrier=None):
    environment = await service.access.check(context, run_environment=True)
    command = SessionSubmit.model_validate(
        body.model_dump(mode="json", by_alias=True, exclude={"history"})
    )
    receipt = await asyncio.to_thread(
        service.store.submit,
        command,
        context,
        run_environment=environment,
        imported_history=[h.model_dump(mode="json") for h in body.history],
    )
    run_id = UUID(receipt["runId"])
    headers = await asyncio.to_thread(service.begin_observation, run_id, context, executor, carrier)
    await service.publisher.flush(run_id)
    run = await asyncio.to_thread(service.store.read_run, run_id, context)
    if run.status == "created":
        service.schedule(run_id, executor, context)

    async def events():
        writer, cursor, finished = RunWriter(body.request_id, run_id), "0-0", False
        progress = TypeAdapter(RunProgressEvent)
        try:
            yield writer.write(RunStarted(type="run.started", payload=RunEmpty()))
            while True:
                run = await asyncio.to_thread(service.store.read_run, run_id, context)
                rows = await service.streams.rows(run_id, cursor, through=run.projected_seq)
                for seq, entry in rows:
                    cursor = seq
                    event = entry["packet"]["event"]
                    if event["type"] != "view.upsert":
                        continue
                    obj = event["payload"]
                    value = None
                    if obj["kind"] == "timeline":
                        value = obj["value"]["event"]
                    elif obj["kind"] == "execution.node":
                        value = {
                            "type": "stream.extension",
                            "payload": {
                                "name": "compass.execution.node",
                                "version": 1,
                                "required": False,
                                "data": legacy_node(obj["value"]),
                            },
                        }
                    if value:
                        yield writer.write(progress.validate_python(value))
                if (
                    run.status in TERMINAL | {"waiting", "paused"}
                    and run.public_view["run"]["status"] == run.status
                ):
                    value = await asyncio.to_thread(terminal_result, service, run)
                    yield writer.write(
                        RunFinished(
                            type="run.finished",
                            payload=TypeAdapter(RunResult).validate_python(value),
                        )
                    )
                    finished = True
                    return
                yield writer.heartbeat()
                await asyncio.sleep(0.1)
        finally:
            if not finished:
                run = await asyncio.to_thread(service.store.read_run, run_id, context)
                if run.status not in TERMINAL:
                    command = ControlCommand(requestId=uuid4(), expectedVersion=run.version)
                    await asyncio.to_thread(
                        control, service.store, run_id, "cancel", command, context
                    )
                    if run_id in service.controls:
                        service.controls[run_id].request_stop("disconnected")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no", **headers},
    )
