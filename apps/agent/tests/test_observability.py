"""Trace ownership, privacy, failures and cross-instance propagation."""

import asyncio
import json
from threading import Event
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from compass_agent.llm.admission import ExecutionGate
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.executor import ReliableExecutor
from compass_agent.llm.policy import LogicalPolicy
from compass_agent.llm.runtime import Runtime
from compass_agent.observability.observer import Observer
from compass_agent.observability.summary import model_comparison
from compass_agent.schemas import ModelUsage
from compass_agent.settings import Settings
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from .test_models import CHAT, TEST, create, generate, headers, invoke
from .test_models import system as system
from .test_runtime import Adapter, request, success

CONTEXT = SimpleNamespace(purpose="test", actor_user_id=uuid4(), scope=SimpleNamespace())


def observer(**options):
    export = InMemorySpanExporter()
    settings = Settings(
        **{
            "compass_observability_enabled": True,
            "compass_observability_capture_content": False,
            **options,
        }
    )
    return Observer(settings, exporter=export), export


def spans(owner, export):
    assert owner.provider is not None
    owner.provider.force_flush()
    return cast(list[Any], list(export.get_finished_spans()))


@pytest.mark.parametrize("ratio", [0, 1])
def test_distributed_parent_and_sampling_override_downstream_local_ratio(ratio):
    one, e1 = observer(compass_observability_sample_ratio=ratio)
    two, e2 = observer(compass_observability_sample_ratio=1 - ratio)
    try:
        upstream = one.root(uuid4(), CONTEXT)
        downstream = two.root(uuid4(), CONTEXT, upstream.carrier())
        assert (
            upstream.headers()["X-Compass-Trace-Id"] == downstream.headers()["X-Compass-Trace-Id"]
        )
        assert downstream.headers()["X-Compass-Trace-Sampled"] == str(ratio)
        downstream.end()
        upstream.end()
        first, second = spans(one, e1), spans(two, e2)
        if ratio:
            assert second[0].parent.span_id == first[0].context.span_id
            assert (
                first[0].resource.attributes["service.instance.id"]
                != second[0].resource.attributes["service.instance.id"]
            )
        else:
            assert not first and not second
    finally:
        one.shutdown()
        two.shutdown()


def test_invalid_context_and_baggage_cannot_inject_metadata():
    owner, export = observer()
    try:
        root = owner.root(
            uuid4(), CONTEXT, {"traceparent": "invalid", "baggage": "password=sentinel"}
        )
        root.set(password="sentinel", model="x" * 257)
        root.end()
        result = spans(owner, export)[0]
        assert result.parent is None
        assert "sentinel" not in str(result.attributes)
        assert "langfuse.observation.metadata.model" not in result.attributes
    finally:
        owner.shutdown()


def test_tracestate_propagates_internally_and_is_removed_from_otlp():
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

    owner, export = observer()
    try:
        root = owner.root(
            uuid4(),
            CONTEXT,
            {
                "traceparent": "00-" + uuid4().hex + "-1234567890abcdef-01",
                "tracestate": "vendor=SENTINEL",
            },
        )
        assert root.carrier()["tracestate"] == "vendor=SENTINEL"
        root.end()
        values = spans(owner, export)
        assert b"SENTINEL" not in encode_spans(values).SerializeToString()
    finally:
        owner.shutdown()


def test_cost_unknown_and_reasoning_does_not_duplicate_usage():
    async def run():
        owner, export = observer()
        root = owner.root(uuid4(), CONTEXT)
        logical = root.child("compass.logical", stage="inference")
        inv = request(5)

        async def action(req, session):
            session.resources.sent = True
            output = await success(req, session)
            session.progress.usage = ModelUsage(
                input_tokens=12, output_tokens=8, reasoning_tokens=3
            )
            session.observation.accept_cost(0.01)
            return output

        result = await ReliableExecutor(
            Runtime({"litellm_chat": Adapter(action)}), ExecutionGate()
        ).execute(inv, LogicalPolicy(), observation=logical)
        logical.complete(result.status)
        logical.end()
        root.end()
        assert logical.span is not None
        generation = next(s for s in spans(owner, export) if s.name == "compass.attempt")
        assert json.loads(generation.attributes["langfuse.observation.usage_details"]) == {
            "input": 12,
            "output": 8,
        }
        assert generation.attributes["langfuse.observation.metadata.reasoning_tokens"] == 3
        assert json.loads(generation.attributes["langfuse.observation.cost_details"]) == {
            "total": 0.01
        }
        owner.shutdown()

    asyncio.run(run())


