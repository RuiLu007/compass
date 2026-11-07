"""Model boundary and real SDK-adapter tests with controlled upstream HTTP."""

import asyncio
import base64
import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from compass_agent.database import Base
from compass_agent.llm.admission import ExecutionGate
from compass_agent.llm.capabilities import validate_schema
from compass_agent.llm.continuation import restore
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.main import app
from compass_agent.model_api import get_model_service
from compass_agent.models.entities import ModelCall, ModelChange, Provider
from compass_agent.models.errors import ModelError
from compass_agent.models.repository import ModelRepository, now
from compass_agent.models.secrets import ModelContinuationCodec, SecretStore
from compass_agent.models.service import ModelService, create_runtime
from compass_agent.models.validation import validate_endpoint
from compass_agent.schemas import (
    ChatMessage,
    DecisionInput,
    DecisionQuestion,
    GenerateInput,
    PlatformScope,
    ProviderCreate,
    ProviderPatch,
    TenantScope,
    TrustedContext,
)
from compass_agent.settings import Settings
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from .provider_fixture import ProviderFixture

ORIGINS = "https://api.deepseek.com,https://decision.example.com"
ACTOR = uuid4()
MANAGE = TrustedContext(actor_user_id=ACTOR, scope=PlatformScope(kind="platform"), purpose="manage")
TEST = MANAGE.model_copy(update={"purpose": "test"})
CHAT = TrustedContext(
    actor_user_id=ACTOR,
    scope=TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
    purpose="chat",
)


def headers(context=MANAGE):
    return {
        "Authorization": "Bearer unit-service-token",
        "X-Compass-Context": base64.urlsafe_b64encode(context.model_dump_json().encode())
        .decode()
        .rstrip("="),
    }


@pytest.fixture
def system():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    repo = ModelRepository(engine, SecretStore(Fernet.generate_key().decode()), ORIGINS)
    fixture = ProviderFixture()
    service = ModelService(
        repo, create_runtime(repo.secrets, fixture.transport), gate=ExecutionGate()
    )
    yield repo, fixture, service
    engine.dispose()


def create(repo, **overrides):
    data = dict(
        operation_id=uuid4(),
        catalog_id="deepseek/deepseek-v4-pro",
        api_model="deepseek-v4-pro",
        alias="Pro",
        base_url="https://api.deepseek.com",
        api_key="unit-key-never-return",
        enabled=True,
        chat_visible=True,
    )
    data.update(overrides)
    body = ProviderCreate.model_validate(data)
    return body, repo.save(body, MANAGE)


def generate(text="你好", **kwargs):
    return GenerateInput(messages=[ChatMessage(role="user", content=text)], **kwargs)


async def invoke(service, provider_id, request, context=TEST, emit=None):
    call_id = uuid4()
    spec = await service.prepare(call_id, provider_id, request, context)
    result = await service.run(spec, request, emit=emit)
    return result


def test_configuration_atomic_idempotent_versions_and_masking(system):
    repo, _, _ = system
    body, saved = create(repo)
    assert repo.save(body, MANAGE) == saved
    assert repo.operation(body.operation_id) == saved
    with pytest.raises(ModelError, match="operation_conflict"):
        repo.save(body.model_copy(update={"alias": "Changed"}), MANAGE)
    with pytest.raises(ModelError, match="alias_conflict"):
        create(repo, alias="  pro  ")
    first = repo.get(saved.provider_id)
    assert first.has_api_key and "unit-key" not in first.model_dump_json()
    changed = repo.save(
        ProviderPatch(operation_id=uuid4(), expected_version=1, alias="Pro renamed"),
        MANAGE,
        saved.provider_id,
    )
    assert changed.version == 2 and repo.get(saved.provider_id).binding_version == 1
    with pytest.raises(ModelError, match="version_conflict"):
        repo.save(
            ProviderPatch(operation_id=uuid4(), expected_version=1, enabled=False),
            MANAGE,
            saved.provider_id,
        )
    repo.save(
        ProviderPatch(operation_id=uuid4(), expected_version=2, api_key="rotated-key"),
        MANAGE,
        saved.provider_id,
    )
    assert repo.get(saved.provider_id).binding_version == 3
    with Session(repo.engine) as session:
        row = session.get(Provider, saved.provider_id)
        assert row is not None and "rotated-key" not in row.api_key_ciphertext
        assert len(session.scalars(select(ModelChange)).all()) == 3
        assert "rotated-key" not in str(session.scalars(select(ModelChange.request_mac)).all())


