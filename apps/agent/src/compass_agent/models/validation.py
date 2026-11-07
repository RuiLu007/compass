"""Application-owned endpoint allowlist and public validation error mapping."""

import ipaddress
import json
from urllib.parse import urlsplit

from compass_agent.models.errors import ModelError


def validate_endpoint(value: str, adapter: str, origins: str) -> str:
    if not value.strip() and adapter == "litellm_chat":
        return ""
    try:
        url = urlsplit(value.strip().rstrip("/"))
        allowed = (
            json.loads(origins)
            if origins.strip().startswith("[")
            else [v.strip().rstrip("/") for v in origins.split(",")]
        )
        port = url.port
    except (ValueError, TypeError) as exc:
        raise ModelError("endpoint_not_allowed") from exc
    origin = f"https://{url.hostname}" + (f":{port}" if port and port != 443 else "")
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or origin not in allowed
    ):
        raise ModelError("endpoint_not_allowed")
    try:
        if not ipaddress.ip_address(url.hostname).is_global:
            raise ModelError("endpoint_not_allowed")
    except ValueError:
        if url.hostname == "localhost" or url.hostname.endswith(".localhost"):
            raise ModelError("endpoint_not_allowed") from None
    if adapter == "systemone_decision" and url.path.endswith("/systemone"):
        raise ModelError("endpoint_not_allowed") from None
    return origin + url.path


def validate_connection_params(value: dict, origins: str) -> dict:
    """Accept SDK connection settings, keeping inference/policy fields application-owned."""
    from litellm.types.router import LiteLLM_Params

    reserved = {
        "model",
        "custom_llm_provider",
        "api_key",
        "api_base",
        "base_url",
        "max_retries",
        "num_retries",
        "timeout",
        "request_timeout",
        "stream_timeout",
        "drop_params",
        "mock_response",
        "model_info",
        "metadata",
        "litellm_trace_id",
        "use_litellm_proxy",
        "litellm_credential_name",
        "client",
        "http_client",
    }
    allowed = set(LiteLLM_Params.model_fields) | {"extra_headers", "default_headers"}
    try:
        encoded = json.dumps(value, allow_nan=False)
        if len(encoded.encode()) > 32768:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise ModelError("validation_failed") from exc
    for key, item in value.items():
        if (
            key not in allowed
            or key in reserved
            or key.startswith("_")
            or "router" in key
            or "callback" in key
            or key.endswith(("_file", "_path"))
        ):
            raise ModelError("validation_failed")
        if key.endswith(("_endpoint", "_url", "_base")):
            if not isinstance(item, str):
                raise ModelError("validation_failed")
            validate_endpoint(item, "litellm_chat", origins)
        if key == "vertex_credentials" and isinstance(item, str):
            try:
                if not isinstance(json.loads(item), dict):
                    raise ValueError()
            except ValueError as exc:
                raise ModelError("validation_failed") from exc
    return value
