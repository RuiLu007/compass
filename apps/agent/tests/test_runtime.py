"""Direct kernel lifecycle acceptance without a database, HTTP API or real provider."""

import ast
import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from compass_agent.llm.capabilities import resolve
from compass_agent.llm.contracts import (
    AdapterKind,
    CallPolicy,
    ContinuationBinding,
    ExecutionRequest,
    GenerationOutput,
    InvocationContext,
    ModelSpec,
    ProviderAccess,
    ProviderAdapter,
    RuntimeCancelled,
)
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.policy import OutputLimits
from compass_agent.llm.runtime import Runtime
from compass_agent.schemas import (
    ChatMessage,
    GenerateInput,
    ModelUsage,
    PlatformScope,
    TextDeltaEvent,
)
from pydantic import SecretStr


class Adapter:
    def __init__(self, action):
        self.action = action
        self.calls = 0

    async def invoke(self, request, session):
        self.calls += 1
        return await self.action(request, session)


class Resource:
    def __init__(self, action=None):
        self.closed = False
        self.action = action

    async def aclose(self):
        if self.action:
            await self.action()
        self.closed = True


def request(seconds: float = 1, **options):
    started = asyncio.get_running_loop().time()
    provider = uuid4()
    return ExecutionRequest(
        InvocationContext(
            uuid4(),
            "verified",
            ContinuationBinding(
                actor=uuid4(),
                scope=PlatformScope(kind="platform"),
                provider=provider,
                binding_version=1,
            ),
        ),
        ProviderAccess(
            ModelSpec(
                provider,
                1,
                1,
                "deepseek",
                "llm",
                "litellm_chat",
                "deepseek-v4-pro",
                "deepseek/deepseek-v4-pro",
                "https://api.deepseek.com",
            ),
            SecretStr("secret"),
        ),
        GenerateInput(messages=[ChatMessage(role="user", content="original")], **options),
        CallPolicy(started, started + seconds),
    )


async def success(req, session):
    session.resources.sent = True
    session.progress.normal_eof = True
    session.progress.finish_reason = "stop"
    session.progress.usage = ModelUsage(input_tokens=3)
    session.progress.output = GenerationOutput(text="answer")
    return session.progress.output


def test_kernel_success_has_owned_values_and_does_not_mutate_caller():
    async def run():
        resource = Resource()
        original = request()

        async def action(req, session):
            req.input.messages[0].content = "changed inside adapter"
            session.resources.clients.append(resource)
            return await success(req, session)

        adapter = Adapter(action)
        mapping: dict[AdapterKind, ProviderAdapter] = {"litellm_chat": adapter}
        runtime = Runtime(mapping)
        mapping.clear()
        result = await runtime.invoke(original)
        assert result.status == "completed" and result.upstream_sent
        assert isinstance(result.output, GenerationOutput)
        assert result.output.text == "answer" and result.usage.input_tokens == 3
        assert result.usage.output_tokens is None and resource.closed and adapter.calls == 1
        assert isinstance(original.input, GenerateInput)
        assert original.input.messages[0].content == "original"
        assert "secret" not in repr(original.access) and "original" not in repr(original)

    asyncio.run(run())


def test_expired_deadline_and_capability_rejection_never_enter_adapter():
    async def run():
        adapter = Adapter(success)
        runtime = Runtime({"litellm_chat": adapter})
        req = request()
        expired = replace(
            req,
            policy=replace(
                req.policy,
                started_monotonic=req.policy.started_monotonic - 2,
                deadline_monotonic=req.policy.started_monotonic - 1,
            ),
        )
        result = await runtime.invoke(expired)
        assert result.error and result.error.code == "model_timeout" and not result.upstream_sent
        mismatch = replace(
            req,
            access=replace(req.access, spec=replace(req.access.spec, adapter="systemone_decision")),
        )
        result = await runtime.invoke(mismatch)
        assert result.error and result.error.code == "capability_not_supported"
        assert adapter.calls == 0

    asyncio.run(run())


def test_backpressure_consumes_the_same_deadline_and_preserves_partial_output():
    async def run():
        resource = Resource()
        calls = []

        async def emit(event):
            calls.append(event)
            await asyncio.Event().wait()

        async def action(req, session):
            session.resources.sent = session.resources.has_partial = True
            session.resources.clients.append(resource)
            session.progress.output = GenerationOutput(text="partial")
            await session.emit(TextDeltaEvent(call_id=req.context.call_id, text="partial"))

        adapter = Adapter(action)
        result = await Runtime({"litellm_chat": adapter}).invoke(request(0.03, stream=True), emit)
        assert (
            result.status == "incomplete" and result.error and result.error.code == "model_timeout"
        )
        assert isinstance(result.output, GenerationOutput)
        assert result.output.text == "partial" and result.upstream_outcome_unknown
        assert resource.closed and len(calls) == adapter.calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("during_cleanup", [False, True])
