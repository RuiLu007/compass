"""Single-attempt System One protocol shared by Alibaba Cloud and TypeSafe/JEV."""

import asyncio
import json
import math

import httpx

from compass_agent.llm.contracts import CallSession, DecisionOutput, ExecutionRequest, ModelOutput
from compass_agent.llm.errors import RuntimeFailure, error_code, recovery_for
from compass_agent.llm.transport import SingleAttemptTransport
from compass_agent.schemas import DecisionAnswer, DecisionInput, ModelUsage, OutputValidation


def status_error(status):
    if status in {401, 403}:
        raise RuntimeFailure("model_authentication_failed")
    if status == 429:
        raise RuntimeFailure("model_rate_limited")
    if not 200 <= status < 300:
        raise RuntimeFailure(
            "model_unavailable_upstream" if status >= 500 else "provider_protocol_error"
        )


def validate_answers(questions, answers):
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise RuntimeFailure("provider_protocol_error")
    output = {}
    for key, question in questions.items():
        answer = DecisionAnswer.model_validate(answers[key])
        if answer.type != question.type:
            raise RuntimeFailure("provider_protocol_error")
        if question.type == "noul":
            if answer.noul is None or not math.isfinite(answer.noul):
                raise RuntimeFailure("provider_protocol_error")
        else:
            expected = (
                set(question.criteria)
                if question.type == "choice"
                else {str(i) for i in range(len(question.criteria))}
            )
            probs = answer.probabilities
            if (
                probs is None
                or set(probs) != expected
                or any(not math.isfinite(x) or not 0 <= x <= 1 for x in probs.values())
                or abs(sum(probs.values()) - 1) > 0.001
            ):
                raise RuntimeFailure("provider_protocol_error")
            if question.type == "choice" and answer.choice not in expected:
                raise RuntimeFailure("provider_protocol_error")
            if question.type == "score":
                if (
                    answer.score is None
                    or not math.isfinite(answer.score)
                    or not 0 <= answer.score <= len(question.criteria) - 1
                ):
                    raise RuntimeFailure("provider_protocol_error")
                legend = {str(i): value for i, value in enumerate(question.criteria)}
                if answer.legend != legend:
                    raise RuntimeFailure("provider_protocol_error")
            if answer.confidence is not None and not math.isfinite(answer.confidence):
                raise RuntimeFailure("provider_protocol_error")
        output[key] = answer
    return output


class SystemOneDecisionAdapter:
    def __init__(self, transport_factory=None):
        self.transport_factory = transport_factory

    async def invoke(self, request: ExecutionRequest, session: CallSession) -> ModelOutput:
        try:
            return await self._invoke(request, session)
        except Exception as exc:
            raise RuntimeFailure(
                error_code(exc),
                recovery=recovery_for(
                    exc, sent=session.resources.sent, has_partial=session.resources.has_partial
                ),
            ) from None

    async def _invoke(self, invocation: ExecutionRequest, session: CallSession) -> ModelOutput:
        access, request = invocation.access, invocation.input
        if not isinstance(request, DecisionInput):
            raise RuntimeFailure("capability_not_supported")
        result, resources = session.progress, session.resources
        transport = SingleAttemptTransport(
            resources, self.transport_factory() if self.transport_factory else None
        )
        client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                max(
                    0.001, invocation.policy.deadline_monotonic - asyncio.get_running_loop().time()
                ),
                connect=invocation.policy.connect_timeout_s,
            ),
            follow_redirects=False,
        )
        resources.clients.append(client)
        data = request.model_dump(mode="json")
        async with client.stream(
            "POST",
            access.spec.api_base + "/systemone",
            headers={"Authorization": "Bearer " + access.api_key.get_secret_value()},
            json={
                "model": access.spec.api_model,
                "state": data["state"],
                "questions": data["questions"],
            },
        ) as response:
            status_error(response.status_code)
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > invocation.policy.limits.visible_output_bytes:
                    raise RuntimeFailure("response_too_large")
                body.extend(chunk)
            value = json.loads(body)
        if not isinstance(value, dict):
            raise RuntimeFailure("provider_protocol_error")
        result.output = DecisionOutput(raw_response=value)
        result.output = DecisionOutput(
            answers=validate_answers(request.questions, value.get("answers")), raw_response=value
        )
        result.response_model = value.get("model")
        result.upstream_request_id = value.get("request_id")
        usage = value.get("usage")
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise RuntimeFailure("provider_protocol_error")
        result.usage = ModelUsage(
            input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens")
        )
        result.validation = OutputValidation(status="valid", mode="decision")
        result.normal_eof = True
        result.finish_reason = "stop"
        return result.output