def test_configuration_chat_gate_and_endpoint_security(system):
    repo, _, _ = system
    _, custom = create(repo, api_model="arbitrary-other-model", alias="Custom")
    assert repo.get(custom.provider_id).chat_supported
    _, decision = create(
        repo,
        catalog_id="aliyun/decision-model-preview",
        api_model="decision-model-preview",
        alias="Decision",
        base_url="https://decision.example.com/compatible-mode/v1",
        chat_visible=False,
    )
    assert not repo.get(decision.provider_id).chat_supported
    assert [item.id for item in repo.available().items] == [custom.provider_id]
    with pytest.raises(ModelError, match="chat_model_not_supported"):
        repo.begin(uuid4(), decision.provider_id, generate(), CHAT)
    for endpoint in [
        "http://api.deepseek.com",
        "https://api.deepseek.com@evil.com",
        "https://api.deepseek.com/v1?key=secret",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
    ]:
        with pytest.raises(ModelError):
            validate_endpoint(endpoint, "litellm_chat", ORIGINS)
    with pytest.raises(RuntimeFailure):
        validate_schema({"$ref": "https://example.com/schema"})


def test_http_authorization_validation_and_no_store(system):
    repo, _, service = system
    app.dependency_overrides[get_model_service] = lambda: service
    settings = Settings(compass_agent_service_token="unit-service-token")
    try:
        with (
            patch("compass_agent.model_api.get_settings", return_value=settings),
            TestClient(app) as client,
        ):
            assert client.get("/v1/models/providers").status_code == 401
            assert client.get("/v1/models/providers", headers=headers(CHAT)).status_code == 403
            body, saved = create(repo)
            response = client.get("/v1/models/providers", headers=headers())
            assert response.status_code == 200 and "no-store" in response.headers["cache-control"]
            assert "unit-key" not in response.text
            wrong = body.model_dump(mode="json") | {
                "api_key": "secret-in-invalid-body",
                "actor_user_id": str(uuid4()),
            }
            response = client.post("/v1/models/providers", headers=headers(), json=wrong)
            assert response.status_code == 422 and "secret-in-invalid-body" not in response.text
            response = client.post(
                f"/v1/models/providers/{saved.provider_id}/tests",
                headers=headers(TEST),
                json={"call_id": str(uuid4()), "input": generate().model_dump(mode="json")},
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "completed", response.text
    finally:
        app.dependency_overrides.clear()


def test_sdk_sync_and_thinking_wire_fields(system):
    repo, provider, service = system
    _, saved = create(repo)
    result = asyncio.run(
        invoke(service, saved.provider_id, generate(thinking=True, reasoning_effort="max"))
    )
    assert result.status == "completed", result
    assert result.text == "这是模型测试回复。"
    assert len(provider.requests) == 1
    wire = json.loads(provider.requests[0].content)
    assert wire["model"] == "deepseek-v4-pro"
    assert wire["thinking"] == {"type": "enabled"} and wire["reasoning_effort"] == "max"
    assert "private reasoning" not in result.model_dump_json()
    record = repo.call(result.call_id, TEST)
    assert record.status == "completed" and record.usage.input_tokens == 10
    with Session(repo.engine) as session:
        assert "private reasoning" not in str(session.get(ModelCall, result.call_id).__dict__)


@pytest.mark.parametrize("text,expected", [("你好", "completed"), ("E2E_TRUNCATED", "incomplete")])
def test_sdk_stream_requires_wire_done_and_has_single_final_metadata(system, text, expected):
    repo, provider, service = system
    _, saved = create(repo)
    events = []

    async def emit(event):
        events.append(event)

    result = asyncio.run(invoke(service, saved.provider_id, generate(text, stream=True), emit=emit))
    assert result.status == expected, result
    assert "".join(e.text for e in events if e.type == "text_delta") == "这是模型测试回复。"
    assert provider.streams[0].closed
    assert len(provider.requests) == 1


def test_schema_and_tool_continuation_keeps_private_state_bound(system):
    repo, provider, service = system
    _, saved = create(repo)
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "required": ["count"],
        "additionalProperties": False,
    }
    result = asyncio.run(
        invoke(service, saved.provider_id, generate(output_format="schema", output_schema=schema))
    )
    assert result.status == "completed" and result.validation.status == "valid", result
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": schema}}]
    first_request = generate(thinking=True, tools=tools)
    first = asyncio.run(invoke(service, saved.provider_id, first_request))
    assert first.status == "completed" and first.tool_calls and first.continuation_token, first
    assert "private reasoning" not in first.continuation_token
    continued = first_request.model_copy(
        update={
            "messages": [
                *first_request.messages,
                ChatMessage(role="assistant", content=first.text, tool_calls=first.tool_calls),
                ChatMessage(role="tool", content="result", tool_call_id=first.tool_calls[0].id),
            ],
            "continuation_token": first.continuation_token,
        }
    )
    second = asyncio.run(invoke(service, saved.provider_id, continued))
    assert second.status == "completed" and second.text == "工具结果已接收。", second
    assert json.loads(provider.requests[-1].content)["messages"][-2][
        "reasoning_content"
    ].startswith("private reasoning")
    spec = repo.begin(uuid4(), saved.provider_id, continued, TEST)
    with pytest.raises(RuntimeFailure, match="continuation_invalid"):
        restore(
            spec.context.continuation_binding.model_copy(
                update={"binding_version": spec.spec.binding_version + 1}
            ),
            continued,
            ModelContinuationCodec(repo.secrets),
        )
    altered = continued.model_copy(
        update={"messages": [ChatMessage(role="user", content="tampered"), *continued.messages[1:]]}
    )
    with pytest.raises(RuntimeFailure, match="continuation_invalid"):
        restore(spec.context.continuation_binding, altered, ModelContinuationCodec(repo.secrets))


