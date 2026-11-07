"""Opt-in model I/O projection, credential redaction and bounded JSON encoding."""

import json
import re

REDACTED = "[REDACTED]"
PRIVATE_KEYS = frozenset(
    "apikey authorization password passwd secret clientsecret accesstoken refreshtoken "
    "token cookie setcookie credentials continuationtoken reasoning reasoningcontent "
    "encryptedcontent signature privateassistants".split()
)
ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_ -]?key|password|passwd|client[_ -]?secret|secret|"
    r"access[_ -]?token|refresh[_ -]?token|continuation[_ -]?token|authorization|"
    r"cookie|token)\b[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|(?:Bearer|Basic)\s+[^\s,;}\]]+|[^\s,;}\]]+)"
)
AUTH = re.compile(r"(?i)\b(Bearer|Basic)\s+[a-z0-9._~+/=-]+")
KEY = re.compile(r"\b(?:sk-(?:lf-)?|pk-lf-|AIza)[a-zA-Z0-9_-]{8,}")


def redact(text, secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    text = ASSIGNMENT.sub(lambda match: match[1] + '"' + REDACTED + '"', text)
    text = AUTH.sub(lambda match: match[1] + " " + REDACTED, text)
    return KEY.sub(REDACTED, text)


class ContentPolicy:
    def __init__(self, enabled, max_bytes):
        self.enabled, self.max_bytes = enabled, max_bytes

    def encode(self, value, secrets=()):
        remaining, truncated = 4096, False

        def clean(item, depth=0):
            nonlocal remaining, truncated
            remaining -= 1
            if depth > 16 or remaining < 0:
                truncated = True
                return "[TRUNCATED]"
            if isinstance(item, str):
                return redact(item, secrets)
            if isinstance(item, dict):
                result = {}
                for key, val in item.items():
                    if remaining <= 0:
                        truncated = True
                        break
                    key = redact(str(key), secrets)
                    private = re.sub(r"[^a-z0-9]", "", key.lower()) in PRIVATE_KEYS
                    result[key] = REDACTED if private else clean(val, depth + 1)
                return result
            if isinstance(item, (list, tuple)):
                result = []
                for val in item:
                    if remaining <= 0:
                        truncated = True
                        break
                    result.append(clean(val, depth + 1))
                return result
            if item is None or type(item) in {bool, int, float}:
                return item
            raise TypeError("Unsupported observation content")

        def dump(item):
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

        encoded = dump(clean(value))
        if len(encoded.encode()) <= self.max_bytes:
            return encoded, truncated
        # Keep the envelope valid JSON and cut only between Unicode characters.
        low, high = 0, min(len(encoded), self.max_bytes)
        while low < high:
            mid = (low + high + 1) // 2
            preview = dump({"truncated": True, "preview": encoded[:mid]})
            if len(preview.encode()) <= self.max_bytes:
                low = mid
            else:
                high = mid - 1
        return dump({"truncated": True, "preview": encoded[:low]}), True

    def accepts(self, value):
        if not self.enabled or type(value) is not str or len(value.encode()) > self.max_bytes:
            return False
        try:
            return self.encode(json.loads(value))[0] == value
        except (TypeError, ValueError, RecursionError):
            return False


def tool_snapshot(tool):
    return {"function": {"name": tool.function.name, "arguments": tool.function.arguments}}


def input_snapshot(value):
    from compass_agent.schemas import DecisionInput

    if isinstance(value, DecisionInput):
        return {
            "state": value.state,
            "questions": {
                key: question.model_dump(exclude_none=True)
                for key, question in value.questions.items()
            },
        }
    return {
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                **(
                    {"tool_calls": [tool_snapshot(tool) for tool in message.tool_calls]}
                    if message.tool_calls
                    else {}
                ),
            }
            for message in value.messages
        ],
        **({"tools": [tool.model_dump() for tool in value.tools]} if value.tools else {}),
        "output_format": value.output_format,
        **({"output_schema": value.output_schema} if value.output_schema is not None else {}),
    }


def output_snapshot(value):
    from compass_agent.llm.contracts import DecisionOutput, GenerationOutput

    if isinstance(value, GenerationOutput) and (value.text or value.tool_calls):
        return {
            "role": "assistant",
            "content": value.text,
            **(
                {"tool_calls": [tool_snapshot(tool) for tool in value.tool_calls]}
                if value.tool_calls
                else {}
            ),
        }
    if isinstance(value, DecisionOutput) and value.answers:
        return {
            "answers": {
                key: answer.model_dump(exclude_none=True) for key, answer in value.answers.items()
            }
        }
    return None
