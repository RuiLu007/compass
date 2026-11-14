"""Agent definition and bounded model context, independent of built-in tasks."""

import json
from dataclasses import dataclass

from compass_agent.harness.prompts import SYSTEM_PROMPT
from compass_agent.schemas import RUN_CONTEXT_MESSAGES, ChatMessage, GenerateInput
from compass_agent.tools.contracts import RegisteredTool


class ContextLimit(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    system_prompt: str = SYSTEM_PROMPT
    tools: tuple[RegisteredTool, ...] = ()
    builtin_tools: frozenset[str] = frozenset()


def build_messages(definition, command, instructions: str = "") -> list[ChatMessage]:
    visible = "\n".join(
        [
            command.question,
            *(value for turn in command.history for value in (turn.question, turn.answer)),
        ]
    )
    if len(visible.encode()) > 32 * 1024:
        raise ContextLimit()
    messages = [ChatMessage(role="system", content=definition.system_prompt + instructions)]
    for turn in command.history:
        messages.extend(
            (
                ChatMessage(role="user", content=turn.question),
                ChatMessage(role="assistant", content=turn.answer),
            )
        )
    messages.append(ChatMessage(role="user", content=command.question))
    return messages


def model_input(
    messages,
    continuation_token,
    *,
    tools=(),
    stream=False,
    thinking=False,
    controlled=False,
    deepseek=False,
) -> GenerateInput:
    if len(messages) > RUN_CONTEXT_MESSAGES:
        raise ContextLimit()
    visible = json.dumps(
        [m.model_dump(mode="json", exclude_none=True) for m in messages],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(visible) > 192 * 1024:
        raise ContextLimit()
    return GenerateInput(
        messages=[message.model_copy(deep=True) for message in messages],
        stream=stream,
        thinking=thinking and (not controlled or deepseek),
        reasoning_effort="low" if controlled and thinking and deepseek else None,
        tools=list(tools),
        max_tokens=2048 if controlled else 8192,
        continuation_token=continuation_token,
    )
