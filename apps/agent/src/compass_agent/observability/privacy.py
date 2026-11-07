"""Finite metadata construction and dependency-log redaction."""

import json
import logging
from math import isfinite

FIELDS = frozenset(
    "call_id parent_call_id attempt_id tenant_id workspace_id actor_id purpose stage "
    "provider_id model adapter config_version binding_version dictionary_version routing_version "
    "category_id scene_id difficulty mode reason status error_code sent outcome_unknown "
    "cleanup_confirmed first_visible_ms queue_wait_ms usage_complete unknown_usage_attempts "
    "cost_state cost_source reasoning_tokens instance_id simulation "
    "content_input_truncated content_output_truncated content_output_partial".split()
)
DEPENDENCY_PREFIXES = ("litellm", "LiteLLM", "openai", "opentelemetry", "httpx", "httpcore")


def scalar(value):
    if type(value) is str:
        return value if len(value) <= 256 else None
    if type(value) is bool or type(value) is int and 0 <= value <= 2**53 - 1:
        return value
    if type(value) is float and isfinite(value) and value >= 0:
        return value
    return None


def fields(values):
    return {
        "langfuse.observation.metadata." + key: clean
        for key, value in values.items()
        if key in FIELDS and (clean := scalar(value)) is not None
    }


class DependencyFilter(logging.Filter):
    def filter(self, record):
        if not record.name.startswith(DEPENDENCY_PREFIXES):
            return True
        record.msg = "telemetry_dependency_event"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def install_dependency_filters():
    prefixes = DEPENDENCY_PREFIXES
    names = set(logging.Logger.manager.loggerDict) | set(prefixes)
    for name in names:
        if name.startswith(prefixes):
            logger = logging.getLogger(name)
            if not any(isinstance(f, DependencyFilter) for f in logger.filters):
                logger.addFilter(DependencyFilter())
    # Handler filters also cover child loggers created after lazy SDK imports.
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, DependencyFilter) for f in handler.filters):
            handler.addFilter(DependencyFilter())


class DiagnosticFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "event": "observability",
                "level": record.levelname,
                "instance_id": getattr(record, "instance_id", ""),
                "reason": getattr(record, "safe_reason", "dependency_failure"),
                **getattr(record, "safe_counts", {}),
            },
            separators=(",", ":"),
        )


def diagnostic(instance_id, reason, counts=None):
    logger = logging.getLogger("compass.observability")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(DiagnosticFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(logging.INFO)
    safe_counts = {
        key: value
        for key, value in (counts or {}).items()
        if key in {"queue_size", "dropped", "export_failures", "instrumentation_errors"}
        and type(value) is int
        and value >= 0
    }
    logger.log(
        logging.INFO if reason == "shutdown" else logging.WARNING,
        "observability",
        extra={"instance_id": instance_id, "safe_reason": reason, "safe_counts": safe_counts},
    )