def test_retry_trace_has_one_logical_call_two_attempts_and_no_body():
    async def run():
        owner, export = observer()
        root = owner.root(uuid4(), CONTEXT)
        logical = root.child("compass.logical", stage="inference")
        count = 0

        async def action(req, session):
            nonlocal count
            count += 1
            session.resources.sent = True
            if count == 1:
                raise RuntimeFailure("model_rate_limited", recovery="rejected_transient")
            return await success(req, session)

        result = await ReliableExecutor(
            Runtime({"litellm_chat": Adapter(action)}), ExecutionGate(), jitter=lambda *args: 0
        ).execute(request(5), LogicalPolicy(max_attempts=2), observation=logical)
        logical.complete(result.status)
        logical.end()
        root.end()
        records = spans(owner, export)
        assert logical.span is not None
        attempts = [s for s in records if s.name == "compass.attempt"]
        assert len(attempts) == 2 and count == 2
        assert [s.attributes["langfuse.observation.metadata.status"] for s in attempts] == [
            "failed",
            "completed",
        ]
        assert all(s.parent.span_id == logical.span.get_span_context().span_id for s in attempts)
        assert "langfuse.observation.input" not in str(records)
        assert "langfuse.observation.cost_details" not in attempts[0].attributes
        owner.shutdown()

    asyncio.run(run())


def test_final_record_failure_wins_over_cancellation_on_root():
    owner, export = observer()
    root = owner.root(uuid4(), CONTEXT)
    logical = root.child("compass.logical")
    logical.complete("failed", "record_write_failed", authoritative=True)
    root.complete("canceled")
    logical.end()
    root.end()
    root.end()
    values = spans(owner, export)
    assert len(values) == 2
    assert all(s.attributes["langfuse.observation.metadata.status"] == "failed" for s in values)
    owner.shutdown()


def test_bounded_queue_and_transport_failure_do_not_block_application():
    entered, release = Event(), Event()

    class Blocking(InMemorySpanExporter):
        def export(self, spans):
            entered.set()
            release.wait(2)
            return SpanExportResult.FAILURE

    o = Observer(Settings(compass_observability_enabled=True), exporter=Blocking())
    try:
        for _ in range(64):
            o.root(uuid4(), CONTEXT).end()
        assert entered.wait(2)
        for _ in range(800):
            o.root(uuid4(), CONTEXT).end()
        info = o.status()
        assert info["queue_size"] <= 512 and info["dropped"] > 0
        release.set()
        assert o.provider is not None
        o.provider.force_flush()
        assert o.status()["export_failures"] > 0
    finally:
        release.set()
        o.shutdown()


def test_exit_filter_rejects_unsafe_attributes():
    owner, export = observer()
    try:
        root = owner.root(uuid4(), CONTEXT)
        assert root.span is not None
        root.span.set_attribute("unregistered.secret", "SENTINEL")
        root.end()
        assert not spans(owner, export)
        assert owner.status()["dropped"] == 1
    finally:
        owner.shutdown()


def test_fixed_summary_counts_terminal_states_and_unknown_cost_separately():
    calls = []

    def handler(request):
        query = json.loads(request.url.params["query"])
        calls.append(query)
        filters = query["filters"]
        status = next((f["value"] for f in filters if f.get("key") == "status"), None)
        if status:
            return httpx.Response(
                200,
                json={
                    "data": [{"count_count": 3 if status == "completed" else 1, "sum_latency": 1}]
                },
            )
        unknown = any(f.get("key") in {"cost_state", "usage_complete"} for f in filters)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"count_count": 1}
                    if unknown
                    else {
                        "count_count": 8,
                        "sum_inputTokens": 12,
                        "sum_outputTokens": 8,
                        "sum_totalCost": 0.1,
                    }
                ]
            },
        )

    settings = Settings(
        compass_langfuse_base_url="http://127.0.0.1:8104",
        compass_langfuse_public_key="fixture",
        compass_langfuse_secret_key=SecretStr("fixture"),
    )
    models = [SimpleNamespace(id=uuid4(), api_model=x) for x in ["flash", "pro"]]
    result = asyncio.run(
        model_comparison(settings, models, "24h", transport=httpx.MockTransport(handler))
    )
    assert len(calls) == 14
    assert all(any(f.get("key") == "model" for f in c["filters"]) for c in calls)
    row = result["items"][0]
    assert row["logical_count"] == 6 and row["success_rate"] == 0.5
    assert row["estimated_usd"] is None and row["known_estimated_usd"] == 0.1


