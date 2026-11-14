"""Prepare authorized execution content before storing or sending it."""

import json

from compass_agent.observability.content import ContentPolicy, redact

DETAIL_BYTES = 32 * 1024


def detail_text(value: str) -> str:
    clean = redact(value, ())
    # JSON escapes control characters, so keep providers' text safe for a single SSE frame.
    clean = "".join(character for character in clean if character >= " " or character in "\t\n\r")
    encoded = clean.encode(errors="replace")
    text = encoded[:DETAIL_BYTES].decode(errors="ignore")
    if len(encoded) <= DETAIL_BYTES:
        return text
    suffix = "\n…（内容已截断）"
    return encoded[: DETAIL_BYTES - len(suffix.encode())].decode(errors="ignore") + suffix


def detail_json(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        encoded, truncated = ContentPolicy(True, DETAIL_BYTES).encode(json.loads(value))
        if truncated:
            return encoded
        formatted = json.dumps(json.loads(encoded), ensure_ascii=False, indent=2)
        return formatted if len(formatted.encode()) <= DETAIL_BYTES else encoded
    except (TypeError, ValueError, RecursionError):
        return None


def legacy_node(value: dict) -> dict:
    return {key: field for key, field in value.items() if key != "detail"}
