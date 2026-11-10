"""Feature 989 Agent Run contracts, execution, cancellation and Retail boundaries."""

import asyncio
import base64
import csv
import inspect
import io
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from compass_agent.llm.contracts import GenerationOutput, ProviderProgress
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.litellm_chat import complete_output
from compass_agent.models.lifecycle import ExecutionTasks
from compass_agent.routing.service import SelectedRoute
from compass_agent.run_api import (
    RunStreamResponse,
    _log_terminal,
    cancel_run,
    get_agent_executor,
    start_run,
)
from compass_agent.runtime.artifact import ExportCandidate, build_analysis_file
from compass_agent.runtime.control import (
    OwnerKey,
    PhaseCanceled,
    RunRegistry,
    StopRequested,
)
from compass_agent.runtime.executor import AgentExecutor
from compass_agent.runtime.retail import (
    RetailBinding,
    RetailQueryResult,
    RetailRow,
    binding_for,
    prepare_retail_call,
    query_retail,
)
from compass_agent.runtime.stream import RunWriter
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
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
    ProductRunParams,
    RoutingFeatures,
    RoutingMetrics,
    RunCompleted,
    RunEmpty,
    RunFailed,
    RunFinished,
    RunPeriod,
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
from compass_agent.settings import RetailRunScopeConfig, Settings
from fastapi import Response
from fastapi.testclient import TestClient
from psycopg.pq import TransactionStatus
from pydantic import SecretStr, ValidationError
from starlette.requests import Request

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


def retail_settings() -> Settings:
    scope = {
        "tenantId": str(TENANT_ID),
        "workspaceId": str(WORKSPACE_ID),
        "release": "retail-dev-20261022b",
        "contentHash": "7ae26405a71589c98ca8379bec1e1ec1ff77e16232ff2e1bf26b6d4501b8183e",
        "role": "unit_a_east",
        "retailTenantId": 101,
        "schema": "core",
    }
    return Settings(
        compass_retail_run_scope=json.dumps(scope),
        compass_retail_run_readonly_dsn=SecretStr("postgresql://unused"),
    )


@pytest.mark.parametrize(
    "role",
    ["a_east", "Unit_a_east", "unit-a_east", "x" * 57 + "_a_east"],
)
def test_retail_scope_rejects_non_manifest_role_identifiers(role: str) -> None:
    value = json.loads(retail_settings().compass_retail_run_scope)
    value["role"] = role
    with pytest.raises(ValidationError):
        RetailRunScopeConfig.model_validate(value)


def binding() -> RetailBinding:
    return RetailBinding(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        release="retail-dev-20261022b",
        content_hash="7ae26405a71589c98ca8379bec1e1ec1ff77e16232ff2e1bf26b6d4501b8183e",
        role="unit_a_east",
        retail_tenant_id=101,
        schema_name="core",
        dsn=SecretStr("postgresql://unused"),
    )


def product_call(call_id: str = "call_product") -> ToolCall:
    return ToolCall(
        id=call_id,
        function=ToolCallFunction(
            name="retail_sales",
            arguments=json.dumps(
                {
                    "dimension": "product",
                    "period": {"start": "2025-10-01", "end": "2025-10-08"},
                    "compareTo": {"start": "2025-09-01", "end": "2025-09-08"},
                    "channelCode": "TMALL",
                }
            ),
        ),
    )


def product_result() -> RetailQueryResult:
    return RetailQueryResult(
        status="ok",
        dimension="product",
        params=ProductRunParams(
            dimension="product",
            period=RunPeriod(start="2025-10-01", end="2025-10-08"),
            compareTo=RunPeriod(start="2025-09-01", end="2025-09-08"),
            channelCode="TMALL",
        ),
        rows=(RetailRow("SPU-1", "=危险公式", 1200, 800, -400),),
    )


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
                    name="retail_sales",
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


def test_retail_rejects_unknown_invalid_and_unavailable_before_dispatch() -> None:
    unknown = prepare_retail_call(
        ToolCall(
            id="unknown",
            function=ToolCallFunction(name="shell", arguments='{"x":1}'),
        ),
        binding(),
    )
    duplicate = prepare_retail_call(
        ToolCall(
            id="duplicate",
            function=ToolCallFunction(
                name="retail_sales",
                arguments=(
                    '{"dimension":"channel","dimension":"channel",'
                    '"period":{"start":"2025-01-01","end":"2025-01-02"},'
                    '"compareTo":null}'
                ),
            ),
        ),
        binding(),
    )
    unavailable = prepare_retail_call(product_call(), None)
    assert unknown.safe_error == "unknown_tool" and unknown.params is None
    assert duplicate.safe_error == "invalid_parameters" and duplicate.params is None
    assert unavailable.safe_error == "retail_unavailable"
    assert isinstance(unavailable.params, ProductRunParams)
    assert unknown.tool_kind == duplicate.tool_kind == "unavailable"
    assert unavailable.tool_kind == "retail_sales"