def test_decision_protocol_has_no_chat_messages_and_is_normalized(system):
    repo, provider, service = system
    _, saved = create(
        repo,
        catalog_id="aliyun/decision-model-preview",
        api_model="decision-model-preview",
        alias="Decision",
        base_url="https://decision.example.com/compatible-mode/v1",
        chat_visible=False,
    )
    request = DecisionInput(
        state={"inventory": 2},
        questions={
            "route": DecisionQuestion(type="choice", criteria={"yes": "选择", "no": "不选择"}),
            "confidence": DecisionQuestion(type="noul"),
            "rank": DecisionQuestion(type="score", criteria=["低", "高"]),
        },
    )
    result = asyncio.run(invoke(service, saved.provider_id, request))
    assert result.status == "completed" and result.validation.status == "valid", result
    wire = json.loads(provider.requests[0].content)
    assert set(wire) == {"model", "state", "questions"}
    assert str(provider.requests[0].url).endswith("/compatible-mode/v1/systemone")
    assert result.answers["route"].choice == "yes"


def test_failed_request_not_retried_and_call_id_never_replayed(system):
    repo, provider, service = system
    _, saved = create(repo)
    result = asyncio.run(invoke(service, saved.provider_id, generate("E2E_RETRY_400")))
    assert result.status == "failed" and len(provider.requests) == 1
    with pytest.raises(ModelError, match="call_already_exists"):
        repo.begin(result.call_id, saved.provider_id, generate(), TEST)
    with pytest.raises(ModelError, match="not_found"):
        repo.call(result.call_id, TEST.model_copy(update={"actor_user_id": uuid4()}))
    with Session(repo.engine) as session, session.begin():
        row = session.get(ModelCall, result.call_id)
        assert row is not None
        row.status = "running"
        row.deadline_at = now() - timedelta(seconds=1)
    assert repo.call(result.call_id, TEST).status == "incomplete"


def test_cancel_closes_response_and_persists_canceled(system):
    repo, provider, service = system
    _, saved = create(repo)

    async def scenario():
        request = generate("E2E_SLOW", stream=True)
        call_id = uuid4()
        spec = await service.prepare(call_id, saved.provider_id, request, TEST)
        task = asyncio.create_task(service.run(spec, request))
        async with asyncio.timeout(5):
            while not provider.streams:
                if task.done():
                    pytest.fail(str(task.result()))
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return call_id

    call_id = asyncio.run(scenario())
    assert provider.streams[0].closed
    assert repo.call(call_id, TEST).status == "canceled"
    assert repo.call(call_id, TEST).upstream_outcome_unknown
