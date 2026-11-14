"""Exercise the HTTP consumer using isolated transports and controlled async boundaries."""

import asyncio
import json
import logging
from dataclasses import replace
from typing import Any
from uuid import UUID

import httpx
import pytest
from compass_worker import run_consumer
from compass_worker.run_consumer import ConsumerSettings, LeaseLost, RunConsumer

RUN_ID = "00000000-0000-4000-8000-000000000001"
EPOCH = "00000000-0000-4000-8000-000000000002"
OTHER_ID = "00000000-0000-4000-8000-000000000003"
TOKEN = "isolated-worker-secret-not-for-logs"
SETTINGS = ConsumerSettings(
    agent_url="http://agent.invalid",
    token=TOKEN,
    worker_id="worker-test",
    heartbeat_seconds=0.002,
    poll_seconds=0.001,
    shutdown_seconds=0.2,
)


def grant(consumer: RunConsumer, **changes: Any) -> dict[str, Any]:
    return {
        "runId": RUN_ID,
        "workerId": SETTINGS.worker_id,
        "bootId": consumer.boot_id,
        "epoch": EPOCH,
        "expiresAt": "2026-11-12T01:00:15Z",
        "checkpointNo": 3,
        **changes,
    }


def request_data(request: httpx.Request) -> tuple[str, dict[str, Any]]:
    assert request.headers["Authorization"] == "Bearer " + TOKEN
    assert request.url.host == "agent.invalid"
    return request.url.path.rsplit("/", 1)[-1], json.loads(request.content)


def ack(status: str, checkpoint: int = 3) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "commandId": "00000000-0000-4000-8000-000000000004",
            "status": status,
            "checkpointNo": checkpoint,
            "reasonCode": None,
        },
    )


def test_transport_timeout_retries_original_boundary_until_continue() -> None:
    async def scenario() -> None:
        advances: list[dict[str, Any]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, body = request_data(request)
            assert operation == "advance"
            advances.append(body)
            if len(advances) == 1:
                raise httpx.ReadTimeout("Response was lost after admission", request=request)
            if len(advances) == 2:
                return ack("running")
            if len(advances) == 3:
                return ack("continue", 4)
            return ack("done", 4)

        consumer = RunConsumer(
            replace(SETTINGS, heartbeat_seconds=30), transport=httpx.MockTransport(handler)
        )
        original = grant(consumer)
        consumer.accept_grant(original, run_consumer.monotonic())
        try:
            await asyncio.wait_for(consumer.consume(asyncio.Event()), timeout=1)
        finally:
            await consumer.close()
        assert [body["expectedCheckpoint"] for body in advances] == [3, 3, 3, 4]
        assert all(body["grant"] == original for body in advances)
        assert consumer.grant is None

    asyncio.run(scenario())


def test_renewed_expiry_does_not_change_pending_boundary() -> None:
    async def scenario() -> None:
        first_advance = asyncio.Event()
        renewal_returned = asyncio.Event()
        advances: list[dict[str, Any]] = []
        renewed_expiry = "2026-11-12T01:00:18Z"

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, body = request_data(request)
            if operation == "heartbeat":
                await first_advance.wait()
                renewal_returned.set()
                return httpx.Response(200, json=grant(consumer, expiresAt=renewed_expiry))
            assert operation == "advance"
            advances.append(body)
            if len(advances) == 1:
                first_advance.set()
                await renewal_returned.wait()
                while True:
                    current = consumer.grant
                    assert current is not None
                    if current["expiresAt"] == renewed_expiry:
                        break
                    await asyncio.sleep(0)
                raise httpx.ReadTimeout("Advance receipt unavailable", request=request)
            return ack("done")

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        consumer.accept_grant(grant(consumer), run_consumer.monotonic())
        try:
            await asyncio.wait_for(consumer.consume(asyncio.Event()), timeout=1)
        finally:
            await consumer.close()
        assert [body["expectedCheckpoint"] for body in advances] == [3, 3]
        assert advances[0]["grant"]["expiresAt"] != advances[1]["grant"]["expiresAt"]
        for key in ("runId", "workerId", "bootId", "epoch"):
            assert advances[0]["grant"][key] == advances[1]["grant"][key]

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 403, 409])
def test_heartbeat_lease_loss_cancels_inflight_advance(status: int) -> None:
    async def scenario() -> None:
        advance_started = asyncio.Event()
        advance_canceled = asyncio.Event()
        operations: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, _ = request_data(request)
            operations.append(operation)
            if operation == "advance":
                advance_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    advance_canceled.set()
            assert operation == "heartbeat"
            await advance_started.wait()
            return httpx.Response(status, json={"code": "lease_lost"})

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        consumer.accept_grant(grant(consumer), run_consumer.monotonic())
        try:
            with pytest.raises(LeaseLost):
                await asyncio.wait_for(consumer.consume(asyncio.Event()), timeout=1)
        finally:
            await consumer.close()
        assert advance_canceled.is_set()
        assert operations.count("advance") == 1
        assert consumer.grant is None

    asyncio.run(scenario())