def test_settings_reject_export_url_credentials_and_invalid_ratio():
    for url in [
        "http://evil.invalid",
        "https://name:password@example.com",
        "https://example.com/?key=secret",
    ]:
        with pytest.raises(ValueError):
            Settings(compass_langfuse_base_url=url)
    with pytest.raises(ValueError):
        Settings(compass_observability_sample_ratio=float("nan"))


def test_optional_sdk_pricing_failure_and_telemetry_never_add_repository_reads(system):
    repo, upstream, service = system
    _, saved = create(repo)
    owner, export = observer()
    service.root_observation = owner.root(uuid4(), TEST)
    try:
        with (
            patch.object(repo, "get", side_effect=AssertionError("extra telemetry read")),
            patch("litellm.stream_chunk_builder", side_effect=ValueError("SENTINEL")),
        ):
            result = asyncio.run(invoke(service, saved.provider_id, generate(stream=True)))
        assert result.status == "completed" and len(upstream.requests) == 1
        service.root_observation.end()
        records = spans(owner, export)
        assert "SENTINEL" not in str(records)
        assert all(
            "traceparent" not in r.headers and "baggage" not in r.headers for r in upstream.requests
        )
    finally:
        owner.shutdown()


def test_record_failure_after_success_marks_actual_root_and_logical_failed(system):
    repo, _, service = system
    _, saved = create(repo)
    owner, export = observer()
    service.root_observation = owner.root(uuid4(), TEST)
    try:
        with patch.object(repo, "finish", return_value=False):
            result = asyncio.run(invoke(service, saved.provider_id, generate()))
        assert result.error_code == "record_write_failed" and result.status == "failed"
        service.root_observation.complete("canceled")
        service.root_observation.end()
        values = spans(owner, export)
        attempt = next(s for s in values if s.name == "compass.attempt")
        assert attempt.attributes["langfuse.observation.metadata.status"] == "completed"
        for value in values:
            if value.name in {"compass.request", "compass.logical"}:
                assert value.attributes["langfuse.observation.metadata.status"] == "failed"
                assert (
                    value.attributes["langfuse.observation.metadata.error_code"]
                    == "record_write_failed"
                )
    finally:
        owner.shutdown()


def test_api_prepare_failure_keeps_safe_trace_and_denied_requests_do_not_start_trace(system):
    from compass_agent.main import app
    from compass_agent.model_api import get_model_service
    from fastapi.testclient import TestClient

    _, _, service = system
    owner, export = observer()
    app.dependency_overrides[get_model_service] = lambda: service
    settings = Settings(compass_agent_service_token="unit-service-token")
    try:
        with (
            patch("compass_agent.model_api.get_settings", return_value=settings),
            TestClient(app) as client,
        ):
            app.state.observation = owner
            response = client.post(
                f"/v1/models/providers/{uuid4()}/tests",
                headers=headers(TEST),
                json={"call_id": str(uuid4()), "input": generate().model_dump(mode="json")},
            )
            assert (
                response.status_code == 404 and response.headers["X-Compass-Trace-Sampled"] == "1"
            )
            response = client.get("/v1/models/observability", headers=headers(CHAT))
            assert response.status_code == 403 and "X-Compass-Trace-Id" not in response.headers
        root = next(s for s in spans(owner, export) if s.name == "compass.request")
        assert root.attributes["langfuse.observation.metadata.error_code"] == "not_found"
    finally:
        app.dependency_overrides.clear()
        owner.shutdown()


def test_incomplete_cache_prices_remain_unknown_and_zero_is_a_valid_known_price():
    from compass_agent.observability.litellm_receipt import estimate

    owner, export = observer()
    root = owner.root(uuid4(), CONTEXT)
    try:
        sdk = SimpleNamespace(completion_cost=lambda **kw: 0)
        response = SimpleNamespace(
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": None},
            }
        )
        estimate(root, sdk, response, "fixture")
        assert root.cost is None
        response.usage["prompt_tokens_details"]["cached_tokens"] = 3
        estimate(root, sdk, response, "fixture")
        assert root.cost == 0
    finally:
        root.end()
        owner.shutdown()


