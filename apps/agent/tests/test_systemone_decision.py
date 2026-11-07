"""System One integration through real configuration, Runtime and controlled HTTP."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from compass_agent.llm.catalog import catalog_page
from compass_agent.llm.policy import CallPolicy, OutputLimits
from compass_agent.models.errors import ModelError
from compass_agent.models.service import create_runtime
from compass_agent.schemas import DecisionInput, DecisionQuestion, ProviderPatch

from .provider_fixture import FixtureStream
from .test_models import CHAT, MANAGE, TEST, create, generate, invoke
from .test_models import system as system


def decision():
    return DecisionInput(
        state={"ticket": "请退款", "amount": 200},
        questions={
            "route": DecisionQuestion(
                type="choice",
                instructions={"question": "交给哪个团队？", "teams": ["售后", "销售"]},
                criteria={"aftercare": {"scope": ["退款"]}, "sales": None},
            ),
            "urgent": DecisionQuestion(type="noul", instructions=["是否紧急？"]),
            "severity": DecisionQuestion(type="score", criteria=["低", "高"]),
        },
    )


def wire_response():
    return {
        "model": "jev-custom-resolved-version",
        "answers": {
            "route": {
                "type": "choice",
                "choice": "aftercare",
                "confidence": 0.8,
                "probabilities": {"aftercare": 0.9, "sales": 0.1},
            },
            "urgent": {"type": "noul", "noul": 0.75},
            "severity": {
                "type": "score",
                "score": 0.6,
                "confidence": 0.5,
                "probabilities": {"0": 0.4, "1": 0.6},
                "legend": {"0": "低", "1": "高"},
            },
        },
        "usage": {"input_tokens": 123, "output_tokens": 17},
        "request_id": "systemone-request-id",
        "latency_ms": 10,
    }


def configure(repo, namespace="typesafe", model="jev-custom-version", base="/v1", **options):
    return create(
        repo,
        catalog_id=f"{namespace}/{model}",
        api_model=model,
        alias="Decision " + uuid4().hex,
        base_url="https://decision.example.com" + base,
        chat_visible=False,
        **options,
    )


def upstream(repo, service, response):
    requests = []

    async def handler(request):
        requests.append(request)
        return response

    service.runtime = create_runtime(repo.secrets, lambda: httpx.MockTransport(handler))
    return requests


@pytest.mark.parametrize(
    "namespace,base",
    [
        ("aliyun", "/compatible-mode/v1"),
        ("typesafe", "/v1"),
        ("systemone", "/custom/v2"),
    ],
)
def test_custom_models_share_protocol_auth_usage_and_edit_binding(system, namespace, base):
    repo, _, service = system
    body, saved = configure(repo, namespace, base=base + "/")
    raw = wire_response() | {"provider_extension": {"sample": "original-field"}}
    requests = upstream(repo, service, httpx.Response(200, json=raw))
    request = decision()
    result = asyncio.run(invoke(service, saved.provider_id, request))
    assert result.status == "completed", result
    assert len(requests) == 1
    assert requests[0].url.path == base + "/systemone"
    assert requests[0].headers["authorization"] == "Bearer unit-key-never-return"
    assert json.loads(requests[0].content) == {
        "model": body.api_model,
        "state": request.state,
        "questions": {k: q.model_dump(mode="json") for k, q in request.questions.items()},
    }
    assert result.raw_response == raw
    record = repo.call(result.call_id, TEST).model_dump_json()
    assert "original-field" not in record and "raw_response" not in record
    assert result.answers["severity"].score == 0.6
    assert result.response_model == "jev-custom-resolved-version"
    assert result.upstream_request_id == "systemone-request-id"
    assert result.usage.input_tokens == 123 and result.usage.output_tokens == 17
    assert repo.call(result.call_id, TEST).usage == result.usage
    assert saved.provider_id not in [item.id for item in repo.available().items]
    with pytest.raises(ModelError, match="chat_model_not_supported"):
        repo.begin(uuid4(), saved.provider_id, generate(), CHAT)
    repo.save(
        ProviderPatch(operation_id=uuid4(), expected_version=1, api_model="another/version-alias"),
        MANAGE,
        saved.provider_id,
    )
    assert repo.get(saved.provider_id).binding_version == 2
    result = asyncio.run(invoke(service, saved.provider_id, request))
    assert result.status == "completed"
    assert json.loads(requests[-1].content)["model"] == "another/version-alias"


def test_catalog_exposes_official_jev_and_aliyun_as_protocol_presets():
    for query, identifier in [
        ("jev-latest", "typesafe/jev-latest"),
        ("decision-model-preview", "aliyun/decision-model-preview"),
    ]:
        item = next(x for x in catalog_page(query, None, 1, 100).items if x.id == identifier)
        assert item.kind == "decision" and item.adapter_supported and not item.chat_supported
        assert item.source == "systemone"


@pytest.mark.parametrize(
    "options,error",
    [
        ({"api_key": ""}, "validation_failed"),
        ({"base_url": ""}, "endpoint_not_allowed"),
        ({"base_url": "https://decision.example.com/v1/systemone"}, "endpoint_not_allowed"),
        ({"base_url": "https://unapproved.example.com/v1"}, "endpoint_not_allowed"),
        ({"connection_params": {"api_version": "ignored"}}, "validation_failed"),
        ({"chat_visible": True}, "chat_model_not_supported"),
    ],
)
def test_decision_configuration_requires_effective_credentials_and_allowed_endpoint(
    system, options, error
):
    repo, _, _ = system
    with pytest.raises(ModelError, match=error):
        create(
            repo,
            catalog_id="typesafe/custom",
            api_model="custom",
            alias="Invalid",
            base_url=options.get("base_url", "https://decision.example.com/v1"),
            **({"chat_visible": False} | {k: v for k, v in options.items() if k != "base_url"}),
        )


@pytest.mark.parametrize(
    "status,error",
    [
        (401, "model_authentication_failed"),
        (403, "model_authentication_failed"),
        (429, "model_rate_limited"),
        (400, "provider_protocol_error"),
        (503, "model_unavailable_upstream"),
    ],
)
def test_upstream_errors_are_safe_recorded_and_never_retried(system, status, error):
    repo, _, service = system
    _, saved = configure(repo)
    requests = upstream(repo, service, httpx.Response(status, text="private upstream error"))
    result = asyncio.run(invoke(service, saved.provider_id, decision()))
    assert result.status == "failed" and result.error_code == error
    assert len(requests) == 1 and repo.call(result.call_id, TEST).status == "failed"
    assert "private upstream error" not in result.model_dump_json()


@pytest.mark.parametrize("variant", ["json", "list", "missing", "type", "probabilities", "usage"])
def test_malformed_answers_and_usage_fail_as_protocol_errors(system, variant):
    repo, _, service = system
    _, saved = configure(repo)
    value = wire_response()
    if variant == "list":
        value = []
    elif variant == "missing":
        del value["answers"]["urgent"]
    elif variant == "type":
        value["answers"]["route"]["type"] = "score"
    elif variant == "probabilities":
        value["answers"]["route"]["probabilities"]["sales"] = 0.9
    elif variant == "usage":
        value["usage"] = [123]
    response = httpx.Response(
        200, content=b"{invalid" if variant == "json" else json.dumps(value).encode()
    )
    requests = upstream(repo, service, response)
    result = asyncio.run(invoke(service, saved.provider_id, decision()))
    assert result.status == "failed" and result.error_code == "provider_protocol_error", result
    assert len(requests) == 1


def test_missing_usage_stays_unknown(system):
    repo, _, service = system
    _, saved = configure(repo)
    value = wire_response()
    del value["usage"]
    upstream(repo, service, httpx.Response(200, json=value))
    result = asyncio.run(invoke(service, saved.provider_id, decision()))
    assert result.status == "completed" and result.usage.input_tokens is None
    assert result.usage.output_tokens is None


@pytest.mark.parametrize("action", ["timeout", "cancel", "oversize"])
def test_decision_deadline_cancellation_and_response_limit_close_resources(system, action):
    repo, _, service = system
    _, saved = configure(repo)
    source = FixtureStream(
        [json.dumps(wire_response()).encode()], delay=0 if action == "oversize" else 1
    )
    requests = upstream(repo, service, httpx.Response(200, stream=source))

    async def scenario():
        request = decision()
        prepared = await service.prepare(uuid4(), saved.provider_id, request, TEST)
        loop = asyncio.get_running_loop()
        started = loop.time()
        prepared = replace(
            prepared,
            policy=CallPolicy(
                started,
                started + (0.05 if action == "timeout" else 2),
                limits=OutputLimits(visible_output_bytes=16)
                if action == "oversize"
                else OutputLimits(),
            ),
        )
        task = asyncio.create_task(service.run(prepared, request))
        if action == "cancel":
            async with asyncio.timeout(1):
                while not requests:
                    await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return repo.call(prepared.context.call_id, TEST)
        return await task

    result = asyncio.run(scenario())
    assert len(requests) == 1 and source.closed
    if action == "cancel":
        assert result.status == "canceled"
    else:
        assert result.status == "failed"
        assert result.error_code == (
            "model_timeout" if action == "timeout" else "response_too_large"
        )


def test_oversized_combined_raw_response_is_discarded(system):
    from compass_agent.llm.contracts import DecisionOutput
    from compass_agent.llm.runtime import Runtime

    from .test_runtime import Adapter

    repo, _, service = system
    _, saved = configure(repo)

    async def large_response(request, session):
        session.resources.sent = True
        session.progress.normal_eof = True
        session.progress.finish_reason = "stop"
        return DecisionOutput(raw_response={"payload": "x" * (2 * 1048576)})

    service.runtime = Runtime({"systemone_decision": Adapter(large_response)})
    result = asyncio.run(invoke(service, saved.provider_id, decision()))
    assert result.status == "failed" and result.error_code == "response_too_large"
    assert result.raw_response is None and len(result.model_dump_json().encode()) < 2097152
