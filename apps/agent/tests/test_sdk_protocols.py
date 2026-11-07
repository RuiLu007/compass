"""Native LiteLLM protocol regressions, using actual SDK conversion over mock HTTP."""

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from compass_agent.llm.catalog import binding, catalog_page
from compass_agent.models.entities import Provider
from compass_agent.models.errors import ModelError
from compass_agent.models.service import create_runtime
from compass_agent.schemas import ProviderPatch
from sqlalchemy import select
from sqlalchemy.orm import Session

from .provider_fixture import FixtureStream
from .test_models import CHAT, MANAGE, create, generate, invoke
from .test_models import system as system


@pytest.mark.parametrize(
    "identifier",
    [
        "anthropic/claude-sonnet-4-5",
        "gemini/gemini-2.5-flash",
        "azure/gpt-4o",
        "bedrock/anthropic.claude-3-sonnet-20240229-v1:0",
        "groq/llama-3.3-70b-versatile",
        "openrouter/anthropic/claude-sonnet-4",
        "openai/custom-unlisted-model",
    ],
)
def test_sdk_binding_has_no_vendor_or_model_whitelist(identifier):
    model = identifier.split("/", 1)[1]
    vendor, kind, adapter, sdk_model = binding(identifier, model)
    assert vendor == identifier.split("/")[0]
    assert (kind, adapter, sdk_model) == ("llm", "litellm_chat", identifier)


@pytest.mark.parametrize("vendor", ["anthropic", "gemini", "azure", "bedrock", "groq"])
def test_catalog_exposes_native_generation_models(vendor):
    result = catalog_page("", vendor, 1, 20)
    assert result.total > 0
    assert all(row.adapter_supported and row.chat_supported for row in result.items)


def test_custom_model_chat_admission_and_encrypted_connection_params(system):
    repo, _, _ = system
    command, saved = create(
        repo,
        catalog_id="azure/unlisted-deployment",
        api_model="unlisted-deployment",
        base_url="",
        api_key="",
        connection_params={"api_version": "2024-10-21", "azure_ad_token": "private-token"},
    )
    assert repo.save(command, MANAGE) == saved
    view = repo.get(saved.provider_id)
    assert view.chat_supported and view.has_connection_params and not view.has_api_key
    assert repo.available().items[0].id == saved.provider_id
    assert "private-token" not in view.model_dump_json()
    with Session(repo.engine) as session:
        row = session.get(Provider, saved.provider_id)
        assert row is not None
        assert "private-token" not in row.api_key_ciphertext
        key, params = repo.secrets.open_connection(row.id, row.api_key_ciphertext)
        assert key == "" and params["azure_ad_token"] == "private-token"
    repo.save(
        ProviderPatch(operation_id=uuid4(), expected_version=1, alias="renamed"),
        MANAGE,
        saved.provider_id,
    )
    assert repo.get(saved.provider_id).has_connection_params
    repo.save(
        ProviderPatch(operation_id=uuid4(), expected_version=2, connection_params={}),
        MANAGE,
        saved.provider_id,
    )
    assert not repo.get(saved.provider_id).has_connection_params
    assert repo.get(saved.provider_id).binding_version == 3


@pytest.mark.parametrize(
    "params",
    [
        {"model": "openai/other"},
        {"messages": []},
        {"client": {}},
        {"mock_response": "fake"},
        {"num_retries": 2},
        {"max_retries": 2},
        {"timeout": 999},
        {"drop_params": True},
        {"aws_bedrock_runtime_endpoint": "https://localhost/"},
        {"vertex_credentials": "/etc/passwd"},
    ],
)
def test_connection_settings_cannot_override_execution_or_endpoint_policy(system, params):
    repo, _, _ = system
    with pytest.raises(ModelError):
        create(repo, connection_params=params)
    with Session(repo.engine) as session:
        assert session.scalar(select(Provider)) is None


