"""Adversarial protocol, authorization, output and cleanup boundaries."""

import asyncio
import gzip
import json
import zlib
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.systemone_decision import validate_answers
from compass_agent.main import app
from compass_agent.model_api import get_model_service
from compass_agent.models.errors import ModelError
from compass_agent.models.service import create_runtime
from compass_agent.schemas import ChatMessage, DecisionQuestion, ModelEvent, ToolCall
from compass_agent.settings import Settings
from fastapi.testclient import TestClient
from pydantic import ValidationError

from .provider_fixture import FixtureStream
from .test_models import MANAGE, TEST, create, generate, headers, invoke
from .test_models import system as system


def wire_response(content="ok", finish: str | None = "stop", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "fixture-response",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-v4-pro",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }


@pytest.mark.parametrize("streaming", [False, True])
def test_compressed_provider_response_is_decoded_once_before_protocol_validation(system, streaming):
    repo, _, service = system
    _, saved = create(repo)
    content = "literal [DONE] remains model text"
    value = wire_response(content)
    if streaming:
        value["object"] = "chat.completion.chunk"
        value["choices"][0]["delta"] = value["choices"][0].pop("message")
        body = ("data: " + json.dumps(value) + "\n\ndata: [DONE]\n\n").encode()
    else:
        body = json.dumps(value).encode()
    compressed = gzip.compress(body)
    source = FixtureStream([compressed[:7], compressed[7:]])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            stream=source,
            headers={
                "content-type": "text/event-stream" if streaming else "application/json",
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
        )

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handler))
    result = asyncio.run(invoke(service, saved.provider_id, generate(stream=streaming)))
    assert result.status == "completed" and result.text == content, result
    assert result.response_model == "deepseek-v4-pro"
    assert result.upstream_request_id == "fixture-response" and result.finish_reason == "stop"
    assert len(requests) == 1 and source.closed
    assert repo.call(result.call_id, TEST).status == "completed"


@pytest.mark.parametrize("length", [1048577, 2097153])
def test_compressed_content_limits_apply_to_decoded_size(system, length):
    repo, _, service = system
    _, saved = create(repo)
    body = gzip.compress(json.dumps(wire_response("x" * length)).encode())
    assert len(body) < 4096
    source = FixtureStream([body])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=source, headers={"content-encoding": "gzip"})

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handler))
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.status == "failed" and result.error_code == "response_too_large"
    assert not result.text and source.closed and len(requests) == 1


def test_invalid_content_encoding_fails_safely_and_closes_without_retry(system):
    repo, _, service = system
    _, saved = create(repo)
    source = FixtureStream([b"invalid gzip private provider body"])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=source, headers={"content-encoding": "gzip"})

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handler))
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.status == "failed" and result.error_code == "provider_protocol_error"
    assert not result.text and source.closed and len(requests) == 1
    assert "private provider body" not in repo.call(result.call_id, TEST).model_dump_json()


def test_compressed_sse_delivers_first_frame_before_upstream_finishes(system):
    repo, _, service = system
    _, saved = create(repo)

    async def scenario():
        delivered = asyncio.Event()
        compressor = zlib.compressobj(wbits=31)
        value = wire_response("first", finish=None)
        value["object"] = "chat.completion.chunk"
        value["choices"][0]["delta"] = value["choices"][0].pop("message")
        first = compressor.compress(("data: " + json.dumps(value) + "\n\n").encode())
        first += compressor.flush(zlib.Z_SYNC_FLUSH)
        value["choices"][0].update(delta={}, finish_reason="stop")
        last = (
            compressor.compress(("data: " + json.dumps(value) + "\n\ndata: [DONE]\n\n").encode())
            + compressor.flush()
        )

        class GatedStream(FixtureStream):
            async def __aiter__(self):
                yield first
                await asyncio.wait_for(delivered.wait(), timeout=2)
                yield last

        source = GatedStream([])
        service.runtime = create_runtime(
            repo.secrets,
            lambda: httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    stream=source,
                    headers={"content-type": "text/event-stream", "content-encoding": "gzip"},
                )
            ),
        )

        async def emit(event):
            if event.type == "text_delta":
                assert event.text == "first"
                delivered.set()

        result = await invoke(service, saved.provider_id, generate(stream=True), emit=emit)
        assert result.status == "completed" and delivered.is_set() and source.closed, result

    asyncio.run(scenario())


