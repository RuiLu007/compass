"""Feature 989 Agent Run contracts, execution, cancellation and Retail boundaries."""

import asyncio
import base64
import inspect
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from compass_agent.api.runs import (
    RunStreamResponse,
    _log_terminal,
    cancel_run,
    get_agent_executor,
    start_run,
)
from compass_agent.llm.contracts import GenerationOutput, ProviderProgress
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.litellm_chat import complete_output
from compass_agent.models.lifecycle import ExecutionTasks
from compass_agent.routing.service import SelectedRoute
from compass_agent.runtime.context import compass_definition
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.runtime.service import RunService
from compass_agent.runtime.stream import RunWriter
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    AnalysisCancelAck,
    AnalysisFileNone,
    AnalysisHistoryTurn,
    AnalysisRunInput,
    CallResponse,
    Capability,
    ChatMessage,
    GenerateInput,
    Intent,
    MainModelSelection,
    ModelCapabilities,
    ModelRoute,
    RoutingFeatures,
    RoutingMetrics,
    RunCompleted,
    RunEmpty,
    RunFailed,
    RunFinished,
    RunStarted,
    StepFinished,
    StepFinishedPayload,
    StepStarted,
    StepStartedPayload,
    TenantScope,
    ToolCall,
    ToolCallFunction,
    ToolDefinition,
    ToolFailedPayload,
    ToolFinished,
    ToolFunction,
    ToolStarted,
    ToolStartedPayload,
    TrustedContext,
)
from compass_agent.settings import Settings
from compass_agent.tools.contracts import PhaseCanceled, StopRequested
from fastapi import Response
from fastapi.testclient import TestClient
from psycopg.pq import TransactionStatus
from pydantic import SecretStr, ValidationError
from starlette.requests import Request

from .query_fixtures import provider_for

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
WORKSPACE_ID = UUID("20000000-0000-0000-0000-000000000002")
ACTOR_ID = UUID("30000000-0000-0000-0000-000000000003")
PROVIDER_ID = UUID("40000000-0000-0000-0000-000000000004")
CHAT = TrustedContext(
    actor_user_id=ACTOR_ID,
    scope=TenantScope(
        kind="tenant",
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
    ),
    purpose="chat",
)


def run_command() -> AnalysisRunInput:
    return AnalysisRunInput(
        requestId=uuid4(),
        question="比较两个时段并找出变化最大的商品",
        history=[AnalysisHistoryTurn(question="上一问", answer="上一答")],
        selection=MainModelSelection(mode="fixed", provider_id=PROVIDER_ID),
    )


def auth_headers(context: TrustedContext, token: str = "run-service-token") -> dict[str, str]:
    encoded = base64.urlsafe_b64encode(context.model_dump_json().encode()).decode().rstrip("=")
    return {
        "Authorization": "Bearer " + token,
        "X-Compass-Context": encoded,
    }


def test_run_writer_enforces_start_sequence_and_terminal() -> None:
    writer = RunWriter(uuid4(), uuid4())
    started = writer.write(RunStarted(type="run.started", payload=RunEmpty()))
    terminal = writer.write(
        RunFinished(
            type="run.finished",
            payload=RunCompleted(
                status="completed",
                finalAnswer="完成",
                file=AnalysisFileNone(status="none"),
            ),
        )
    )
    first = json.loads(started.removeprefix(b"data: ").removesuffix(b"\n\n"))
    last = json.loads(terminal.removeprefix(b"data: ").removesuffix(b"\n\n"))
    assert first["protocol"] == "compass.run" and first["version"] == 1
    assert (first["seq"], last["seq"]) == (1, 2)
    with pytest.raises(ValueError, match="start or terminal"):
        writer.write(RunStarted(type="run.started", payload=RunEmpty()))


def test_oversized_visible_context_is_rejected_before_any_external_dispatch() -> None:
    external_calls = []
    with pytest.raises(ValidationError, match="visible context exceeds 32 KiB"):
        AnalysisRunInput(
            requestId=uuid4(),
            question="问题",
            history=[
                AnalysisHistoryTurn(
                    question="历史",
                    answer="界" * 11_000,
                )
            ],
            selection=MainModelSelection(mode="fixed", provider_id=PROVIDER_ID),
        )
        external_calls.append("unreachable")
    assert external_calls == []