def anthropic_response(tool=False):
    return {
        "id": "msg_native",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [
            {
                "type": "tool_use",
                "id": "call_native",
                "name": "get_sales",
                "input": {"month": "2026-11"},
            }
        ]
        if tool
        else [{"type": "text", "text": "native ok"}],
        "stop_reason": "tool_use" if tool else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


def native_response(vendor, streaming):
    if vendor == "anthropic":
        value = anthropic_response()
        if not streaming:
            return httpx.Response(200, json=value)
        start = {**value, "content": [], "stop_reason": None}
        events = [
            ("message_start", {"type": "message_start", "message": start}),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "native ok"},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 2},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        chunks = [
            (f"event: {name}\ndata: {json.dumps(body)}\n\n").encode() for name, body in events
        ]
    else:
        value = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "native ok"}]},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 3,
                "candidatesTokenCount": 2,
                "totalTokenCount": 5,
            },
            "modelVersion": "gemini-2.5-flash",
        }
        if not streaming:
            return httpx.Response(200, json=value)
        chunks = [("data: " + json.dumps(value) + "\n\n").encode()]
    return httpx.Response(
        200, stream=FixtureStream(chunks), headers={"content-type": "text/event-stream"}
    )


@pytest.mark.parametrize(
    "vendor,model,base",
    [
        ("anthropic", "claude-sonnet-4-5", "https://api.anthropic.com"),
        ("gemini", "gemini-2.5-flash", "https://generativelanguage.googleapis.com"),
    ],
)
@pytest.mark.parametrize("streaming", [False, True])
def test_actual_sdk_native_protocol_sync_stream_and_chat(system, vendor, model, base, streaming):
    repo, _, service = system
    repo.allowed_origins += "," + base
    _, saved = create(repo, catalog_id=vendor + "/" + model, api_model=model, base_url=base)
    requests = []

    def handle(request):
        requests.append(request)
        return native_response(vendor, streaming)

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handle))
    result = asyncio.run(invoke(service, saved.provider_id, generate(stream=streaming), CHAT))
    assert result.status == "completed" and result.text == "native ok", result
    assert result.usage.input_tokens == 3 and len(requests) == 1
    body = json.loads(requests[0].content)
    if vendor == "anthropic":
        assert requests[0].url.path == "/v1/messages"
        assert requests[0].headers["x-api-key"] == "unit-key-never-return"
        assert body["messages"][0]["role"] == "user"
    else:
        assert "generateContent" in str(requests[0].url) or "streamGenerateContent" in str(
            requests[0].url
        )
        assert "contents" in body and "messages" not in body


def test_native_sdk_connection_options_are_forwarded_without_execution_override(system):
    repo, _, service = system
    _, saved = create(
        repo,
        catalog_id="bedrock/amazon.nova-pro-v1:0",
        api_model="amazon.nova-pro-v1:0",
        base_url="",
        api_key="",
        connection_params={
            "aws_region_name": "us-east-1",
            "aws_access_key_id": "private-access",
            "aws_secret_access_key": "private-secret",
        },
    )
    seen = {}

    async def completion(**params):
        seen.update(params)
        from litellm import ModelResponse

        return ModelResponse(
            model="amazon.nova-pro-v1:0",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "sdk result"},
                    "finish_reason": "stop",
                }
            ],
        )

    with patch("litellm.acompletion", completion):
        result = asyncio.run(invoke(service, saved.provider_id, generate(), CHAT))
    assert result.status == "completed", result
    assert seen["aws_region_name"] == "us-east-1" and seen["aws_access_key_id"] == "private-access"
    assert seen["model"] == "bedrock/amazon.nova-pro-v1:0" and seen["num_retries"] == 0
    assert seen["api_base"] is None and seen["api_key"] is None
    assert "private-access" not in result.model_dump_json()


