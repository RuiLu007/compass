"""Safe model failures without HTTP or application dependencies."""

from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import ValidationError

from compass_agent.schemas import ModelInputError

type RuntimeErrorCode = Literal[
    ModelInputError,
    "validation_failed",
    "capability_not_supported",
    "model_authentication_failed",
    "model_rate_limited",
    "model_timeout",
    "model_unavailable_upstream",
    "provider_protocol_error",
    "stream_interrupted",
    "output_truncated",
    "model_refused",
    "empty_output",
    "invalid_json",
    "schema_validation_failed",
    "invalid_tool_arguments",
    "continuation_invalid",
    "continuation_too_large",
    "response_too_large",
    "internal_error",
    "runtime_overloaded",
    "resource_cleanup_failed",
]


@dataclass(frozen=True, slots=True)
class RuntimeErrorInfo:
    code: RuntimeErrorCode
    recovery: Literal["not_sent_transient", "rejected_transient", "never"] = "never"


class RuntimeFailure(Exception):
    def __init__(
        self,
        code: RuntimeErrorCode,
        *,
        recovery: Literal["not_sent_transient", "rejected_transient", "never"] = "never",
    ):
        self.error = RuntimeErrorInfo(code, recovery)
        super().__init__(code)


def model_input_error(body: object) -> ModelInputError | None:
    """Classify stable provider error fields without reusing response messages."""
    if not isinstance(body, dict):
        return None
    details = body.get("error", body)
    if not isinstance(details, dict):
        return None
    code = details.get("code")
    parameter = details.get("param")
    if code in ("context_length_exceeded", "context_window_exceeded", "input_token_limit_exceeded"):
        return "input_token_limit_exceeded"
    if code in ("max_tokens_exceeded", "max_output_tokens_exceeded", "output_token_limit_exceeded"):
        return "output_token_limit_exceeded"
    if code in ("invalid_value", "value_above_maximum") and parameter in (
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
    ):
        return (
            "output_token_limit_exceeded"
            if code == "value_above_maximum"
            else "invalid_model_input"
        )
    if code in ("invalid_value", "invalid_parameter", "unsupported_parameter", "invalid_type") or (
        details.get("type") in ("invalid_request_error", "invalid_argument")
    ):
        return "invalid_model_input"
    return None


def error_code(error: Exception) -> RuntimeErrorCode:
    if isinstance(error, RuntimeFailure):
        return error.error.code
    if isinstance(error, (TimeoutError, httpx.TimeoutException)) or type(error).__name__ in {
        "Timeout",
        "APITimeoutError",
    }:
        return "model_timeout"
    if isinstance(error, (ValidationError, ValueError, KeyError, TypeError)):
        return "provider_protocol_error"
    if type(error).__name__ == "UnsupportedParamsError":
        return "capability_not_supported"
    status = getattr(error, "status_code", None)
    if type(error).__name__ == "ContextWindowExceededError":
        return "input_token_limit_exceeded"
    if status in {400, 422}:
        classified = model_input_error(getattr(error, "body", None))
        if classified is not None:
            return classified
    if status in {401, 403}:
        return "model_authentication_failed"
    if status == 429:
        return "model_rate_limited"
    if isinstance(error, httpx.HTTPError) or status is not None:
        return "model_unavailable_upstream"
    return "internal_error"


def recovery_for(error, *, sent, has_partial, http_status=None):
    if has_partial or http_status is not None and 200 <= http_status < 300:
        return "never"
    if isinstance(error, RuntimeFailure):
        return error.error.recovery
    code = error_code(error)
    status = getattr(error, "status_code", None)
    if code in {"model_rate_limited", "model_unavailable_upstream"}:
        if status == 429 or isinstance(status, int) and 500 <= status < 600:
            return "rejected_transient"
        if not sent and isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
            return "not_sent_transient"
    return "never"