def test_sync_tool_metadata_is_normalized_and_thinking_continuation_is_preserved(system):
    repo, _, service = system
    _, saved = create(repo)
    tool = {
        "index": 0,
        "id": "provider-tool-1",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"count":2}'},
    }
    # Public request DTOs retain strict validation of caller-supplied tool records.
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ToolCall.model_validate(tool)
    requests = []
    streams = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            value = wire_response("", "tool_calls", [tool])
            value["choices"][0]["message"]["reasoning_content"] = "private tool reasoning"
        else:
            assistant = body["messages"][-2]
            assert assistant["reasoning_content"] == "private tool reasoning"
            assert "index" not in assistant["tool_calls"][0]
            assert body["messages"][-1]["tool_call_id"] == tool["id"]
            value = wire_response("1200")
        source = FixtureStream([gzip.compress(json.dumps(value).encode())])
        streams.append(source)
        return httpx.Response(200, stream=source, headers={"content-encoding": "gzip"})

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handler))
    request = generate(
        thinking=True,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "parameters": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    },
                },
            }
        ],
    )
    first = asyncio.run(invoke(service, saved.provider_id, request))
    assert first.status == "completed" and first.finish_reason == "tool_calls", first
    assert len(first.tool_calls) == 1 and first.continuation_token
    assert first.tool_calls[0].model_dump() == {k: v for k, v in tool.items() if k != "index"}
    assert "private tool reasoning" not in first.model_dump_json()
    continued = request.model_copy(
        update={
            "messages": [
                *request.messages,
                ChatMessage(role="assistant", content=first.text, tool_calls=first.tool_calls),
                ChatMessage(role="tool", tool_call_id=tool["id"], content='{"revenue":1200}'),
            ],
            "continuation_token": first.continuation_token,
        }
    )
    second = asyncio.run(invoke(service, saved.provider_id, continued))
    assert second.status == "completed" and second.text == "1200", second
    assert len(requests) == 2 and all(stream.closed for stream in streams)
    assert repo.call(first.call_id, TEST).status == "completed"
    assert repo.call(second.call_id, TEST).status == "completed"


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("lookup", "not-json"),
        ("lookup", "{}"),
        ("lookup", '{"count":"2"}'),
        ("other", '{"count":2}'),
    ],
)
def test_sync_tool_metadata_does_not_relax_function_and_argument_validation(
    system, name, arguments
):
    repo, _, service = system
    _, saved = create(repo)
    tool = {
        "index": 0,
        "id": "provider-tool-1",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=wire_response("", "tool_calls", [tool]))

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handler))
    result = asyncio.run(
        invoke(
            service,
            saved.provider_id,
            generate(
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "parameters": {
                                "type": "object",
                                "properties": {"count": {"type": "integer"}},
                                "required": ["count"],
                            },
                        },
                    }
                ]
            ),
        )
    )
    assert result.status == "failed" and result.error_code == "invalid_tool_arguments", result
    assert len(requests) == 1 and repo.call(result.call_id, TEST).status == "failed"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "model_authentication_failed"),
        (429, "model_rate_limited"),
        (500, "model_unavailable_upstream"),
        (307, "provider_protocol_error"),
    ],
)
def test_upstream_errors_have_one_request_and_never_read_body(system, status, code):
    repo, _, service = system
    _, saved = create(repo)
    requests = []

    class ForbiddenBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            pytest.fail("upstream error body must not be read")
            yield b""

        async def aclose(self):
            self.closed = True

    stream = ForbiddenBody()

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            status, stream=stream, headers={"location": "https://unapproved.example"}
        )

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handler))
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.error_code == code and result.status == "failed"
    assert len(requests) == 1 and stream.closed


@pytest.mark.parametrize(
    "content,finish,code",
    [
        ("", "stop", "empty_output"),
        ("partial", "length", "output_truncated"),
        ("denied", "content_filter", "model_refused"),
        ("x", "unsupported", "provider_protocol_error"),
    ],
)
def test_invalid_generation_terminal_states_are_not_completed(system, content, finish, code):
    repo, _, service = system
    _, saved = create(repo)
    service.runtime = create_runtime(
        repo.secrets,
        lambda: httpx.MockTransport(
            lambda _: httpx.Response(200, json=wire_response(content, finish))
        ),
    )
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.error_code == code and result.status != "completed"


