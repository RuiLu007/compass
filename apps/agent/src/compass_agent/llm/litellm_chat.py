"""LiteLLM generation adapter with explicit parameters and single-request transport."""

import asyncio
import hashlib
import json
from copy import deepcopy
from itertools import islice
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from compass_agent.llm.continuation import restore, seal
from compass_agent.llm.contracts import (
    CallSession,
    ContinuationCodec,
    ExecutionRequest,
    ModelOutput,
)
from compass_agent.llm.errors import RuntimeFailure, error_code, recovery_for
from compass_agent.llm.sdk import sdk
from compass_agent.llm.transport import SingleAttemptTransport
from compass_agent.schemas import (
    ModelUsage,
    OutputValidation,
    ReasoningMetaEvent,
    TextDeltaEvent,
    ToolCall,
    ToolDeltaEvent,
)


def usage_from(value):
    if not value:
        return ModelUsage()
    details = value.get("completion_tokens_details") or {}
    return ModelUsage(
        input_tokens=value.get("prompt_tokens"),
        output_tokens=value.get("completion_tokens"),
        reasoning_tokens=details.get("reasoning_tokens"),
    )


def enforce_output_budget(result, reasoning, tools, limits):
    size = len(result.text.encode()) + sum(
        len(json.dumps(tool, ensure_ascii=False, separators=(",", ":")).encode()) for tool in tools
    )
    if (
        size > limits.visible_output_bytes
        or len(reasoning.encode()) > limits.private_reasoning_bytes
    ):
        result.text = ""
        result.tool_calls = ()
        raise RuntimeFailure("response_too_large")


def complete_output(request, result, normal_eof):
    if not normal_eof or result.finish_reason is None:
        raise RuntimeFailure("stream_interrupted")
    if result.finish_reason == "length":
        raise RuntimeFailure("output_truncated")
    if result.finish_reason in {"content_filter", "refusal"}:
        raise RuntimeFailure("model_refused")
    if result.finish_reason not in {"stop", "tool_calls"}:
        raise RuntimeFailure("provider_protocol_error")
    definitions = {tool.function.name: tool.function.parameters for tool in request.tools}
    if len({tool.id for tool in result.tool_calls}) != len(result.tool_calls):
        raise RuntimeFailure("invalid_tool_arguments")
    for tool in result.tool_calls:
        if tool.function.name not in definitions:
            raise RuntimeFailure("invalid_tool_arguments")
        try:
            args = json.loads(tool.function.arguments)
            Draft202012Validator(definitions[tool.function.name]).validate(args)
        except Exception as exc:
            raise RuntimeFailure("invalid_tool_arguments") from exc
    if not result.text.strip() and not result.tool_calls:
        raise RuntimeFailure("empty_output")
    if request.output_format != "text" and not result.tool_calls:
        result.validation = OutputValidation(status="invalid", mode=request.output_format)
        try:
            parsed = json.loads(result.text)
        except ValueError as exc:
            raise RuntimeFailure("invalid_json") from exc
        if request.output_schema is not None:
            errors = list(
                islice(Draft202012Validator(request.output_schema).iter_errors(parsed), 20)
            )
            if errors:
                result.validation.paths = [
                    "/" + "/".join(str(part) for part in e.absolute_path) for e in errors[:20]
                ]
                raise RuntimeFailure("schema_validation_failed")
        result.validation.status = "valid"
    result.normal_eof = normal_eof


