"""Safe model failures without HTTP or application dependencies."""

from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import ValidationError

type RuntimeErrorCode = Literal[
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