def test_network_failure_stops_advancement_at_conservative_deadline(monkeypatch) -> None:
    async def scenario() -> None:
        clock = [100.0]
        monkeypatch.setattr(run_consumer, "monotonic", lambda: clock[0])
        advance_times: list[float] = []
        heartbeat_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal heartbeat_count
            operation, _ = request_data(request)
            if operation == "heartbeat":
                heartbeat_count += 1
                clock[0] = 113.0 if heartbeat_count == 1 else 115.0
                raise httpx.ConnectError("Isolated connection failure", request=request)
            assert operation == "advance"
            advance_times.append(clock[0])
            return ack("running")

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        consumer.accept_grant(grant(consumer), clock[0])
        deadline = consumer.deadline
        try:
            with pytest.raises(LeaseLost):
                await asyncio.wait_for(consumer.consume(asyncio.Event()), timeout=1)
        finally:
            await consumer.close()
        assert heartbeat_count == 2
        assert advance_times
        assert all(sent < deadline for sent in advance_times)
        assert consumer.grant is None

    asyncio.run(scenario())


def test_slow_claim_response_does_not_extend_server_lease(monkeypatch) -> None:
    async def scenario() -> None:
        clock = [100.0]
        monkeypatch.setattr(run_consumer, "monotonic", lambda: clock[0])
        operations: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, _ = request_data(request)
            operations.append(operation)
            assert operation == "claim"
            clock[0] = 116.0
            return httpx.Response(200, json=grant(consumer))

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        sent = clock[0]
        claimed = await consumer.request("claim", {"bootId": consumer.boot_id})
        consumer.accept_grant(claimed, sent)
        try:
            with pytest.raises(LeaseLost):
                await asyncio.wait_for(consumer.consume(asyncio.Event()), timeout=1)
        finally:
            await consumer.close()
        assert operations == ["claim"]
        assert consumer.deadline < clock[0]

    asyncio.run(scenario())


