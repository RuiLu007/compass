"""Explicit real Langfuse + PostgreSQL acceptance; see observability/README.md.

Run this file explicitly. Normal unit tests do not require Docker or project keys.
"""

import asyncio
import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from compass_agent.observability.observer import Observer
from compass_agent.observability.summary import model_comparison
from compass_agent.schemas import ModelUsage
from compass_agent.settings import Settings

from .test_models import CHAT, MANAGE, TEST, headers

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def emit_gold(settings_values, ids, carrier, result):
    """Separate process, message carrier and native OTLP export to the same project."""
    settings = Settings(**settings_values)
    owner = Observer(settings)
    context = SimpleNamespace(purpose="chat", actor_user_id=uuid4(), scope=SimpleNamespace())
    root = owner.root(uuid4(), context, carrier)
    pending = []
    for model_id in ids:
        for status in ("completed", "failed", "canceled", "incomplete"):
            logical = root.child(
                "compass.logical", stage="inference", provider_id=model_id, model="gold"
            )
            pending.append((logical, status))
            states = ["failed", "completed"] if status == "completed" else [status]
            if status == "canceled":
                states = []
            for attempt_status in states:
                attempt = logical.child("compass.attempt", attempt_id=str(uuid4()))
                known = attempt_status == "completed"
                if known:
                    attempt.accept_cost(0.01)
                attempt.finish_attempt(
                    SimpleNamespace(
                        adapter_started=True,
                        status=attempt_status,
                        error=None,
                        upstream_sent=True,
                        upstream_outcome_unknown=not known,
                        cleanup_confirmed=True,
                        requested_model="gold",
                        usage=ModelUsage(input_tokens=12, output_tokens=8)
                        if known
                        else ModelUsage(),
                    )
                )
        # Same model in a classifier stage must not inflate inference totals.
        classifier = root.child(
            "compass.logical", stage="intent_flash", provider_id=model_id, model="gold"
        )
        classifier.complete("completed")
        classifier.end()
    time.sleep(1)
    for logical, status in pending:
        logical.complete(status)
        logical.end()
    root.end()
    assert owner.provider is not None
    owner.provider.force_flush()
    result.send({"headers": root.headers(), "status": owner.status()})
    owner.shutdown()
    result.close()


@pytest.fixture(scope="module")
def settings():
    value = Settings()
    assert value.compass_observability_enabled and value.compass_langfuse_base_url
    assert os.environ["AGENT_TEST_DATABASE_URL"].split("/")[-1].startswith("compass_agent_test_")
    return value