def test_structured_output_failure_is_explicit_and_sensitive_text_never_stored(system):
    repo, _, service = system
    _, saved = create(repo)
    service.runtime = create_runtime(
        repo.secrets,
        lambda: httpx.MockTransport(
            lambda _: httpx.Response(200, json=wire_response('{"value":"wrong"}'))
        ),
    )
    result = asyncio.run(
        invoke(
            service,
            saved.provider_id,
            generate(
                output_format="schema",
                output_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
            ),
        )
    )
    assert result.error_code == "schema_validation_failed"
    assert result.validation.status == "invalid" and result.validation.paths == ["/value"]
    assert "wrong" not in repo.call(result.call_id, TEST).model_dump_json()


def test_sse_public_contract_has_one_final_and_no_reasoning_text(system):
    repo, _, service = system
    _, saved = create(repo)
    app.dependency_overrides[get_model_service] = lambda: service
    try:
        with (
            patch(
                "compass_agent.model_api.get_settings",
                return_value=Settings(compass_agent_service_token="unit-service-token"),
            ),
            TestClient(app) as client,
        ):
            response = client.post(
                f"/v1/models/providers/{saved.provider_id}/tests",
                headers=headers(TEST),
                json={
                    "call_id": str(uuid4()),
                    "input": generate(stream=True, thinking=True).model_dump(mode="json"),
                },
            )
            assert response.status_code == 200
            events = [
                ModelEvent.model_validate_json(line[6:]).root
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[0].type == "start" and events[-1].type == "final"
            assert sum(e.type == "final" for e in events) == 1
            assert (
                "text" not in events[-1].model_dump()
                and "tool_calls" not in events[-1].model_dump()
            )
            assert "private reasoning" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_finish_failure_returns_safe_failure_and_does_not_repeat_model(system):
    repo, upstream, service = system
    _, saved = create(repo)
    with patch.object(repo, "finish", return_value=False):
        result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.status == "failed" and result.error_code == "record_write_failed"
    assert len(upstream.requests) == 1
    assert repo.call(result.call_id, TEST).status == "running"


def test_size_limit_is_applied_while_reading_not_after_buffering(system):
    repo, _, service = system
    _, saved = create(repo)
    stream = FixtureStream([b"x" * (2 * 1024 * 1024 + 1)])
    service.runtime = create_runtime(
        repo.secrets, lambda: httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    )
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.error_code == "response_too_large" and stream.closed


def test_decision_invalid_candidates_probabilities_and_scope(system):
    question = {"q": DecisionQuestion(type="choice", criteria={"a": "A", "b": "B"})}
    invalid = [
        {"q": {"type": "choice", "choice": "c", "probabilities": {"a": 0.5, "b": 0.5}}},
        {"q": {"type": "choice", "choice": "a", "probabilities": {"a": 1}}},
        {"q": {"type": "choice", "choice": "a", "probabilities": {"a": 0.6, "b": 0.6}}},
        {"different": {"type": "noul", "noul": 0.5}},
    ]
    for answers in invalid:
        with pytest.raises(RuntimeFailure):
            validate_answers(question, answers)
    repo, _, _ = system
    body, saved = create(repo)
    with pytest.raises(ModelError, match="operation_conflict"):
        repo.save(body, MANAGE.model_copy(update={"actor_user_id": uuid4()}))
    assert repo.get(saved.provider_id).version == 1


def test_timeout_and_missing_metadata_are_preserved(system):
    repo, _, service = system
    _, saved = create(repo)
    count = 0

    async def timeout(request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(timeout))
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.error_code == "model_timeout", result
    assert count == 1 and result.upstream_outcome_unknown
    response = wire_response()
    response.pop("model")
    response.pop("id")
    service.runtime = create_runtime(
        repo.secrets, lambda: httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    )
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.status == "completed"
    assert result.response_model is None and result.upstream_request_id is None


def test_done_text_inside_content_does_not_end_stream(system):
    repo, _, service = system
    _, saved = create(repo)
    value = {
        "id": "fixture-response",
        "model": "deepseek-v4-pro",
        "created": 1,
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": "data: [DONE]"}, "finish_reason": "stop"}],
    }
    stream = FixtureStream([("data: " + json.dumps(value) + "\n\n").encode()])
    service.runtime = create_runtime(
        repo.secrets,
        lambda: httpx.MockTransport(
            lambda _: httpx.Response(
                200, stream=stream, headers={"content-type": "text/event-stream"}
            )
        ),
    )
    result = asyncio.run(invoke(service, saved.provider_id, generate(stream=True)))
    assert result.status == "incomplete" and result.error_code == "stream_interrupted"


@pytest.mark.parametrize("length,status", [(1048576, "completed"), (1048577, "failed")])
def test_sync_visible_output_budget_matches_stream_limit(system, length, status):
    repo, _, service = system
    _, saved = create(repo)
    service.runtime = create_runtime(
        repo.secrets,
        lambda: httpx.MockTransport(
            lambda _: httpx.Response(200, json=wire_response("x" * length))
        ),
    )
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.status == status
    if status == "failed":
        assert result.error_code == "response_too_large"
        assert result.text == "" and len(result.model_dump_json().encode()) < 4096


def test_interleaved_tool_names_and_arguments_are_assembled_per_index(system):
    repo, _, service = system
    _, saved = create(repo)
    tools = [
        {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
        for name in ["get_sales", "get_inventory"]
    ]
    result = asyncio.run(invoke(service, saved.provider_id, generate(stream=True, tools=tools)))
    assert result.status == "completed", result
    assert [tool.function.name for tool in result.tool_calls] == ["get_sales", "get_inventory"]
    assert len(result.tool_calls) == 2


def test_done_marker_in_model_text_and_comment_is_preserved(system):
    repo, _, service = system
    _, saved = create(repo)
    content = "literal data: [DONE] in a message"
    value = {
        "id": "fixture-response",
        "model": "deepseek-v4-pro",
        "created": 1,
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}],
    }
    chunks = [
        b": comment [DONE]\n\n",
        ("data: " + json.dumps(value) + "\n\n").encode(),
        b"data: [DONE]\n\n",
    ]
    stream = FixtureStream(chunks)
    service.runtime = create_runtime(
        repo.secrets,
        lambda: httpx.MockTransport(
            lambda _: httpx.Response(
                200, stream=stream, headers={"content-type": "text/event-stream"}
            )
        ),
    )
    result = asyncio.run(invoke(service, saved.provider_id, generate(stream=True)))
    assert result.status == "completed" and result.text == content, result