def test_heartbeat_success_after_local_deadline_does_not_revive_lease(monkeypatch) -> None:
    async def scenario() -> None:
        clock = [100.0]
        monkeypatch.setattr(run_consumer, "monotonic", lambda: clock[0])
        heartbeat_returned = asyncio.Event()
        advances: list[dict[str, Any]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, body = request_data(request)
            if operation == "heartbeat":
                # Sent at 101, received at 115: after the original 114.5 deadline,
                # but before a replacement deadline computed from this send time.
                assert clock[0] == 101.0
                clock[0] = 115.0
                heartbeat_returned.set()
                return httpx.Response(200, json=grant(consumer, expiresAt="2026-11-12T01:00:29Z"))
            assert operation == "advance"
            advances.append(body)
            if len(advances) == 1:
                clock[0] = 101.0
                await heartbeat_returned.wait()
                await asyncio.sleep(0)
                return ack("running")
            return ack("done")

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        consumer.accept_grant(grant(consumer), clock[0])
        original_deadline = consumer.deadline
        try:
            with pytest.raises(LeaseLost):
                await asyncio.wait_for(consumer.consume(asyncio.Event()), timeout=1)
        finally:
            await consumer.close()
        assert clock[0] > original_deadline
        assert len(advances) == 1
        assert consumer.grant is None

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["workerId", "bootId"])
def test_claim_rejects_another_worker_or_process(field: str) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        operations: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, body = request_data(request)
            operations.append(operation)
            assert body == {"bootId": consumer.boot_id}
            stop.set()
            return httpx.Response(200, json=grant(consumer, **{field: "different-identity"}))

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        try:
            await asyncio.wait_for(consumer.run(stop), timeout=1)
        finally:
            await consumer.close()
        assert operations == ["claim"]
        assert consumer.claims == 0
        assert consumer.grant is None

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["workerId", "bootId", "runId", "epoch"])
def test_heartbeat_cannot_replace_active_execution_identity(field: str) -> None:
    async def scenario() -> None:
        heartbeat_returned = asyncio.Event()
        advances: list[dict[str, Any]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, body = request_data(request)
            if operation == "heartbeat":
                heartbeat_returned.set()
                return httpx.Response(200, json=grant(consumer, **{field: OTHER_ID}))
            assert operation == "advance"
            advances.append(body)
            if len(advances) == 1:
                await heartbeat_returned.wait()
                await asyncio.sleep(0)
                return ack("running")
            return ack("done")

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        original = grant(consumer)
        consumer.accept_grant(original, run_consumer.monotonic())
        try:
            with pytest.raises(LeaseLost):
                await asyncio.wait_for(consumer.consume(asyncio.Event()), timeout=1)
        finally:
            await consumer.close()
        assert len(advances) == 1
        assert advances[0]["grant"] == original

    asyncio.run(scenario())


def test_advance_lease_loss_stops_without_retry_or_release() -> None:
    async def scenario() -> None:
        operations: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, _ = request_data(request)
            operations.append(operation)
            return httpx.Response(409, json={"code": "lease_lost"})

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        consumer.accept_grant(grant(consumer), run_consumer.monotonic())
        try:
            with pytest.raises(LeaseLost):
                await asyncio.wait_for(consumer.consume(asyncio.Event()), timeout=1)
        finally:
            await consumer.close()
        assert operations == ["advance"]

    asyncio.run(scenario())


def test_stop_drains_current_boundary_with_heartbeat_then_releases_once() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        renewed = asyncio.Event()
        operations: list[str] = []
        advances: list[dict[str, Any]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, body = request_data(request)
            operations.append(operation)
            if operation == "claim":
                return httpx.Response(200, json=grant(consumer))
            if operation == "heartbeat":
                assert stop.is_set()
                renewed.set()
                return httpx.Response(200, json=grant(consumer))
            if operation == "release":
                assert stop.is_set()
                return httpx.Response(204)
            assert operation == "advance"
            advances.append(body)
            if len(advances) == 1:
                stop.set()
                await renewed.wait()
                return ack("running")
            return ack("continue", 4)

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        try:
            await asyncio.wait_for(consumer.run(stop), timeout=1)
        finally:
            await consumer.close()
        assert operations.count("claim") == 1
        assert operations.count("heartbeat") >= 1
        assert operations.count("release") == 1
        assert operations[-1] == "release"
        assert [body["expectedCheckpoint"] for body in advances] == [3, 3]
        assert consumer.grant is None

    asyncio.run(scenario())


def test_stop_while_queue_idle_prevents_another_claim() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        operations: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, _ = request_data(request)
            operations.append(operation)
            stop.set()
            return httpx.Response(204)

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        try:
            await asyncio.wait_for(consumer.run(stop), timeout=1)
        finally:
            await consumer.close()
        assert operations == ["claim"]
        assert consumer.claims == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("checkpoint", [3, 2, -1, True, "4", None])
def test_continue_requires_a_strictly_new_integer_checkpoint(checkpoint: Any) -> None:
    async def scenario() -> None:
        operations: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, _ = request_data(request)
            operations.append(operation)
            return ack("continue", checkpoint)

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        consumer.accept_grant(grant(consumer), run_consumer.monotonic())
        try:
            with pytest.raises(LeaseLost):
                await asyncio.wait_for(consumer.consume(asyncio.Event()), timeout=1)
        finally:
            await consumer.close()
        assert operations == ["advance"]

    asyncio.run(scenario())


def test_dependency_error_logs_and_settings_repr_keep_token_private(caplog) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            operation, _ = request_data(request)
            assert operation == "claim"
            stop.set()
            raise httpx.ConnectError("Sensitive upstream detail: " + TOKEN, request=request)

        consumer = RunConsumer(SETTINGS, transport=httpx.MockTransport(handler))
        try:
            with caplog.at_level(logging.INFO, logger="compass_worker"):
                await asyncio.wait_for(consumer.run(stop), timeout=1)
        finally:
            await consumer.close()
        assert "Worker Agent dependency unavailable" in caplog.text
        assert TOKEN not in caplog.text
        assert TOKEN not in repr(SETTINGS)

    asyncio.run(scenario())


def test_each_consumer_process_instance_has_a_fresh_boot_id() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(204))
        first = RunConsumer(SETTINGS, transport=transport)
        second = RunConsumer(SETTINGS, transport=transport)
        try:
            assert first.boot_id != second.boot_id
            assert UUID(first.boot_id).version == 4
            assert UUID(second.boot_id).version == 4
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())
