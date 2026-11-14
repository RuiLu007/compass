"""The single source for model/Chat capability checks used by API and UI."""

import json

from jsonschema import Draft202012Validator, SchemaError

from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.policy import OutputLimits
from compass_agent.llm.sdk import sdk
from compass_agent.schemas import Capability, DecisionInput, GenerateInput, ModelCapabilities


def chat_supported(binding) -> bool:
    return binding.kind == "llm" and binding.adapter == "litellm_chat" and bool(binding.sdk_model)


def supported_parameters(vendor: str, api_model: str) -> list[str] | None:
    try:
        return sdk().get_supported_openai_params(model=api_model, custom_llm_provider=vendor)
    except Exception:
        return None


def capabilities(kind: str, vendor: str = "", api_model: str = "") -> ModelCapabilities:
    yes = Capability(state="supported", reason="已实现协议；真实接入状态见测试结果")
    no = Capability(state="unsupported", reason="该模型不支持此能力")
    unknown = Capability(state="unknown", reason="能力待核验，可在管理端诊断测试")
    deepseek = vendor == "deepseek" and api_model in {"deepseek-v4-pro", "deepseek-flash"}
    params = supported_parameters(vendor, api_model) if kind == "llm" else None

    def parameter(name):
        if kind != "llm":
            return no
        return unknown if params is None else yes if name in params else no

    # Model metadata is authoritative when available; protocol-wide params are a fallback.
    def metadata(name, fallback):
        info = (
            sdk().model_cost.get(vendor + "/" + api_model) or sdk().model_cost.get(api_model) or {}
        )
        value = info.get(name)
        return yes if value is True else no if value is False else fallback

    thinking = yes if deepseek else metadata("supports_reasoning", parameter("reasoning_effort"))
    return ModelCapabilities(
        sync=yes,
        stream=parameter("stream"),
        tools=yes if deepseek else metadata("supports_function_calling", parameter("tools")),
        thinking=thinking if kind == "llm" else no,
        json_output=yes if deepseek else parameter("response_format"),
        schema_output=Capability(state="supported", reason="JSON 输出 + 本地 Schema 校验")
        if deepseek
        else parameter("response_format"),
        reasoning_efforts=["low", "high", "max"]
        if deepseek
        else ["low", "medium", "high"]
        if thinking.state == "supported"
        else [],
    )


def validate_schema(schema: dict, limit: int = 16384):
    if len(json.dumps(schema).encode()) > limit:
        raise RuntimeFailure("validation_failed")

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(child, str) or not child.startswith("#")
                ):
                    raise RuntimeFailure("validation_failed")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise RuntimeFailure("validation_failed") from exc


def resolve(binding) -> ModelCapabilities:
    return capabilities(binding.kind, binding.vendor, binding.api_model)


def validate_input(
    binding,
    request: GenerateInput | DecisionInput,
    mode: str = "verified",
    limits: OutputLimits | None = None,
):
    limits = limits or OutputLimits()
    expected = "litellm_chat" if binding.kind == "llm" else "systemone_decision"
    if binding.kind not in {"llm", "decision"} or binding.adapter != expected:
        raise RuntimeFailure("capability_not_supported")
    if expected == "litellm_chat" and binding.sdk_model != binding.vendor + "/" + binding.api_model:
        raise RuntimeFailure("capability_not_supported")
    if expected == "systemone_decision" and (
        not binding.api_model.strip() or binding.sdk_model is not None
    ):
        raise RuntimeFailure("capability_not_supported")
    if mode not in {"verified", "diagnostic"}:
        raise RuntimeFailure("validation_failed")
    caps = resolve(binding)
    required = [caps.stream if request.stream else caps.sync]
    if isinstance(request, GenerateInput):
        if request.tools:
            required.append(caps.tools)
        if request.thinking:
            required.append(caps.thinking)
        if request.output_format == "json":
            required.append(caps.json_output)
        if request.output_format == "schema":
            required.append(caps.schema_output)
    if any(
        c.state == "unsupported" or (c.state == "unknown" and mode != "diagnostic")
        for c in required
    ):
        raise RuntimeFailure("capability_not_supported")
    if (binding.kind == "llm") != isinstance(request, GenerateInput):
        raise RuntimeFailure("capability_not_supported")
    data = request.model_dump(mode="json", exclude={"continuation_token"})
    if len(json.dumps(data, ensure_ascii=False).encode()) > limits.visible_input_bytes:
        raise RuntimeFailure("validation_failed")
    if isinstance(request, GenerateInput):
        if len(request.messages) > limits.messages or len(request.tools) > limits.tools:
            raise RuntimeFailure("validation_failed")
        if (
            request.thinking
            and request.reasoning_effort is not None
            and (caps.reasoning_efforts and request.reasoning_effort not in caps.reasoning_efforts)
        ):
            raise RuntimeFailure("capability_not_supported")
        if request.output_schema is not None:
            validate_schema(request.output_schema, limits.schema_bytes)
        names = [tool.function.name for tool in request.tools]
        if len(set(names)) != len(names):
            raise RuntimeFailure("validation_failed")
        for tool in request.tools:
            validate_schema(tool.function.parameters, limits.schema_bytes)
        pending = set()
        for msg in request.messages:
            if msg.role == "tool":
                if msg.tool_call_id not in pending:
                    raise RuntimeFailure("invalid_tool_arguments")
                pending.remove(str(msg.tool_call_id))
            else:
                if pending:
                    raise RuntimeFailure("invalid_tool_arguments")
                if msg.tool_calls:
                    ids = [t.id for t in msg.tool_calls]
                    if len(set(ids)) != len(ids):
                        raise RuntimeFailure("invalid_tool_arguments")
                    pending = set(ids)
        if pending:
            raise RuntimeFailure("invalid_tool_arguments")


def capability_summary(request) -> list[str]:
    if isinstance(request, DecisionInput):
        return ["decision"]
    return (
        ["stream" if request.stream else "sync"]
        + (["tools"] if request.tools else [])
        + (["thinking"] if request.thinking else [])
        + ([request.output_format] if request.output_format != "text" else [])
    )