def test_agent_dispatch_preserves_unknown_tool_for_safe_executor_observation() -> None:
    request = GenerateInput(
        messages=[ChatMessage(role="user", content="test")],
        tools=[
            ToolDefinition(
                function=ToolFunction(
                    name="custom_query",
                    parameters={
                        "type": "object",
                        "properties": {"dimension": {"const": "channel"}},
                        "required": ["dimension"],
                    },
                )
            )
        ],
    )
    output = GenerationOutput(
        tool_calls=(
            ToolCall(
                id="call_unknown",
                function=ToolCallFunction(name="other", arguments="not-json"),
            ),
        )
    )
    progress = ProviderProgress(output=output, finish_reason="tool_calls")
    complete_output(request, progress, True, "agent_dispatch")
    assert progress.normal_eof
    strict = ProviderProgress(output=output, finish_reason="tool_calls")
    with pytest.raises(RuntimeFailure, match="invalid_tool_arguments"):
        complete_output(request, strict, True)


def test_final_answer_budget_uses_serialized_json_bytes() -> None:
    exact = "x" * (RUN_STREAM_LIMITS["answerBytes"] - 2)
    assert RunService._final_answer(exact) == exact
    with pytest.raises(Exception, match="model_incomplete"):
        RunService._final_answer("x" * (RUN_STREAM_LIMITS["answerBytes"] - 1))
    # Each quote is one UTF-8 byte but two JSON bytes after escaping.
    with pytest.raises(Exception, match="model_incomplete"):
        RunService._final_answer('"' * (RUN_STREAM_LIMITS["answerBytes"] // 2))


class FakeModels:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []
        self.root_observation = None
        self.stage = "intent_flash"
        self.logical_observation_stages = []

    async def prepare(self, call_id, provider_id, request, context, **kwargs):
        self.logical_observation_stages.append(self.stage)
        self.requests.append((call_id, request.model_copy(deep=True), kwargs))
        return SimpleNamespace(call_id=call_id)

    async def run(self, _prepared, _request, **kwargs):
        assert kwargs["tool_output_validation"] == "agent_dispatch"
        return self.results.pop(0)


class FakeRouting:
    def __init__(self, selected):
        self.selected = selected

    async def select_route(self, *_args, **kwargs):
        kwargs["check"]()
        return self.selected


def selected_route() -> SelectedRoute:
    yes = Capability(state="supported", reason="test")
    no = Capability(state="unsupported", reason="test")
    capabilities = ModelCapabilities(
        sync=yes,
        stream=yes,
        tools=yes,
        thinking=no,
        json_output=yes,
        schema_output=yes,
        reasoning_efforts=[],
    )
    from datetime import UTC, datetime

    from compass_agent.schemas import ProviderView

    provider = ProviderView(
        id=PROVIDER_ID,
        catalog_id="fixture",
        vendor="fixture",
        api_model="unit-model",
        alias="Retail Pro",
        kind="llm",
        base_url="https://fixture.test/v1",
        has_api_key=True,
        enabled=True,
        chat_visible=True,
        chat_supported=True,
        version=1,
        binding_version=7,
        capabilities=capabilities,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    intent = Intent(category_id="CAT-03", scene_id="SC-12")
    features = RoutingFeatures(
        task_type="analysis",
        difficulty="L2",
        response_mode="answer",
        required_capabilities=["tools"],
    )
    routing = ModelRoute(
        mode="fixed",
        provider_id=PROVIDER_ID,
        model="unit-model",
        reason="test",
        dictionary_version="test",
        routing_version="test",
    )
    return SelectedRoute(
        intent,
        features,
        routing,
        provider,
        "按授权工具分析",
        False,
        RoutingMetrics(),
    )


def model_result(*, tool_calls=(), text="", token=None, finish="stop") -> CallResponse:
    return CallResponse(
        call_id=uuid4(),
        provider_id=PROVIDER_ID,
        config_version=1,
        status="completed",
        duration_ms=1,
        requested_model="unit-model",
        kind="generate",
        text=text,
        tool_calls=list(tool_calls),
        continuation_token=token,
        finish_reason=finish,
    )


def test_agent_generation_observation_stage_resets_after_routing() -> None:
    async def scenario():
        models = FakeModels([model_result(text="完成", finish="stop")])
        executor = RunService(
            cast(Any, models),
            cast(Any, FakeRouting(selected_route())),
            tools_provider=provider_for(lambda _scope: binding()),
            definition=compass_definition(),
        )
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT))

        result = await executor.run(run_command(), CHAT, control, lambda _event: None)

        assert result.status == "completed"
        assert models.logical_observation_stages == ["inference"]

    asyncio.run(scenario())


class FakeCursor:
    def __init__(self, connection, query_started, release):
        self.connection = connection
        self.query_started = query_started
        self.release = release
        self.statement = ""
        self.closed = False

    async def execute(self, statement, _values=None):
        self.statement = statement
        if statement == "BEGIN READ ONLY":
            self.connection.pgconn.transaction_status = TransactionStatus.INTRANS
        elif "WITH latest" in statement:
            self.query_started.set()
            await self.release.wait()

    async def fetchall(self):
        if "session_user" in self.statement:
            return [("unit_a_east", "unit_a_east", "on", True, True)]
        return [("SPU-1", "商品", 1200, 800, -400)]

    async def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, query_started, release):
        self.pgconn = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.cursor_value = FakeCursor(self, query_started, release)
        self.release = release
        self.canceled = self.rolled_back = self.closed = False

    def cursor(self):
        return self.cursor_value

    async def cancel_safe(self, timeout):
        assert timeout == 1
        self.canceled = True
        self.release.set()

    async def rollback(self):
        self.rolled_back = True
        self.pgconn.transaction_status = TransactionStatus.IDLE

    async def close(self):
        self.closed = True


