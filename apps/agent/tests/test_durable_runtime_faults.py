"""Real Redis AOF/process faults and PostgreSQL owner loss in isolated resources."""

import asyncio
import os
import socket
import subprocess
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from compass_agent.models.errors import ModelError
from compass_agent.runtime.control import RunRegistry
from compass_agent.runtime.durable_service import DurableRuntime
from compass_agent.runtime.entities import AgentOutbox
from compass_agent.settings import Settings
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from .test_database_tools_integration import db
from .test_database_tools_integration import targets as targets
from .test_durable_runtime_integration import CHAT, command, prepare

# Keep the verified Compose image as the single image reference.
IMAGE = (
    "public.ecr.aws/docker/library/redis:8.2.11-alpine@sha256:"
    "fe9daae35cd4fd6ec22416aa4fe0cf6b3e36741e06ff45b9986253fe2a81c041"
)


def docker(*args):
    return subprocess.check_output(
        ["docker", *args], text=True, stderr=subprocess.PIPE, timeout=60
    ).strip()


@pytest.fixture
def isolated_redis():
    name = "compass-l19-fault-" + uuid4().hex[:12]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--label",
        "compass.test=lesson19",
        "-p",
        f"127.0.0.1:{port}:6379",
        IMAGE,
        "redis-server",
        "--appendonly",
        "yes",
        "--appendfsync",
        "always",
        "--maxmemory-policy",
        "noeviction",
    )
    try:
        port = docker("port", name, "6379/tcp").rsplit(":", 1)[1]
        yield name, "redis://127.0.0.1:" + port + "/0"
    finally:
        docker("rm", "-f", "-v", name)


def test_aof_sigkill_oom_and_owner_connection_loss_stop_dispatch_and_recover(
    targets, isolated_redis
):
    name, url = isolated_redis
    engine = prepare(targets)
    settings = Settings(compass_redis_url=url, compass_redis_password=SecretStr(""))

    async def ready(runtime):
        async with asyncio.timeout(12):
            while not runtime.store.ready:
                await asyncio.sleep(0.05)

    async def scenario():
        runtime = DurableRuntime(engine, settings, RunRegistry())
        replacement = None
        await runtime.start()
        try:
            await ready(runtime)
            receipt = runtime.store.submit(command(), CHAT)
            identifier = UUID(receipt["runId"])
            await runtime.publisher.flush(identifier)
            before = await runtime.streams.rows(identifier)
            assert before and runtime.store.read_run(identifier).projected_seq == before[-1][0]
            # AOF survives a hard process exit without changing original packet/stream IDs.
            await asyncio.to_thread(docker, "kill", "--signal", "KILL", name)
            with pytest.raises(ModelError):
                await runtime.streams.ready()
            await asyncio.to_thread(docker, "start", name)
            async with asyncio.timeout(12):
                while True:
                    try:
                        await runtime.streams.ready()
                        break
                    except ModelError:
                        await asyncio.sleep(0.1)
            assert await runtime.streams.rows(identifier) == before
            await ready(runtime)
            # SQL acceptance stays durable while Redis refuses new writes.
            await runtime.streams.writer.config_set("maxmemory", 1)
            second = runtime.store.submit(command(), CHAT)
            second_id = UUID(second["runId"])
            with pytest.raises(ModelError):
                await runtime.publisher.flush(second_id)
            row = runtime.store.read_run(second_id)
            assert row.status == "created" and row.started_at is None and row.projected_seq == "0-0"
            with Session(engine) as tx:
                pending = tx.scalars(
                    select(AgentOutbox).where(AgentOutbox.run_id == second_id)
                ).all()
                assert pending and all(item.delivered_at is None for item in pending)
            await runtime.streams.writer.config_set("maxmemory", 0)
            await ready(runtime)
            await runtime.publisher.flush(second_id)
            assert runtime.store.read_run(second_id).projected_seq != "0-0"
            # A second owner cannot publish or dispatch while the original lock is alive.
            replacement = DurableRuntime(engine, settings, RunRegistry())
            await replacement.start()
            assert not replacement.store.ready
            with pytest.raises(ModelError, match="runtime_owner_unavailable"):
                replacement.require_owner()
            assert runtime.ticker is not None
            runtime.ticker.cancel()
            with suppress(asyncio.CancelledError):
                await runtime.ticker
            assert runtime.owner is not None
            owner_pid = runtime.owner.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            with psycopg.connect(
                db.dsn(os.environ["AGENT_TEST_ADMIN_URL"]), autocommit=True
            ) as admin:
                assert admin.execute(
                    "SELECT pg_terminate_backend(%s)", (owner_pid,)
                ).fetchone() == (True,)
            # The failed heartbeat fences the old owner before it can regain the lock.
            runtime.ticker = asyncio.create_task(runtime.maintain())
            async with asyncio.timeout(5):
                while runtime.owner_healthy:
                    await asyncio.sleep(0.01)
            with pytest.raises(ModelError, match="runtime_owner_unavailable"):
                runtime.require_owner()
            runtime.ticker.cancel()
            with suppress(asyncio.CancelledError):
                await runtime.ticker
            await ready(replacement)
            replacement.require_owner()
            recovered = await replacement.streams.rows(identifier)
            assert recovered[: len(before)] == before
            assert replacement.store.read_run(identifier).status == "paused"
        finally:
            await runtime.close()
            if replacement:
                await replacement.close()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