def test_cancellation_and_repeated_cancellation_close_owned_resources(during_cleanup):
    async def run():
        running, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def close():
            closing.set()
            await release.wait()

        resource = Resource(close)

        async def action(req, session):
            session.resources.sent = True
            session.resources.clients.append(resource)
            running.set()
            if during_cleanup:
                return await success(req, session)
            await asyncio.Event().wait()

        task = asyncio.create_task(Runtime({"litellm_chat": Adapter(action)}).invoke(request()))
        await running.wait()
        if during_cleanup:
            await closing.wait()
        task.cancel()
        await closing.wait()
        task.cancel()
        release.set()
        with pytest.raises(RuntimeCancelled) as caught:
            await task
        assert caught.value.result.status == "canceled"
        assert caught.value.result.upstream_outcome_unknown and resource.closed
        assert caught.value.result.error is None

    asyncio.run(run())


def test_stalled_resource_cleanup_is_bounded_without_restarting_execution():
    async def run():
        stopped = asyncio.Event()

        async def close():
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def action(req, session):
            session.resources.clients.append(Resource(close))
            return await success(req, session)

        req = request()
        req = replace(req, policy=replace(req.policy, cleanup_timeout_s=0.02))
        adapter = Adapter(action)
        result = await asyncio.wait_for(Runtime({"litellm_chat": adapter}).invoke(req), 0.3)
        await asyncio.wait_for(stopped.wait(), 0.3)
        assert result.status == "completed" and adapter.calls == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "code", ["model_rate_limited", "provider_protocol_error", "stream_interrupted"]
)
def test_safe_failures_keep_usage_without_retry(code):
    async def run():
        resource = Resource()

        async def action(req, session):
            session.resources.sent = True
            session.resources.clients.append(resource)
            session.progress.usage = ModelUsage(input_tokens=4)
            raise RuntimeFailure(code)

        adapter = Adapter(action)
        result = await Runtime({"litellm_chat": adapter}).invoke(request())
        assert result.error and result.error.code == code and result.usage.input_tokens == 4
        assert result.status == ("incomplete" if code == "stream_interrupted" else "failed")
        assert resource.closed and adapter.calls == 1

    asyncio.run(run())


def test_adapter_exit_without_terminal_protocol_metadata_is_incomplete():
    async def action(req, session):
        return GenerationOutput(text="a fragment")

    async def run():
        result = await Runtime({"litellm_chat": Adapter(action)}).invoke(request())
        assert (
            result.status == "incomplete"
            and result.error
            and result.error.code == "stream_interrupted"
        )

    asyncio.run(run())


def test_unhandled_adapter_failure_is_safe_and_not_a_protocol_claim():
    async def action(req, session):
        session.resources.sent = True
        raise ValueError("private provider payload")

    async def run():
        result = await Runtime({"litellm_chat": Adapter(action)}).invoke(request())
        assert result.error and result.error.code == "internal_error"
        assert result.status == "failed" and result.upstream_outcome_unknown
        assert "private provider payload" not in repr(result)

    asyncio.run(run())


def test_unknown_model_capability_is_diagnostic_only_and_limits_are_enforced(monkeypatch):
    from compass_agent.llm.sdk import sdk

    monkeypatch.setattr(sdk(), "get_supported_openai_params", lambda **kwargs: None)

    async def run():
        req = request(thinking=True)
        req = replace(
            req,
            access=replace(
                req.access,
                spec=replace(req.access.spec, api_model="custom", sdk_model="deepseek/custom"),
            ),
        )
        assert resolve(req.access.spec).thinking.state == "unknown"
        adapter = Adapter(success)
        runtime = Runtime({"litellm_chat": adapter})
        result = await runtime.invoke(req)
        assert result.error and result.error.code == "capability_not_supported"
        diagnostic = replace(req, context=replace(req.context, capability_mode="diagnostic"))
        assert (await runtime.invoke(diagnostic)).status == "completed"
        limited = replace(
            diagnostic, policy=replace(req.policy, limits=OutputLimits(visible_input_bytes=10))
        )
        result = await runtime.invoke(limited)
        assert result.error and result.error.code == "validation_failed"
        assert adapter.calls == 1

    asyncio.run(run())


def test_llm_modules_have_no_application_database_or_http_api_imports():
    root = Path(__file__).parents[1] / "src/compass_agent/llm"
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("compass_agent.models", "sqlalchemy", "fastapi")
                ), path


@pytest.mark.parametrize(
    "change",
    [
        {"max_attempts": 2},
        {"allow_fallback": True},
        {"deadline_monotonic": float("inf")},
        {"cleanup_timeout_s": 5},
    ],
)
def test_policy_rejects_unsupported_execution_controls(change):
    with pytest.raises(ValueError):
        CallPolicy(**({"started_monotonic": 1, "deadline_monotonic": 2} | change))