@pytest.mark.parametrize(
    "vendor,model,base",
    [
        ("anthropic", "claude-sonnet-4-5", "https://api.anthropic.com"),
        ("gemini", "gemini-2.5-flash", "https://generativelanguage.googleapis.com"),
    ],
)
@pytest.mark.parametrize("streaming", [False, True])
def test_native_tools_use_sdk_protocol_and_keep_private_continuation(
    system, vendor, model, base, streaming
):
    repo, _, service = system
    repo.allowed_origins += "," + base
    _, saved = create(repo, catalog_id=vendor + "/" + model, api_model=model, base_url=base)
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) > 1:
            if vendor == "anthropic":
                assert body["messages"][-1]["content"][0]["type"] == "tool_result"
            else:
                assert "functionResponse" in json.dumps(body) or "function_response" in json.dumps(
                    body
                )
                assert "native-thought-signature" in json.dumps(body)
            return native_response(vendor, streaming)
        if vendor == "anthropic":
            if not streaming:
                return httpx.Response(200, json=anthropic_response(tool=True))
            events = [
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {**anthropic_response(), "content": [], "stop_reason": None},
                    },
                ),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "call_native",
                            "name": "get_sales",
                            "input": {},
                        },
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"month":"2026-11"}',
                        },
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                (
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 2},
                    },
                ),
                ("message_stop", {"type": "message_stop"}),
            ]
            return httpx.Response(
                200,
                stream=FixtureStream(
                    [
                        (f"event: {name}\ndata: {json.dumps(value)}\n\n").encode()
                        for name, value in events
                    ]
                ),
                headers={"content-type": "text/event-stream"},
            )
        payload = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "get_sales",
                                    "args": {"month": "2026-11"},
                                },
                                "thoughtSignature": "native-thought-signature",
                            }
                        ],
                    },
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 3,
                "candidatesTokenCount": 2,
                "totalTokenCount": 5,
            },
        }
        if streaming:
            return httpx.Response(
                200,
                stream=FixtureStream([("data: " + json.dumps(payload) + "\n\n").encode()]),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=payload)

    from compass_agent.schemas import ChatMessage

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handle))
    request = generate(
        stream=streaming,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_sales",
                    "parameters": {
                        "type": "object",
                        "properties": {"month": {"type": "string"}},
                        "required": ["month"],
                    },
                },
            }
        ],
    )
    first = asyncio.run(invoke(service, saved.provider_id, request, CHAT))
    assert first.status == "completed" and len(first.tool_calls) == 1, first
    assert first.continuation_token and "native-thought-signature" not in first.model_dump_json()
    continued = request.model_copy(
        update={
            "messages": [
                *request.messages,
                ChatMessage(role="assistant", content=first.text, tool_calls=first.tool_calls),
                ChatMessage(
                    role="tool", tool_call_id=first.tool_calls[0].id, content='{"sales":10}'
                ),
            ],
            "continuation_token": first.continuation_token,
        }
    )
    second = asyncio.run(invoke(service, saved.provider_id, continued, CHAT))
    assert second.status == "completed" and second.text == "native ok", second
    assert len(requests) == 2


@pytest.mark.parametrize(
    "vendor,model,base",
    [
        ("anthropic", "claude-sonnet-4-5", "https://api.anthropic.com"),
        ("gemini", "gemini-2.5-flash", "https://generativelanguage.googleapis.com"),
    ],
)
def test_native_provider_auth_errors_are_safe_and_not_retried(system, vendor, model, base):
    repo, _, service = system
    repo.allowed_origins += "," + base
    _, saved = create(repo, catalog_id=vendor + "/" + model, api_model=model, base_url=base)
    sent = []

    def handle(request):
        sent.append(request)
        return httpx.Response(401, json={"error": {"message": "private native error"}})

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handle))
    result = asyncio.run(invoke(service, saved.provider_id, generate(), CHAT))
    assert result.status == "failed" and result.error_code == "model_authentication_failed"
    assert len(sent) == 1 and "private native error" not in result.model_dump_json()


def test_model_discovery_does_not_start_provider_authentication():
    from compass_agent.llm.sdk import resolve_model

    resolve_model.cache_clear()
    with patch("litellm.get_llm_provider", side_effect=AssertionError("discovery attempted auth")):
        assert resolve_model("github_copilot/gpt-4o")[:2] == ("gpt-4o", "github_copilot")