class LiteLLMChatAdapter:
    def __init__(self, continuation: ContinuationCodec, transport_factory=None):
        self.continuation = continuation
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
        from compass_agent.schemas import GenerateInput

        if not isinstance(request, GenerateInput):
            raise RuntimeFailure("capability_not_supported")
        result, resources, emit = session.progress, session.resources, session.emit
        litellm = sdk()
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
        from openai import AsyncOpenAI

        litellm.suppress_debug_info = True
        litellm.turn_off_message_logging = True
        messages, private = restore(
            invocation.context.continuation_binding, request, self.continuation
        )
        if request.output_format != "text":
            instruction = "Return a JSON object."
            if request.output_schema is not None:
                instruction += " Follow this JSON Schema: " + json.dumps(
                    request.output_schema, ensure_ascii=False
                )
            messages = [{"role": "system", "content": instruction}, *messages]
        # Keep the existing DeepSeek SDK-version workaround for its wire responses.
        # Other providers, including OpenAI Responses bridges, use native SDK normalization.
        info = (
            litellm.model_cost.get(access.spec.sdk_model or "")
            or litellm.model_cost.get(access.spec.api_model)
            or {}
        )
        wire_guard = access.spec.vendor == "deepseek"

        def public_tool_id(value):
            # SDKs can embed private provider state in tool IDs; keep native IDs sealed.
            return (
                value if wire_guard else "call_" + hashlib.sha256(value.encode()).hexdigest()[:32]
            )

        sdk_client = None
        if wire_guard or self.transport_factory:
            transport = SingleAttemptTransport(
                resources,
                self.transport_factory() if self.transport_factory else None,
                observe_openai=wire_guard,
            )
            timeout = httpx.Timeout(
                max(
                    0.001, invocation.policy.deadline_monotonic - asyncio.get_running_loop().time()
                ),
                connect=invocation.policy.connect_timeout_s,
            )
            if access.spec.vendor == "openai" and info.get("mode") != "responses":
                client = httpx.AsyncClient(
                    transport=transport, timeout=timeout, follow_redirects=False
                )
                resources.clients.append(client)
                sdk_client = AsyncOpenAI(
                    api_key=access.api_key.get_secret_value() or None,
                    base_url=access.spec.api_base or None,
                    max_retries=0,
                    http_client=client,
                )
            else:
                sdk_client = AsyncHTTPHandler(
                    transport=transport, timeout=timeout, follow_redirects=False
                )
                resources.clients.append(sdk_client.client)
        params: dict[str, Any] = {
            **deepcopy(access.connection_params),
            "model": access.spec.sdk_model,
            "api_base": access.spec.api_base or None,
            "api_key": access.api_key.get_secret_value() or None,
            "messages": deepcopy(messages),
            "stream": request.stream,
            "max_tokens": request.max_tokens,
            "num_retries": 0,
            "max_retries": 0,
            "drop_params": False,
            "timeout": max(
                0.001, invocation.policy.deadline_monotonic - asyncio.get_running_loop().time()
            ),
            "caching": False,
        }
        if sdk_client is not None:
            params["client"] = sdk_client
        if request.thinking and access.spec.vendor != "deepseek":
            params["reasoning_effort"] = request.reasoning_effort or "high"
        if access.spec.vendor == "deepseek":
            # 1.106.6 removes top-level reasoning_effort; preserve it in the wire body.
            extra_body: dict[str, Any] = {
                "thinking": {"type": "enabled" if request.thinking else "disabled"}
            }
            if request.thinking:
                extra_body["reasoning_effort"] = request.reasoning_effort or "high"
            params["extra_body"] = extra_body
        if request.stream:
            # LiteLLM uses this to emit normalized usage for native protocols, too.
            params["stream_options"] = {"include_usage": True}
        if request.tools:
            params["tools"] = [tool.model_dump() for tool in request.tools]
        if request.output_format != "text":
            params["response_format"] = {"type": "json_object"}
        if emit:
            await emit(
                ReasoningMetaEvent(
                    call_id=invocation.context.call_id,
                    thinking=request.thinking,
                    reasoning_effort=request.reasoning_effort if request.thinking else None,
                )
            )
        if not wire_guard and not self.transport_factory:
            # Native SDKs may perform authentication before their inference request.
            resources.sent = True
        if session.observation:
            from compass_agent.observability.litellm_receipt import callbacks

            params.update(callbacks(session.observation))
        response: Any = await litellm.acompletion(**params)
        if request.stream and callable(getattr(response, "aclose", None)):
            resources.clients.append(response)
        reasoning = ""
        provider_message = {}
        chunks = []
        chunk_bytes = 0
        parts = {}
        if request.stream:
            async for chunk in response:
                value = chunk.model_dump()
                chunk_bytes += len(json.dumps(value, ensure_ascii=False, default=str).encode())
                if chunk_bytes > invocation.policy.limits.wire_response_bytes:
                    raise RuntimeFailure("response_too_large")
                chunks.append(chunk)
                result.response_model = value.get("model") or result.response_model
                result.upstream_request_id = value.get("id") or result.upstream_request_id
                if value.get("usage"):
                    result.usage = usage_from(value["usage"])
                choices = value.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    result.finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("refusal"):
                    raise RuntimeFailure("model_refused")
                reasoning += (
                    delta.get("reasoning_content")
                    or (delta.get("provider_specific_fields") or {}).get("reasoning_content")
                    or ""
                )
                text = delta.get("content") or ""
                result.text += text
                enforce_output_budget(result, reasoning, parts.values(), invocation.policy.limits)
                if text and emit:
                    resources.has_partial = True
                    await emit(TextDeltaEvent(call_id=invocation.context.call_id, text=text))
                for part in delta.get("tool_calls") or []:
                    index = part.get("index", 0)
                    if (
                        not isinstance(index, int)
                        or index < 0
                        or index >= invocation.policy.limits.tools
                    ):
                        raise RuntimeFailure("provider_protocol_error")
                    target = parts.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    tool_id = part.get("id")
                    if tool_id:
                        if target["id"] and target["id"] != tool_id:
                            raise RuntimeFailure("provider_protocol_error")
                        target["id"] = tool_id
                    function = part.get("function") or {}
                    name = function.get("name")
                    if name:
                        target["function"]["name"] += name
                    args = function.get("arguments") or ""
                    target["function"]["arguments"] += args
                    enforce_output_budget(
                        result, reasoning, parts.values(), invocation.policy.limits
                    )
                    if emit:
                        resources.has_partial = True
                        await emit(
                            ToolDeltaEvent(
                                call_id=invocation.context.call_id,
                                index=index,
                                id=public_tool_id(tool_id) if tool_id else None,
                                name=name,
                                arguments=args,
                            )
                        )
            result.tool_calls = tuple(
                ToolCall.model_validate({**parts[i], "id": public_tool_id(parts[i]["id"])})
                for i in sorted(parts)
            )
            if result.tool_calls:
                assembled = litellm.stream_chunk_builder(chunks=chunks, messages=messages)
                if assembled is not None:
                    provider_message = assembled.model_dump()["choices"][0]["message"]
            normal_eof = resources.saw_done if wire_guard else True
        else:
            value = response.model_dump()
            choices = value.get("choices") or []
            if len(choices) != 1:
                raise RuntimeFailure("provider_protocol_error")
            choice = choices[0]
            message = choice.get("message") or {}
            provider_message = message
            if message.get("refusal"):
                raise RuntimeFailure("model_refused")
            result.text = message.get("content") or ""
            result.tool_calls = tuple(
                ToolCall.model_validate(
                    {
                        "id": public_tool_id(tool["id"]),
                        "type": tool["type"],
                        "function": {
                            "name": tool["function"]["name"],
                            "arguments": tool["function"]["arguments"],
                        },
                    }
                )
                for tool in message.get("tool_calls") or []
            )
            reasoning = (
                message.get("reasoning_content")
                or (message.get("provider_specific_fields") or {}).get("reasoning_content")
                or ""
            )
            result.response_model = value.get("model")
            result.upstream_request_id = value.get("id")
            result.finish_reason = choice.get("finish_reason")
            result.usage = usage_from(value.get("usage"))
            normal_eof = True
        enforce_output_budget(
            result, reasoning, (t.model_dump() for t in result.tool_calls), invocation.policy.limits
        )
        if wire_guard:
            # Retain strict metadata validation for the legacy DeepSeek SDK workaround.
            result.finish_reason = resources.wire_finish_reason
            result.response_model = resources.wire_response_model
            result.upstream_request_id = resources.wire_request_id
        if session.observation:
            from compass_agent.observability.litellm_receipt import estimate

            # Optional SDK pricing must never change the inference outcome.
            try:
                priced = (
                    litellm.stream_chunk_builder(chunks=chunks, messages=messages)
                    if request.stream and chunks
                    else response
                )
                estimate(session.observation, litellm, priced, access.spec.sdk_model)
            except Exception:
                pass
        complete_output(request, result, normal_eof)
        result.continuation_token = seal(
            invocation.context.continuation_binding,
            request,
            result,
            reasoning,
            private,
            self.continuation,
            provider_message,
        )
        if (
            result.continuation_token
            and len(result.continuation_token.encode())
            > invocation.policy.limits.continuation_bytes
        ):
            raise RuntimeFailure("continuation_too_large")
        return result.output