def test_deadline_preserves_unconfirmed_phase_cleanup_receipt() -> None:
    async def scenario():
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT), lifetime_s=0.2)

        async def operation():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise PhaseCanceled(cleanup_confirmed=False) from None

        with pytest.raises(StopRequested):
            await control.perform(operation)
        result = control.commit(RunFailed(status="failed", errorCode="internal_error"))
        assert result.status == "cleanup_unconfirmed"
        assert result.reason == "deadline"

    asyncio.run(scenario())


def test_cancel_endpoint_runs_on_owner_event_loop_and_is_idempotent() -> None:
    assert inspect.iscoroutinefunction(cancel_run)

    async def scenario():
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT))
        loop = asyncio.get_running_loop()
        original = control.request_stop

        def request_stop(reason):
            assert asyncio.get_running_loop() is loop
            return original(reason)

        control.request_stop = request_stop
        response = Response()
        ack = await cancel_run(
            control.id, RunEmpty(), response, CHAT, registry, Request({"type": "http"})
        )
        assert isinstance(ack, AnalysisCancelAck)
        assert response.status_code == 202 and ack.root.status == "accepted"
        terminal = control.commit(
            RunCompleted(
                status="completed",
                finalAnswer="late",
                file=AnalysisFileNone(status="none"),
            )
        )
        assert terminal.status == "canceled"
        repeated = await cancel_run(
            control.id, RunEmpty(), Response(), CHAT, registry, Request({"type": "http"})
        )
        assert isinstance(repeated, AnalysisCancelAck)
        assert repeated.root.status == "already_terminal"

    asyncio.run(scenario())


def test_first_packet_precedes_executor_dispatch() -> None:
    class ImmediateExecutor:
        def __init__(self):
            self.models = SimpleNamespace(root_observation=None)
            self.dispatched = False

        async def run(self, _command, _context, control, _emit):
            self.dispatched = True
            return control.commit(
                RunCompleted(
                    status="completed",
                    finalAnswer="done",
                    file=AnalysisFileNone(status="none"),
                )
            )

    async def scenario():
        tasks = ExecutionTasks()
        app = SimpleNamespace(state=SimpleNamespace(model_tasks=tasks, observation=None))
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/analysis/runs",
                "headers": [],
                "app": app,
            }
        )
        registry = RunRegistry()
        executor = ImmediateExecutor()
        response = await start_run(run_command(), request, CHAT, registry, cast(Any, executor))
        iterator = cast(AsyncIterator[bytes], response.body_iterator.__aiter__())
        first = await anext(iterator)
        assert b'"type":"run.started"' in first
        assert not executor.dispatched
        second = await anext(iterator)
        assert b'"type":"run.finished"' in second and executor.dispatched
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)
        await tasks.close()

    asyncio.run(scenario())