@pytest.mark.parametrize("vendor", ["azure", "bedrock"])
@pytest.mark.parametrize("status", [200, 429])
def test_cloud_native_sdk_uses_connection_config_with_its_own_clients(
    system, monkeypatch, vendor, status
):
    repo, _, service = system
    requests = []
    if vendor == "azure":
        repo.allowed_origins += ",https://fixture.openai.azure.com"
        model, base, key = "teaching-deployment", "https://fixture.openai.azure.com", "cloud-key"
        options = {"api_version": "2024-10-21"}
        payload = {
            "id": "cloud-response",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "native cloud ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
    else:
        model, base, key = "amazon.nova-pro-v1:0", "", ""
        options = {
            "aws_region_name": "us-east-1",
            "aws_access_key_id": "AKIATEACHINGONLY",
            "aws_secret_access_key": "private-cloud-secret",
        }
        payload = {
            "output": {"message": {"role": "assistant", "content": [{"text": "native cloud ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 3, "outputTokens": 2, "totalTokens": 5},
            "metrics": {"latencyMs": 1},
        }
    _, saved = create(
        repo,
        catalog_id=vendor + "/" + model,
        api_model=model,
        base_url=base,
        api_key=key,
        connection_params=options,
    )

    async def send(client, request, **kwargs):
        requests.append(request)
        return httpx.Response(
            status,
            json=payload if status == 200 else {"error": {"message": "private cloud failure"}},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    service.runtime = create_runtime(repo.secrets)
    result = asyncio.run(invoke(service, saved.provider_id, generate(), CHAT))
    assert len(requests) == 1
    if status == 200:
        assert result.status == "completed" and result.text == "native cloud ok", result
        assert result.usage.input_tokens == 3
    else:
        assert result.status == "failed" and result.error_code == "model_rate_limited", result
    if vendor == "azure":
        assert "/openai/deployments/teaching-deployment/chat/completions" in requests[0].url.path
        assert requests[0].url.params["api-version"] == options["api_version"]
        assert requests[0].headers["api-key"] == key
    else:
        assert "/converse" in requests[0].url.path
        assert "us-east-1" in requests[0].url.host
        assert requests[0].headers["authorization"].startswith("AWS4-HMAC-SHA256")
    assert "private-cloud-secret" not in result.model_dump_json()


def test_old_credential_envelopes_remain_readable_and_can_gain_native_params(system):
    repo, _, _ = system
    _, saved = create(repo)
    with Session(repo.engine) as session, session.begin():
        row = session.get(Provider, saved.provider_id)
        assert row is not None
        row.api_key_ciphertext = repo.secrets.seal(
            "provider_key", {"provider_id": str(row.id), "key": "old-secret"}
        )
    assert (
        repo.get(saved.provider_id).has_api_key
        and not repo.get(saved.provider_id).has_connection_params
    )
    repo.save(
        ProviderPatch(
            operation_id=uuid4(),
            expected_version=1,
            connection_params={"extra_headers": {"X-Test": "native"}},
        ),
        MANAGE,
        saved.provider_id,
    )
    with Session(repo.engine) as session:
        row = session.get(Provider, saved.provider_id)
        assert row is not None
        key, options = repo.secrets.open_connection(row.id, row.api_key_ciphertext)
        assert key == "old-secret" and options["extra_headers"]["X-Test"] == "native"


@pytest.mark.parametrize("model,with_tools", [("gpt-5.4-pro", False), ("gpt-5.4", True)])
def test_openai_responses_bridge_is_handled_by_sdk_native_protocol(
    system, monkeypatch, model, with_tools
):
    repo, _, service = system
    repo.allowed_origins += ",https://api.openai.com"
    _, saved = create(
        repo, catalog_id="openai/" + model, api_model=model, base_url="https://api.openai.com/v1"
    )
    requests = []

    async def send(client, request, **kwargs):
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "resp_native",
                "object": "response",
                "created_at": 1,
                "model": model,
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "output": [
                    {
                        "type": "message",
                        "id": "msg_native",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "responses ok", "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    service.runtime = create_runtime(repo.secrets)
    request = generate(
        thinking=with_tools,
        tools=[
            {
                "type": "function",
                "function": {"name": "lookup", "parameters": {"type": "object", "properties": {}}},
            }
        ]
        if with_tools
        else [],
    )
    result = asyncio.run(invoke(service, saved.provider_id, request, CHAT))
    assert result.status == "completed" and result.text == "responses ok", result
    assert len(requests) == 1 and requests[0].url.path == "/v1/responses"
    assert "input" in json.loads(requests[0].content)
