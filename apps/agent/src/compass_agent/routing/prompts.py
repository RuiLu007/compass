"""Prompt file choices for the public routing modes."""

from compass_agent.prompt_assets import factory_text, prompt_text

OFF_TOPIC_PROMPT = factory_text("support/routing/off-topic.md")
_MODE_FILES = {
    "answer": "business",
    "clarify": "clarify",
    "refuse": "restricted",
    "handoff": "handoff",
}


def route_prompt(mode, *, off_topic=False):
    name = "off-topic" if off_topic else _MODE_FILES[mode]
    return prompt_text(f"support/routing/{name}.md")