def wait_for(check, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("acceptance condition timed out")


def observations(settings, trace_id):
    with httpx.Client(
        base_url=settings.compass_langfuse_base_url,
        auth=(
            settings.compass_langfuse_public_key,
            settings.compass_langfuse_secret_key.get_secret_value(),
        ),
        timeout=5,
    ) as client:
        params = {
            "traceId": trace_id,
            "limit": "100",
            "fields": "core,basic,metadata,usage,model,io",
            "expandMetadata": ",".join(
                ("prompts_used", "tools_selected", "skills_selected", "capabilities_excluded")
            ),
        }
        records = []
        while True:
            response = client.get("/api/public/v2/observations", params=params)
            assert response.status_code == 200, response.text[:500]
            page = response.json()
            records.extend(page["data"])
            cursor = page["meta"].get("cursor")
            if not cursor:
                return records
            assert cursor != params.get("cursor"), "Langfuse repeated its observation cursor"
            params["cursor"] = cursor


def test_two_process_gold_aggregates_usage_latency_and_message_parent(settings):
    ids = [str(uuid4()), str(uuid4())]
    source = Observer(settings)
    root = source.root(uuid4(), MANAGE)
    trace_id = root.headers()["X-Compass-Trace-Id"]
    settings_values = settings.model_dump()
    workers, channels = [], []
    try:
        for index in range(2):
            parent, child = multiprocessing.Pipe(duplex=False)
            process = multiprocessing.get_context("spawn").Process(
                target=emit_gold,
                args=(
                    {**settings_values, "compass_observability_instance_id": f"gold-{index}"},
                    ids,
                    root.carrier(),
                    child,
                ),
            )
            process.start()
            child.close()
            workers.append(process)
            channels.append(parent)
        for process, channel in zip(workers, channels, strict=True):
            assert channel.poll(30)
            value = channel.recv()
            assert value["headers"]["X-Compass-Trace-Id"] == trace_id
            assert value["status"]["last_export"] == "success"
            process.join(10)
            assert process.exitcode == 0
        root.end()
        assert source.provider is not None
        source.provider.force_flush()
        models = [SimpleNamespace(id=UUID(key), api_model="gold") for key in ids]

        def check():
            value = asyncio.run(model_comparison(settings, models, "1h"))
            return value if all(x.get("logical_count") == 8 for x in value["items"]) else None

        value = wait_for(check)
        for row in value["items"]:
            assert row["completed"] == row["failed"] == row["canceled"] == row["incomplete"] == 2
            assert row["attempt_count"] == 8
            assert row["known_input_tokens"] == 24 and row["known_output_tokens"] == 16
            assert row["unknown_usage_attempts"] == row["unknown_cost_attempts"] == 6
            assert row["estimated_usd"] is None and row["known_estimated_usd"] == pytest.approx(
                0.02
            )
            assert 950 < row["mean_duration_ms"] < 1200
        records = wait_for(lambda: r if len(r := observations(settings, trace_id)) >= 39 else None)
        assert root.span is not None
        parent_id = f"{root.span.get_span_context().span_id:016x}"
        children = [
            r for r in records if r["name"] == "compass.request" and r["parentObservationId"]
        ]
        assert len(children) == 2 and all(r["parentObservationId"] == parent_id for r in children)
        assert {r["metadata"]["instance_id"] for r in children} == {"gold-0", "gold-1"}
    finally:
        root.end()
        source.shutdown()
        for process in workers:
            if process.is_alive():
                process.terminate()
                process.join(5)


@pytest.fixture(scope="module")
def agent_hosts(settings):
    root = Path(__file__).resolve().parents[3]
    hosts = []
    logs = []
    try:
        for index, ratio in enumerate([0, 1]):
            port = 8014 + index
            logfile = (root / f"work/observability/agent-{index}.log").open("w+")
            env = {
                **os.environ,
                "DATABASE_URL": os.environ["AGENT_TEST_DATABASE_URL"],
                "PYTHONPATH": str(root / "apps/agent/tests"),
                "COMPASS_OBSERVABILITY_INSTANCE_ID": f"http-{index}",
                "COMPASS_OBSERVABILITY_SAMPLE_RATIO": str(ratio),
                "COMPASS_OBSERVABILITY_CAPTURE_CONTENT": "false",
                "COMPASS_ROUTING_MODE": "flash_only",
            }
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "e2e_app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=root,
                env=env,
                stdout=logfile,
                stderr=subprocess.STDOUT,
            )
            hosts.append((process, f"http://127.0.0.1:{port}"))
            logs.append(logfile)

            def ready(process=process, port=port):
                assert process.poll() is None, "Agent test host exited"
                try:
                    return httpx.get(f"http://127.0.0.1:{port}/readyz").is_success
                except httpx.HTTPError:
                    return False

            wait_for(ready)
        yield [host for _, host in hosts]
    finally:
        for process, _ in hosts:
            process.terminate()
        for process, _ in hosts:
            process.wait(timeout=35)
        for logfile in logs:
            logfile.close()