def test_cancel_immediately_after_first_packet_returns_canceled_terminal_without_dispatch() -> None:
    class CancelAwareExecutor:
        def __init__(self):
            self.models = SimpleNamespace(root_observation=None)
            self.external_dispatches = 0

        async def run(self, _command, _context, control, _emit):
            try:
                control.require_running()
                self.external_dispatches += 1
            except StopRequested:
                pass
            return control.commit(
                RunCompleted(
                    status="completed",
                    finalAnswer="late",
                    file=AnalysisFileNone(status="none"),
                )
            )

    async def scenario():
        tasks = ExecutionTasks()
        app = SimpleNamespace(state=SimpleNamespace(model_tasks=tasks, observation=None))
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/analysis/runs",
                "headers": [],
                "app": app,
            }
        )
        registry = RunRegistry()
        executor = CancelAwareExecutor()
        response = await start_run(run_command(), request, CHAT, registry, cast(Any, executor))
        iterator = cast(AsyncIterator[bytes], response.body_iterator.__aiter__())
        first = await anext(iterator)
        assert b'"type":"run.started"' in first
        control = next(iter(registry.active.values()))
        control.request_stop("user")
        terminal = await anext(iterator)
        packet = json.loads(terminal.removeprefix(b"data: ").removesuffix(b"\n\n"))
        assert packet["event"].pop("durationMs") >= 0
        assert packet["event"] == {
            "type": "run.finished",
            "payload": {"status": "canceled", "reason": "user", "cleanup": "confirmed"},
        }
        assert executor.external_dispatches == 0
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)
        await tasks.close()

    asyncio.run(scenario())


def test_disconnect_listener_cancels_stream_while_send_is_blocked() -> None:
    async def scenario():
        first_sent = asyncio.Event()
        blocked = asyncio.Event()
        finalized = asyncio.Event()
        body_count = 0

        async def body():
            try:
                yield b"first"
                yield b"second"
            finally:
                finalized.set()

        async def send(message):
            nonlocal body_count
            if message["type"] != "http.response.body":
                return
            body_count += 1
            if body_count == 1:
                first_sent.set()
                return
            blocked.set()
            await asyncio.Event().wait()

        async def receive():
            await blocked.wait()
            return {"type": "http.disconnect"}

        response = RunStreamResponse(body(), media_type="text/event-stream")
        await asyncio.wait_for(
            response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            ),
            1,
        )
        assert first_sent.is_set() and blocked.is_set() and finalized.is_set()

    asyncio.run(scenario())


def test_response_start_send_failure_releases_unstarted_run_capacity() -> None:
    class NeverExecutor:
        def __init__(self):
            self.models = SimpleNamespace(root_observation=None)

        async def run(self, *_args):
            raise AssertionError("executor must not start")

    async def scenario():
        tasks = ExecutionTasks()
        app = SimpleNamespace(state=SimpleNamespace(model_tasks=tasks, observation=None))
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/analysis/runs",
                "headers": [],
                "app": app,
            }
        )
        registry = RunRegistry()
        response = await start_run(
            run_command(), request, CHAT, registry, cast(Any, NeverExecutor())
        )

        async def send(_message):
            raise OSError("connection closed before response start")

        async def receive():
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        with pytest.raises(ClientDisconnect):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            )
        assert registry.active == {}
        assert len(registry.terminal) == 1
        await tasks.close()

    from starlette.requests import ClientDisconnect

    asyncio.run(scenario())


def test_analysis_http_rejects_missing_service_token_without_dispatch() -> None:
    from compass_agent.main import app

    dispatched = []

    class NeverExecutor:
        models = SimpleNamespace(root_observation=None)

        async def run(self, *_args):
            dispatched.append(True)
            raise AssertionError("must not dispatch")

    app.dependency_overrides[get_agent_executor] = lambda: NeverExecutor()
    try:
        with (
            patch(
                "compass_agent.main.get_settings",
                return_value=Settings(compass_agent_service_token=""),
            ),
            patch(
                "compass_agent.api.models.get_settings",
                return_value=Settings(compass_agent_service_token=""),
            ),
            TestClient(app) as client,
        ):
            response = client.post(
                "/v1/analysis/runs",
                json=run_command().model_dump(mode="json", by_alias=True),
            )
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "unavailable"
            assert "no-store" in response.headers["cache-control"]
        assert dispatched == []
    finally:
        app.dependency_overrides.clear()


def test_agent_run_is_the_only_product_inference_entrypoint() -> None:
    from compass_agent.main import app

    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert "post" in paths["/v1/analysis/runs"]
        assert "/v1/models/calls" not in paths
        assert "get" in paths["/v1/models/calls/{call_id}"]
        assert "post" in paths["/v1/models/providers/{provider_id}/tests"]
        assert client.post("/v1/models/calls", json={}).status_code == 404