@pytest.mark.parametrize("phase", ["dispatched", "partial"])
def test_model_process_kill_preserves_unknown_attempt_reservation_and_partial_text(
    targets, tmp_path, phase
):
    import sys
    import time

    import httpx
    from compass_agent.models.entities import ModelCall
    from compass_agent.models.repository import ModelRepository
    from compass_agent.models.secrets import SecretStore
    from compass_agent.runtime.entities import AgentMessage, AgentRun, ModelAttempt
    from sqlalchemy import func

    from .test_agent_run import auth_headers
    from .test_models import ORIGINS, create

    engine = prepare(targets)
    repo = ModelRepository(engine, SecretStore(os.environ["COMPASS_MODEL_ENCRYPTION_KEY"]), ORIGINS)
    _, provider = create(repo, catalog_id="deepseek/deepseek-flash", api_model="deepseek-flash")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parent),
        "COMPASS_AGENT_SERVICE_TOKEN": "run-service-token",
        "COMPASS_TEST_REAL_MODEL": "true",
        # Managed Runs deliberately keep model deltas private. The partial-stream
        # recovery case covers the older public streaming protocol.
        "COMPASS_CONTEXT_RUNTIME_ENABLED": "false" if phase == "partial" else "true",
        "COMPASS_WORKFLOW_E2E_RETAIL_DSN": "",
        "COMPASS_CONTROLLED_AUTONOMY_ENABLED": "false",
        "COMPASS_HYBRID_WORKFLOW_ENABLED": "false",
    }
    child = None
    log_path = tmp_path / f"model-{phase}.log"
    log = log_path.open("w")

    def start():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "apps.agent.tests.durable_process_fixture:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--timeout-graceful-shutdown",
                "2",
            ],
            env=env,
            cwd=Path(__file__).resolve().parents[3],
            stdout=log,
            stderr=log,
        )
        for _ in range(120):
            assert process.poll() is None, log_path.read_text()
            try:
                if httpx.get(origin + "/readyz", timeout=1).status_code == 200:
                    return process
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        process.terminate()
        process.wait(timeout=8)
        pytest.fail("model crash fixture readiness timed out")

    try:
        child = start()
        with httpx.Client(base_url=origin, headers=auth_headers(CHAT), timeout=10) as client:
            submitted = client.post(
                "/v1/analysis/sessions/submit",
                json=command(
                    question="你好呀，讲个笑话 E2E_SLOW",
                    selection={"mode": "fixed", "provider_id": str(provider.provider_id)},
                ).model_dump(mode="json", by_alias=True),
            )
            assert submitted.status_code == 202, submitted.text
            accepted = submitted.json()
            run_id = UUID(accepted["runId"])
            partial = ""
            for _ in range(300):
                with Session(engine) as tx:
                    physical = tx.scalar(
                        select(ModelAttempt)
                        .join(ModelCall, ModelCall.call_id == ModelAttempt.call_id)
                        .where(ModelCall.run_id == run_id, ModelAttempt.status == "dispatched")
                    )
                    message = tx.scalar(
                        select(AgentMessage).where(
                            AgentMessage.run_id == run_id,
                            AgentMessage.role == "assistant",
                            AgentMessage.channel == "conversation",
                        )
                    )
                    partial = (message.public_content or {}).get("text", "") if message else ""
                    if physical and (phase == "dispatched" or partial):
                        attempt_id = physical.id
                        break
                time.sleep(0.025)
            else:
                pytest.fail("model never reached requested crash boundary: " + log_path.read_text())
            child.kill()
            assert child.wait(timeout=10) != 0
            with Session(engine) as tx:
                before_count = tx.scalar(
                    select(func.count())
                    .select_from(ModelAttempt)
                    .join(ModelCall, ModelCall.call_id == ModelAttempt.call_id)
                    .where(ModelCall.run_id == run_id)
                )
                attempt = tx.get(ModelAttempt, attempt_id)
                assert attempt and attempt.origin == "live" and attempt.sent
                assert attempt.outcome_unknown and attempt.reserved_tokens > 0
                assert attempt.actual_cost_microusd is None and attempt.usage == {}
                assert attempt.output_content is None
            child = start()
            snapshot = client.get(f"/v1/analysis/sessions/{accepted['sessionId']}/messages")
            assert snapshot.status_code == 200
            with Session(engine) as tx:
                run = tx.get(AgentRun, run_id)
                assert run and run.status == "paused" and run.recovery_status == "required"
                version = run.version
                if phase == "partial":
                    message = tx.scalars(
                        select(AgentMessage).where(
                            AgentMessage.run_id == run_id,
                            AgentMessage.role == "assistant",
                            AgentMessage.channel == "conversation",
                        )
                    ).one()
                    assert message.public_content is not None
                    assert partial and message.public_content["text"].startswith(partial)
                    assert message.status != "completed"
            resumed = client.post(
                f"/v1/analysis/runs/{run_id}/resume",
                json={
                    "requestId": str(uuid4()),
                    "expectedVersion": version,
                },
            )
            assert (
                resumed.status_code == 409
                and resumed.json()["error"]["code"] == "execution_unknown"
            )
            with Session(engine) as tx:
                assert (
                    tx.scalar(
                        select(func.count())
                        .select_from(ModelAttempt)
                        .join(ModelCall, ModelCall.call_id == ModelAttempt.call_id)
                        .where(ModelCall.run_id == run_id)
                    )
                    == before_count
                )
                attempt = tx.get(ModelAttempt, attempt_id)
                assert attempt is not None and attempt.outcome_unknown
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        log.close()
        engine.dispose()
