"""Opaque tool continuations; protocol checks depend only on a narrow codec."""

import hashlib
import json

from compass_agent.llm.contracts import ContinuationBinding, ContinuationPayload
from compass_agent.llm.errors import RuntimeFailure


def canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def visible_messages(request):
    return [m.model_dump(exclude_none=True) for m in request.messages]


def restore(binding: ContinuationBinding, request, codec):
    messages = visible_messages(request)
    private = {}
    if request.continuation_token:
        value = codec.open(request.continuation_token)
        if value.binding != binding:
            raise RuntimeFailure("continuation_invalid")
        count = value.visible_count
        if not 0 < count < len(messages):
            raise RuntimeFailure("continuation_invalid")
        if hashlib.sha256(canonical(messages[:count])).hexdigest() != value.visible_digest:
            raise RuntimeFailure("continuation_invalid")
        extra = messages[count:]
        ids = [m.get("tool_call_id") for m in extra]
        if (
            any(m["role"] != "tool" for m in extra)
            or len(set(ids)) != len(ids)
            or set(ids) != set(value.allowed_tool_call_ids)
        ):
            raise RuntimeFailure("continuation_invalid")
        private = value.private_assistants
        tool_ids = {}
        for index, state in private.items():
            if not index.isascii() or not index.isdecimal():
                raise RuntimeFailure("continuation_invalid")
            i = int(index)
            if not 0 <= i < count or messages[i]["role"] != "assistant":
                raise RuntimeFailure("continuation_invalid")
            if isinstance(state, str):
                messages[i]["reasoning_content"] = state
            else:
                # Match public opaque tool IDs to server-sealed native SDK IDs.
                visible_tools = messages[i].get("tool_calls") or []
                native_tools = state.get("tool_calls") or []
                if native_tools and len(native_tools) != len(visible_tools):
                    raise RuntimeFailure("continuation_invalid")
                tool_ids.update(
                    {
                        public["id"]: native["id"]
                        for public, native in zip(
                            visible_tools, native_tools, strict=bool(native_tools)
                        )
                    }
                )
                messages[i].update(state)
        for message in messages:
            if message["role"] == "tool" and message.get("tool_call_id") in tool_ids:
                message["tool_call_id"] = tool_ids[message["tool_call_id"]]
    elif request.thinking and any(m["role"] == "tool" for m in messages):
        raise RuntimeFailure("continuation_invalid")
    return messages, private


def seal(
    binding: ContinuationBinding, request, result, reasoning, private, codec, provider_message=None
):
    if not result.tool_calls:
        return None
    messages = visible_messages(request)
    index = len(messages)
    messages.append(
        {
            "role": "assistant",
            "content": result.text,
            "tool_calls": [t.model_dump() for t in result.tool_calls],
        }
    )
    state = {
        key: value
        for key, value in (provider_message or {}).items()
        if key not in {"role", "content"} and value is not None
    }
    if state.get("tool_calls"):
        state["tool_calls"] = [
            {key: value for key, value in tool.items() if key != "index" and value is not None}
            for tool in state["tool_calls"]
        ]
    if reasoning:
        state["reasoning_content"] = reasoning
    if state:
        private = {**private, str(index): state}
    return codec.seal(
        ContinuationPayload(
            binding=binding,
            visible_count=len(messages),
            visible_digest=hashlib.sha256(canonical(messages)).hexdigest(),
            private_assistants=private,
            allowed_tool_call_ids=[t.id for t in result.tool_calls],
        )
    )