def test_authenticated_http_parent_sampling_privacy_and_platform_access(settings, agent_hosts):
    prefix = "OBS_SENTINEL_" + uuid4().hex
    token_headers = headers()
    token_headers["Authorization"] = "Bearer " + settings.compass_agent_service_token
    source = Observer(settings)
    parent = source.root(uuid4(), MANAGE)
    with httpx.Client(base_url=agent_hosts[0], timeout=15) as client:
        response = client.post(
            "/v1/models/providers",
            headers=token_headers,
            json={
                "operation_id": str(uuid4()),
                "catalog_id": "deepseek/deepseek-v4-pro",
                "api_model": "deepseek-v4-pro",
                "alias": "Observability " + uuid4().hex,
                "base_url": "https://api.deepseek.com",
                "api_key": prefix,
                "enabled": True,
                "chat_visible": True,
            },
        )
        assert response.status_code == 201, response.text
        provider = response.json()["provider_id"]
        test_headers = {
            **headers(TEST),
            "Authorization": token_headers["Authorization"],
            **parent.carrier(),
        }
        response = client.post(
            f"/v1/models/providers/{provider}/tests",
            headers=test_headers,
            json={
                "call_id": str(uuid4()),
                "input": {"kind": "generate", "messages": [{"role": "user", "content": prefix}]},
            },
        )
        assert response.status_code == 200 and response.json()["status"] == "completed", (
            response.text
        )
        trace_id = response.headers["X-Compass-Trace-Id"]
        assert trace_id == parent.headers()["X-Compass-Trace-Id"]
        assert response.headers["X-Compass-Trace-Sampled"] == "1"
        records = wait_for(lambda: r if len(r := observations(settings, trace_id)) >= 5 else None)
        assert len([r for r in records if r["name"] == "compass.attempt"]) == 1
        child = next(r for r in records if r["name"] == "compass.request")
        assert parent.span is not None
        assert child["parentObservationId"] == f"{parent.span.get_span_context().span_id:016x}"
        assert prefix not in json.dumps(records)
        for authorization in (
            {},
            {"Authorization": "Bearer invalid"},
            {**headers(CHAT), "Authorization": token_headers["Authorization"]},
        ):
            denied = client.get("/v1/models/observability", headers=authorization)
            assert denied.status_code in {401, 403}
            assert "X-Compass-Trace-Id" not in denied.headers
            assert settings.compass_langfuse_public_key not in denied.text
        for index, host in enumerate(agent_hosts):
            status = httpx.get(host + "/v1/models/observability", headers=token_headers).json()
            assert status["instance_id"] == f"http-{index}"
        unsampled = {**test_headers, "traceparent": "00-" + uuid4().hex + "-1234567890abcdef-00"}
        response = httpx.post(
            agent_hosts[1] + f"/v1/models/providers/{provider}/tests",
            headers=unsampled,
            json={
                "call_id": str(uuid4()),
                "input": {"kind": "generate", "messages": [{"role": "user", "content": "ok"}]},
            },
        )
        assert response.status_code == 200 and response.headers["X-Compass-Trace-Sampled"] == "0"
    parent.end()
    source.shutdown()
    for path in Path("work/observability").glob("agent-*.log"):
        assert prefix not in path.read_text()