def test_retail_configuration_error_log_never_contains_scope_or_dsn(caplog) -> None:
    sentinel = "credential-sentinel-never-log"
    settings = Settings(
        compass_retail_run_scope='{"unexpected":"' + sentinel + '"}',
        compass_retail_run_readonly_dsn=SecretStr("postgresql://" + sentinel),
    )
    assert binding_for(settings, CHAT) is None
    assert "configuration_code=invalid_scope" in caplog.text
    assert sentinel not in repr([record.__dict__ for record in caplog.records])


def test_csv_is_bom_crlf_formula_safe_and_standard_base64() -> None:
    step_id = uuid4()
    run_id = uuid4()
    result = build_analysis_file(
        ExportCandidate(step_id, "call_product", product_result()),
        run_id,
    )
    assert result.status == "ready"
    content = base64.b64decode(result.content_base64, validate=True)
    assert content.startswith(b"\xef\xbb\xbf") and b"\r\n" in content
    assert b"'=\xe5\x8d\xb1\xe9\x99\xa9\xe5\x85\xac\xe5\xbc\x8f" in content
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    assert rows[1][-3:] == ["1200", "800", "-400"]


def test_final_answer_budget_uses_serialized_json_bytes() -> None:
    exact = "x" * (RUN_STREAM_LIMITS["answerBytes"] - 2)
    assert AgentExecutor._final_answer(exact) == exact
    with pytest.raises(Exception, match="model_incomplete"):
        AgentExecutor._final_answer("x" * (RUN_STREAM_LIMITS["answerBytes"] - 1))
    # Each quote is one UTF-8 byte but two JSON bytes after escaping.
    with pytest.raises(Exception, match="model_incomplete"):
        AgentExecutor._final_answer('"' * (RUN_STREAM_LIMITS["answerBytes"] // 2))


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
    provider = cast(
        Any,
        SimpleNamespace(
            id=PROVIDER_ID,
            binding_version=7,
            alias="Retail Pro",
            capabilities=capabilities,
        ),
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
        executor = AgentExecutor(
            cast(Any, models),
            cast(Any, FakeRouting(selected_route())),
            retail_settings(),
        )
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT))

        result = await executor.run(run_command(), CHAT, control, lambda _event: None)

        assert result.status == "completed"
        assert models.logical_observation_stages == ["inference"]

    asyncio.run(scenario())


def test_executor_runs_tools_with_original_ids_continuation_and_csv() -> None:
    async def scenario():
        models = FakeModels(
            [
                model_result(
                    tool_calls=(
                        product_call(),
                        ToolCall(
                            id="call_unknown",
                            function=ToolCallFunction(name="other", arguments="{}"),
                        ),
                    ),
                    token="sealed-continuation",
                    finish="tool_calls",
                ),
                model_result(text="商品 SPU-1 变化最大。", finish="stop"),
            ]
        )
        queried = []

        async def retail(prepared, _control):
            queried.append(prepared.tool_call_id)
            return product_result()

        executor = AgentExecutor(
            cast(Any, models),
            cast(Any, FakeRouting(selected_route())),
            retail_settings(),
            retail_query=retail,
        )
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT))
        events = []
        result = await executor.run(run_command(), CHAT, control, events.append)
        assert result.status == "completed" and result.file.status == "ready"
        assert queried == ["call_product"]
        assert [event.type for event in events] == [
            "run.routed",
            "step.started",
            "tool.started",
            "tool.finished",
            "tool.started",
            "tool.finished",
            "step.finished",
            "step.started",
            "step.finished",
        ]
        second = models.requests[1][1]
        assert second.continuation_token == "sealed-continuation"
        assert [message.role for message in second.messages[-3:]] == [
            "assistant",
            "tool",
            "tool",
        ]
        assert [message.tool_call_id for message in second.messages[-2:]] == [
            "call_product",
            "call_unknown",
        ]
        assert models.requests[0][2]["expected_binding_version"] == 7
        assert models.requests[0][2]["deadline"] == control.deadline_at

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
            return [("unit_a_east", "unit_a_east", "on", True)]
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


def test_retail_cancel_waits_for_query_then_rolls_back_and_closes() -> None:
    async def scenario():
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT))
        started = asyncio.Event()
        release = asyncio.Event()
        connection = FakeConnection(started, release)
        prepared = prepare_retail_call(product_call(), binding())

        async def connect(_binding, _run_id):
            return connection

        task = asyncio.create_task(
            control.perform(lambda: query_retail(prepared, control, connect=connect))
        )
        await started.wait()
        control.request_stop("user")
        with pytest.raises(StopRequested):
            await task
        result = control.commit(
            RunCompleted(
                status="completed",
                finalAnswer="late",
                file=AnalysisFileNone(status="none"),
            )
        )
        assert result.status == "canceled" and result.cleanup == "confirmed"
        assert connection.canceled and connection.rolled_back and connection.closed
        assert connection.cursor_value.closed

    asyncio.run(scenario())


