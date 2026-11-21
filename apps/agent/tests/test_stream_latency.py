"""Live progress is independent from slow maintenance and idle SSE polling."""

import asyncio
import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

from compass_agent.api.sessions import events
from compass_agent.runtime.durable_service import DurableRuntime
from compass_agent.schemas import TrustedContext


def test_progress_is_published_promptly_and_failed_flush_is_retried():
    async def scenario():
        published = asyncio.Event()
        attempts = 0
        sink = SimpleNamespace(pending=["event"])

        async def flush():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary failure")
            sink.pending.clear()
            published.set()

        sink.flush = flush
        service = SimpleNamespace(closing=False, owner_healthy=True, sinks={"run": sink})
        task = asyncio.create_task(DurableRuntime.publish_progress(cast(DurableRuntime, service)))
        try:
            await asyncio.wait_for(published.wait(), 0.3)
            assert attempts == 2
        finally:
            service.closing = True
            await task

    asyncio.run(scenario())


def test_idle_sse_delivers_new_packet_without_half_second_wait():
    async def scenario():
        run_id, session_id, request_id, message_id = (uuid4() for _ in range(4))
        run = SimpleNamespace(
            session_id=session_id,
            config_snapshot={},
            projected_seq="1-0",
            public_view={"run": {"status": "completed"}},
        )
        packet = {
            "packetId": str(uuid4()),
            "runId": str(run_id),
            "requestId": str(request_id),
            "seq": None,
            "occurredAt": "2026-11-14T00:00:00Z",
            "event": {
                "type": "message.delta",
                "payload": {"messageId": str(message_id), "revision": 2, "text": "新增内容"},
            },
        }
        service = SimpleNamespace(
            access=SimpleNamespace(visibility=AsyncMock(return_value={"details": True})),
            store=SimpleNamespace(read_run=lambda *_: run, pending=lambda *_: []),
            streams=SimpleNamespace(
                validate=AsyncMock(),
                rows=AsyncMock(side_effect=[[], [("1-0", {"packet": packet})]]),
                confirmed={run_id: "1-0"},
            ),
        )
        response = await events(
            run_id, cast(TrustedContext, object()), cast(DurableRuntime, service)
        )

        async def collect():
            return [frame async for frame in response.body_iterator]

        frames = await asyncio.wait_for(collect(), 0.3)
        data = next(
            frame for frame in frames if isinstance(frame, bytes) and frame.startswith(b"id:")
        )
        value = json.loads(data.decode().split("data: ", 1)[1])
        assert value["packets"][0]["event"]["payload"]["text"] == "新增内容"
        assert value["cursors"] == [{"runId": str(run_id), "seq": "1-0"}]

    asyncio.run(scenario())