def test_analysis_http_auth_owner_headers_and_body_limit(caplog) -> None:
    from compass_agent.main import app

    class ImmediateExecutor:
        def __init__(self):
            self.models = SimpleNamespace(root_observation=None)
            self.calls = 0

        async def run(self, _command, _context, control, _emit):
            self.calls += 1
            step_id = uuid4()
            model_call_id = uuid4()
            _emit(
                StepStarted(
                    type="step.started",
                    payload=StepStartedPayload(
                        stepId=str(step_id),
                        stepIndex=1,
                        modelCallId=str(model_call_id),
                    ),
                )
            )
            _emit(
                ToolStarted(
                    type="tool.started",
                    payload=ToolStartedPayload(
                        stepId=str(step_id),
                        toolCallId="call_safe",
                        toolIndex=1,
                        toolKind="unavailable",
                        params=None,
                    ),
                )
            )
            _emit(
                ToolFinished(
                    type="tool.finished",
                    payload=ToolFailedPayload(
                        stepId=str(step_id),
                        toolCallId="call_safe",
                        toolIndex=1,
                        status="error",
                        errorCode="retail_unavailable",
                    ),
                )
            )
            _emit(
                StepFinished(
                    type="step.finished",
                    payload=StepFinishedPayload(stepId=str(step_id), status="completed"),
                )
            )
            return control.commit(
                RunCompleted(
                    status="completed",
                    finalAnswer="done",
                    file=AnalysisFileNone(status="none"),
                )
            )

    executor = ImmediateExecutor()
    caplog.set_level("INFO", logger="compass_agent.api.runs")
    sentinel = "private-question-sentinel"
    settings = Settings(
        compass_agent_service_token="run-service-token", compass_runtime_state_key=SecretStr("")
    )
    app.dependency_overrides[get_agent_executor] = lambda: executor
    wrong_purpose = CHAT.model_copy(update={"purpose": "module_read"})
    try:
        with (
            patch("compass_agent.main.get_settings", return_value=settings),
            patch("compass_agent.api.models.get_settings", return_value=settings),
            TestClient(app) as client,
        ):
            denied = client.post(
                "/v1/analysis/runs",
                headers=auth_headers(wrong_purpose),
                json=run_command().model_dump(mode="json", by_alias=True),
            )
            assert denied.status_code == 403 and executor.calls == 0

            oversized = client.post(
                "/v1/analysis/runs",
                headers=auth_headers(CHAT) | {"Content-Type": "application/json"},
                content=b"{" + b"x" * (64 * 1024),
            )
            assert oversized.status_code == 422 and executor.calls == 0

            command = run_command().model_copy(update={"question": sentinel})
            started = client.post(
                "/v1/analysis/runs",
                headers=auth_headers(CHAT),
                json=command.model_dump(mode="json", by_alias=True),
            )
            assert started.status_code == 503 and executor.calls == 0
            assert started.json()["error"]["code"] == "runtime_unavailable"
            assert "no-store" in started.headers["cache-control"]
            assert sentinel not in repr([record.__dict__ for record in caplog.records])
    finally:
        app.dependency_overrides.clear()


def test_cleanup_unconfirmed_terminal_log_is_code_and_id_only(caplog) -> None:
    caplog.set_level("INFO", logger="compass_agent.api.runs")

    async def scenario():
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT))
        control.mark_cleanup_unconfirmed()
        result = control.commit(
            RunCompleted(
                status="completed",
                finalAnswer="private-answer-sentinel",
                file=AnalysisFileNone(status="none"),
            )
        )
        _log_terminal(control, result)
        ack = await cancel_run(
            control.id, RunEmpty(), Response(), CHAT, registry, Request({"type": "http"})
        )
        assert isinstance(ack, AnalysisCancelAck)
        assert ack.root.status == "already_terminal"
        return str(control.id)

    run_id = asyncio.run(scenario())
    record = next(record for record in caplog.records if record.message == "analysis.run.finished")
    assert record.run_id == run_id
    assert record.status == "cleanup_unconfirmed"
    assert record.cleanup == "unconfirmed"
    assert record.stop_reason == "execution_error"
    assert "private-answer-sentinel" not in repr(record.__dict__)
    cancel_record = next(
        record for record in caplog.records if record.message == "analysis.run.cancel"
    )
    assert cancel_record.terminal_status == "cleanup_unconfirmed"
    assert cancel_record.cleanup == "unconfirmed"


# Shared authorized-connection fixture; no business tool is loaded by the Runtime.
def binding():
    from compass_agent.data_connections.transport import AuthorizedDatabaseBinding

    return AuthorizedDatabaseBinding(
        TENANT_ID, WORKSPACE_ID, "unit_a_east", SecretStr("postgresql://unused")
    )


def product_call(call_id="call_query", channel_code="POS"):
    return ToolCall(
        id=call_id,
        function=ToolCallFunction(
            name="database_query",
            arguments=json.dumps(
                {
                    "op": "query",
                    "sql": "SELECT 1 AS value",
                }
            ),
        ),
    )
