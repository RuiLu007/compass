"""One HTTP-only execution slot with independent lease renewal and bounded shutdown."""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID, uuid4

import httpx

logger = logging.getLogger("compass_worker")


class LeaseLost(Exception):
    """The consumer must stop sending advancement commands for this grant."""


@dataclass(frozen=True)
class ConsumerSettings:
    agent_url: str = "http://127.0.0.1:8102"
    token: str = field(default="", repr=False)
    worker_id: str = "worker-1"
    lease_seconds: float = 15
    heartbeat_seconds: float = 3
    poll_seconds: float = 0.25
    shutdown_seconds: float = 8

    @classmethod
    def from_env(cls):
        result = cls(
            agent_url=os.getenv("AGENT_API_URL", "http://127.0.0.1:8102").rstrip("/"),
            token=os.getenv("COMPASS_WORKER_TOKEN", ""),
            worker_id=os.getenv("COMPASS_WORKER_ID", "worker-1"),
            lease_seconds=float(os.getenv("COMPASS_RUNTIME_LEASE_SECONDS", "15")),
        )
        if not result.token or not result.worker_id or not 3 <= result.lease_seconds <= 300:
            raise ValueError("Worker identity, token and lease configuration are required")
        return result


class RunConsumer:
    def __init__(self, settings: ConsumerSettings, *, transport=None):
        self.settings = settings
        self.boot_id = str(uuid4())
        self.client = httpx.AsyncClient(
            base_url=settings.agent_url,
            headers={"Authorization": "Bearer " + settings.token},
            timeout=4,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )
        self.grant = None
        self.deadline = 0.0
        self.claims = 0
        self.release_requested = False

    async def close(self):
        await self.client.aclose()

    async def request(self, operation, body):
        response = await self.client.post("/internal/runtime/jobs/" + operation, json=body)
        if response.status_code in {401, 403, 409}:
            raise LeaseLost()
        response.raise_for_status()
        return None if response.status_code == 204 else response.json()

    def accept_grant(self, value, sent):
        if (
            not isinstance(value, dict)
            or value.get("workerId") != self.settings.worker_id
            or value.get("bootId") != self.boot_id
        ):
            raise LeaseLost()
        for key in ("runId", "epoch"):
            UUID(value[key])
        if type(value.get("checkpointNo")) is not int or value["checkpointNo"] < 0:
            raise LeaseLost()
        if self.grant is not None and any(
            value[name] != self.grant[name] for name in ("runId", "epoch")
        ):
            raise LeaseLost()
        self.grant = value
        # Starting at send time never adds the HTTP round-trip to the server lease.
        self.deadline = sent + self.settings.lease_seconds - 0.5

    async def wait(self, event, seconds):
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0, seconds))
        except TimeoutError:
            pass

    async def heartbeat(self, finished):
        while not finished.is_set():
            await self.wait(
                finished, min(self.settings.heartbeat_seconds, self.settings.lease_seconds / 3)
            )
            if finished.is_set():
                return
            if monotonic() >= self.deadline:
                raise LeaseLost()
            sent = monotonic()
            try:
                renewed = await self.request("heartbeat", {"grant": self.grant})
                if monotonic() >= self.deadline:
                    raise LeaseLost()
                self.accept_grant(renewed, sent)
            except (httpx.HTTPError, ValueError, KeyError):
                if monotonic() >= self.deadline:
                    raise LeaseLost() from None

    async def drive(self, stop, finished):
        assert self.grant is not None
        checkpoint = self.grant["checkpointNo"]
        shutdown_at = None
        while not finished.is_set():
            if monotonic() >= self.deadline:
                raise LeaseLost()
            if stop.is_set() and shutdown_at is None:
                shutdown_at = monotonic() + self.settings.shutdown_seconds
            if shutdown_at is not None and monotonic() >= shutdown_at:
                return
            try:
                ack = await self.request(
                    "advance", {"grant": self.grant, "expectedCheckpoint": checkpoint}
                )
                if not isinstance(ack, dict) or ack.get("status") not in {
                    "running",
                    "continue",
                    "parked",
                    "done",
                    "interrupted",
                }:
                    raise LeaseLost()
                if ack["status"] in {"parked", "done", "interrupted"}:
                    return
                if ack["status"] == "continue":
                    if (
                        type(ack.get("checkpointNo")) is not int
                        or ack["checkpointNo"] <= checkpoint
                    ):
                        raise LeaseLost()
                    checkpoint = ack["checkpointNo"]
                    if stop.is_set():
                        self.release_requested = True
                        return
                await self.wait(finished, self.settings.poll_seconds)
            except (httpx.HTTPError, ValueError, KeyError):
                # Retry the same server-derived boundary identity after transport failure.
                await self.wait(finished, self.settings.poll_seconds)

    async def consume(self, stop):
        finished = asyncio.Event()
        self.release_requested = False
        heartbeat = asyncio.create_task(self.heartbeat(finished))
        drive = asyncio.create_task(self.drive(stop, finished))
        try:
            done, _ = await asyncio.wait((heartbeat, drive), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            finished.set()
            for task in (heartbeat, drive):
                if not task.done():
                    task.cancel()
            await asyncio.gather(heartbeat, drive, return_exceptions=True)
            try:
                if self.release_requested:
                    await self.request("release", {"grant": self.grant})
            finally:
                self.grant = None

    async def run(self, stop):
        logger.info(
            "Worker consumer started; worker=%s boot=%s", self.settings.worker_id, self.boot_id
        )
        previous = None
        while not stop.is_set():
            try:
                sent = monotonic()
                grant = await self.request("claim", {"bootId": self.boot_id})
                if grant is None:
                    if previous != "idle":
                        logger.info("Worker queue idle")
                    previous = "idle"
                    await self.wait(stop, 1)
                    continue
                self.accept_grant(grant, sent)
                if stop.is_set():
                    await self.request("release", {"grant": self.grant})
                    self.grant = None
                    return
                self.claims += 1
                previous = "claimed"
                logger.info("Worker claimed run=%s epoch=%s", grant["runId"], grant["epoch"])
                await self.consume(stop)
            except LeaseLost:
                logger.info("Worker lease unavailable; advancement stopped")
                self.grant = None
                await self.wait(stop, 1)
            except (httpx.HTTPError, ValueError, KeyError):
                if previous != "unavailable":
                    logger.warning("Worker Agent dependency unavailable")
                previous = "unavailable"
                await self.wait(stop, 1)