def test_real_chat_retry_stream_ttft_and_preparation_failure(settings, agent_hosts):
    source = Observer(settings)
    parent = source.root(uuid4(), MANAGE)
    auth = {**headers(), "Authorization": "Bearer " + settings.compass_agent_service_token}
    with httpx.Client(base_url=agent_hosts[0], timeout=15) as client:
        models = []
        for model in ["deepseek-flash", "deepseek-v4-pro"]:
            value = client.post(
                "/v1/models/providers",
                headers=auth,
                json={
                    "operation_id": str(uuid4()),
                    "catalog_id": "deepseek/" + model,
                    "api_model": model,
                    "alias": "Chat trace " + uuid4().hex,
                    "base_url": "https://api.deepseek.com",
                    "api_key": "controlled-only",
                    "enabled": True,
                    "chat_visible": True,
                },
            )
            assert value.status_code == 201, value.text
            models.append(value.json()["provider_id"])
        command = {
            "requestId": str(uuid4()),
            "question": "比较销售额 E2E_RETRY_429_ONCE " + uuid4().hex,
            "selection": {"mode": "fixed", "provider_id": models[1]},
        }
        response = client.post(
            "/v1/analysis/runs",
            json=command,
            headers={**headers(CHAT), "Authorization": auth["Authorization"], **parent.carrier()},
        )
        assert response.status_code == 200, response.text
        events = [
            json.loads(line[6:])["event"]
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "run.finished"
        assert events[-1]["payload"]["status"] == "completed", events[-1]
        trace_id = parent.headers()["X-Compass-Trace-Id"]
        records = wait_for(lambda: r if len(r := observations(settings, trace_id)) >= 13 else None)
        logical = [r for r in records if r["name"] == "compass.logical"]
        inference = next(r for r in logical if r["metadata"]["stage"] == "inference")
        assert inference["metadata"]["status"] == "completed"
        assert len([r for r in logical if r["metadata"]["stage"] == "intent_flash"]) == 1
        attempts = [
            r
            for r in records
            if r["name"] == "compass.attempt" and r["parentObservationId"] == inference["id"]
        ]
        assert len(attempts) == 2
        assert sorted(r["level"] for r in attempts) == ["DEFAULT", "ERROR"]
        completed = next(r for r in attempts if r["level"] == "DEFAULT")
        assert completed["metadata"]["first_visible_ms"] >= 0
        assert not any(r.get("input") or r.get("output") for r in records)
        assert "private reasoning" not in json.dumps(records)
        assert any(r["name"] == "compass.backoff" for r in records)
        # A nonexistent provider fails before an Attempt, while retaining correlation.
        failed = client.post(
            f"/v1/models/providers/{uuid4()}/tests",
            headers={
                **headers(TEST),
                "Authorization": auth["Authorization"],
                "traceparent": "00-" + uuid4().hex + "-1234567890abcdef-01",
            },
            json={
                "call_id": str(uuid4()),
                "input": {"kind": "generate", "messages": [{"role": "user", "content": "ok"}]},
            },
        )
        assert failed.status_code == 404
        failures = wait_for(lambda: observations(settings, failed.headers["X-Compass-Trace-Id"]))
        assert not any(r["name"] == "compass.attempt" for r in failures)
    parent.end()
    source.shutdown()


@pytest.mark.parametrize("failure", [401, 503, "slow"])
def test_real_http_export_failures_leave_postgres_call_successful(settings, failure):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from compass_agent.llm.admission import ExecutionGate
    from compass_agent.llm.sdk import sdk
    from compass_agent.models.repository import ModelRepository
    from compass_agent.models.secrets import SecretStore
    from compass_agent.models.service import ModelService, create_runtime
    from sqlalchemy import create_engine

    from .provider_fixture import ProviderFixture
    from .test_models import create, generate, invoke

    payloads = []

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args):
            pass

        def do_POST(self):
            payloads.append(self.rfile.read(int(self.headers["Content-Length"])))
            if failure == "slow":
                time.sleep(2.5)
            self.send_response(503 if failure == "slow" else failure)
            self.end_headers()

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    owner = Observer(
        settings.model_copy(
            update={"compass_langfuse_base_url": f"http://127.0.0.1:{backend.server_port}"}
        )
    )
    engine = create_engine(
        os.environ["AGENT_TEST_DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://")
    )
    repo = ModelRepository(
        engine, SecretStore(settings.compass_model_encryption_key), "https://api.deepseek.com"
    )
    fixture = ProviderFixture()
    service = ModelService(
        repo, create_runtime(repo.secrets, fixture.transport), gate=ExecutionGate()
    )
    _, saved = create(repo, alias="Fault " + uuid4().hex)
    service.root_observation = owner.root(uuid4(), TEST)
    try:
        sdk()  # Exclude one-time SDK import from the network-isolation timing.
        started = time.monotonic()
        outcome = asyncio.run(invoke(service, saved.provider_id, generate("OBS_PRIVATE_SENTINEL")))
        assert outcome.status == "completed" and len(fixture.requests) == 1
        assert time.monotonic() - started < 2
        assert repo.call(outcome.call_id, TEST).status == "completed"
        service.root_observation.end()
        assert owner.provider is not None
        owner.provider.force_flush()
        assert owner.status()["export_failures"] > 0 and payloads
        assert b"OBS_PRIVATE_SENTINEL" not in b"".join(payloads)
    finally:
        owner.shutdown()
        backend.shutdown()
        backend.server_close()
        engine.dispose()