def test_committed_finish_remains_completed_when_event_loop_resumes_after_timeout(system):
    """A delayed wait callback must inspect the already-completed worker transaction."""
    import time

    repo, _, service = system
    _, saved = create(repo)
    original_finish = repo.finish

    async def scenario():
        loop = asyncio.get_running_loop()

        def finish(result):
            # Simulate a busy event loop while the independent DB thread commits promptly.
            loop.call_soon_threadsafe(time.sleep, 2.05)
            return original_finish(result)

        with patch.object(repo, "finish", side_effect=finish):
            return await invoke(service, saved.provider_id, generate())

    result = asyncio.run(scenario())
    assert repo.call(result.call_id, TEST).status == "completed"
    assert result.status == "completed" and result.error_code is None


def test_cancellation_during_final_write_propagates_after_metadata_finishes(system):
    import threading

    repo, _, service = system
    _, saved = create(repo)
    original_finish = repo.finish
    release = threading.Event()
    completed_call = []

    async def scenario():
        loop = asyncio.get_running_loop()
        writing = asyncio.Event()

        def finish(result):
            completed_call.append(result.call_id)
            loop.call_soon_threadsafe(writing.set)
            assert release.wait(timeout=1)
            return original_finish(result)

        with patch.object(repo, "finish", side_effect=finish):
            task = asyncio.create_task(invoke(service, saved.provider_id, generate()))
            await asyncio.wait_for(writing.wait(), timeout=5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    # Model work had already completed when the caller disconnected during its metadata write.
    assert repo.call(completed_call[0], TEST).status == "completed"


def test_unfinished_final_write_still_reports_failure_without_retry(system):
    import time

    repo, upstream, service = system
    _, saved = create(repo)
    attempts = []

    def finish(result):
        attempts.append(result.call_id)
        time.sleep(2.1)
        return True

    with patch.object(repo, "finish", side_effect=finish):
        result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.status == "failed" and result.error_code == "record_write_failed"
    assert repo.call(result.call_id, TEST).status == "running"
    assert len(attempts) == 1 and len(upstream.requests) == 1