def test_retail_failed_cancel_is_confirmed_when_query_then_settles() -> None:
    class FailedCancelConnection(FakeConnection):
        async def cancel_safe(self, timeout):
            assert timeout == 1
            asyncio.get_running_loop().call_soon(self.release.set)
            raise RuntimeError("cancel request failed")

    async def scenario():
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT))
        started = asyncio.Event()
        release = asyncio.Event()
        connection = FailedCancelConnection(started, release)
        prepared = prepare_retail_call(product_call(), binding())

        async def connect(_binding, _run_id):
            return connection

        task = asyncio.create_task(
            control.perform(lambda: query_retail(prepared, control, connect=connect))
        )
        await started.wait()
        control.request_stop("user")
        with pytest.raises(StopRequested):
            await task
        result = control.commit(RunFailed(status="failed", errorCode="internal_error"))
        assert result.status == "canceled" and result.cleanup == "confirmed"
        assert connection.rolled_back and connection.closed

    asyncio.run(scenario())


def test_deadline_preserves_unconfirmed_phase_cleanup_receipt() -> None:
    async def scenario():
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT), lifetime_s=0.01)

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


def test_retail_connect_timeout_is_a_typed_tool_failure() -> None:
    async def scenario():
        registry = RunRegistry()
        control = registry.register(OwnerKey.from_context(CHAT))
        prepared = prepare_retail_call(product_call(), binding())

        async def connect(_binding, _run_id):
            raise TimeoutError()

        result = await query_retail(prepared, control, connect=connect)
        assert result.status == "error"
        assert result.error_code == "retail_query_timeout"
        assert control.cleanup_confirmed

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
        ack = await cancel_run(control.id, RunEmpty(), response, CHAT, registry)
        assert response.status_code == 202 and ack.root.status == "accepted"
        terminal = control.commit(
            RunCompleted(
                status="completed",
                finalAnswer="late",
                file=AnalysisFileNone(status="none"),
            )
        )
        assert terminal.status == "canceled"
        repeated = await cancel_run(control.id, RunEmpty(), Response(), CHAT, registry)
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
            patch("compass_agent.main.get_settings", return_value=Settings()),
            patch("compass_agent.model_api.get_settings", return_value=Settings()),
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
    caplog.set_level("INFO", logger="compass_agent.run_api")
    sentinel = "private-question-sentinel"
    settings = Settings(compass_agent_service_token="run-service-token")
    app.dependency_overrides[get_agent_executor] = lambda: executor
    wrong_purpose = CHAT.model_copy(update={"purpose": "module_read"})
    other_owner = CHAT.model_copy(update={"actor_user_id": uuid4()})
    try:
        with (
            patch("compass_agent.main.get_settings", return_value=settings),
            patch("compass_agent.model_api.get_settings", return_value=settings),
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
            assert started.status_code == 200 and executor.calls == 1, started.text
            assert started.headers["content-type"].startswith("text/event-stream")
            assert started.headers["x-accel-buffering"] == "no"
            assert "private" in started.headers["cache-control"]
            packets = [
                json.loads(line.removeprefix("data: "))
                for line in started.text.splitlines()
                if line.startswith("data: ")
            ]
            run_id = packets[0]["runId"]
            assert packets[-1]["event"]["type"] == "run.finished"

            hidden = client.post(
                f"/v1/analysis/runs/{run_id}/cancel",
                headers=auth_headers(other_owner),
                json={},
            )
            assert hidden.status_code == 404
            assert hidden.json()["error"]["code"] == "not_found"

            cancel = client.post(
                f"/v1/analysis/runs/{run_id}/cancel",
                headers=auth_headers(CHAT),
                json={},
            )
            assert cancel.status_code == 200
            assert cancel.json()["status"] == "already_terminal"
            assert cancel.headers["content-type"].startswith("application/json")
            assert "no-store" in cancel.headers["cache-control"]
            records = [
                record
                for record in caplog.records
                if getattr(record, "event_name", "").startswith("analysis.")
            ]
            names = {record.event_name for record in records}
            assert {
                "analysis.run.started",
                "analysis.step.started",
                "analysis.tool.started",
                "analysis.tool.finished",
                "analysis.step.finished",
                "analysis.run.finished",
                "analysis.run.cancel",
            } <= names
            correlated = [record for record in records if hasattr(record, "step_id")]
            assert correlated and {record.run_id for record in correlated} == {run_id}
            assert any(
                record.event_name == "analysis.tool.finished"
                and record.tool_call_id == "call_safe"
                and record.error_code == "retail_unavailable"
                for record in records
            )
            assert any(
                record.event_name == "analysis.run.finished"
                and record.status == "completed"
                and record.cleanup == "confirmed"
                for record in records
            )
            assert any(
                record.event_name == "analysis.run.cancel" and record.stop_reason == "user"
                for record in records
            )
            assert sentinel not in repr([record.__dict__ for record in records])
    finally:
        app.dependency_overrides.clear()


def test_cleanup_unconfirmed_terminal_log_is_code_and_id_only(caplog) -> None:
    caplog.set_level("INFO", logger="compass_agent.run_api")

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
        ack = await cancel_run(control.id, RunEmpty(), Response(), CHAT, registry)
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