def test_two_instances_diagnose_drops_and_shutdown_within_bound():
    from time import monotonic

    class SlowFailure(InMemorySpanExporter):
        def export(self, spans):
            import time

            time.sleep(0.05)
            return SpanExportResult.FAILURE

    owners = [
        Observer(Settings(compass_observability_enabled=True), exporter=SlowFailure())
        for _ in range(2)
    ]
    started = monotonic()
    for owner in owners:
        for _ in range(900):
            owner.root(uuid4(), CONTEXT).end()
    assert owners[0].instance_id != owners[1].instance_id
    for owner in owners:
        assert owner.status()["dropped"] > 0
        owner.shutdown()
        assert owner.pipeline is not None and owner.pipeline.exporter.failures > 0
        owner.shutdown()
    assert monotonic() - started < 10


def test_queue_timing_and_first_visible_excludes_reasoning_and_nonstream():
    from compass_agent.schemas import ReasoningMetaEvent, TextDeltaEvent

    async def run():
        owner, export = observer()
        root = owner.root(uuid4(), CONTEXT)
        gate = ExecutionGate(capacity=1, max_inflight=3)
        inv = request(5)

        async def action(req, session):
            if session.emit:
                await session.emit(ReasoningMetaEvent(call_id=req.context.call_id, thinking=True))
                assert session.observation.first_ns is None
                await asyncio.sleep(0.02)
                await session.emit(TextDeltaEvent(call_id=req.context.call_id, text="private"))
            return await success(req, session)

        async def emit(event):
            pass

        logical = root.child("compass.logical", stage="inference")
        await gate.slots.acquire()
        task = asyncio.create_task(
            ReliableExecutor(Runtime({"litellm_chat": Adapter(action)}), gate).execute(
                inv, LogicalPolicy(), emit, observation=logical
            )
        )
        await asyncio.sleep(0.04)
        gate.slots.release()
        await task
        logical.end()
        plain = root.child("compass.logical", stage="test")
        await ReliableExecutor(Runtime({"litellm_chat": Adapter(action)}), gate).execute(
            inv, LogicalPolicy(), observation=plain
        )
        plain.end()
        root.end()
        values = spans(owner, export)
        attempts = [s for s in values if s.name == "compass.attempt"]
        assert attempts[0].attributes["langfuse.observation.metadata.first_visible_ms"] >= 15
        assert "langfuse.observation.completion_start_time" not in attempts[1].attributes
        recorded = next(s for s in values if s.name == "compass.logical")
        assert recorded.attributes["langfuse.observation.metadata.queue_wait_ms"] >= 30
        owner.shutdown()

    asyncio.run(run())


def test_overload_creates_no_attempt_and_decision_usage_is_observed(system):
    from compass_agent.schemas import DecisionInput, DecisionQuestion

    async def run():
        owner, export = observer()
        root = owner.root(uuid4(), CONTEXT)
        gate = ExecutionGate(1, 1)
        gate.enter()
        logical = root.child("compass.logical")
        outcome = await ReliableExecutor(Runtime({"litellm_chat": Adapter(success)}), gate).execute(
            request(5), LogicalPolicy(), observation=logical
        )
        assert outcome.error is not None and outcome.error.code == "runtime_overloaded"
        logical.complete(outcome.status)
        logical.end()
        gate.leave()
        root.end()
        assert not any(s.name == "compass.attempt" for s in spans(owner, export))
        owner.shutdown()
        repo, upstream, service = system
        _, saved = create(
            repo,
            catalog_id="typesafe/jev",
            api_model="jev",
            alias="Decision trace",
            base_url="https://decision.example.com",
            chat_visible=False,
        )
        owner, export = observer()
        service.root_observation = owner.root(uuid4(), TEST)
        outcome = await invoke(
            service,
            saved.provider_id,
            DecisionInput(
                state="PRIVATE_STATE", questions={"predict": DecisionQuestion(type="noul")}
            ),
        )
        assert outcome.status == "completed" and len(upstream.requests) == 1
        service.root_observation.end()
        values = spans(owner, export)
        attempt = next(s for s in values if s.name == "compass.attempt")
        assert json.loads(attempt.attributes["langfuse.observation.usage_details"]) == {
            "input": 12,
            "output": 3,
        }
        assert attempt.attributes["langfuse.observation.metadata.cost_state"] == "unknown"
        assert "PRIVATE_STATE" not in str(values)
        owner.shutdown()

    asyncio.run(run())
