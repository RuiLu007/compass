"""Classify the scene and derive the category from the routing dictionary."""

import json
from typing import Literal

from compass_agent.context_runtime.run_facts import RunFacts
from compass_agent.models.errors import ModelError
from compass_agent.prompt_assets import prompt_text
from compass_agent.routing.config import POLICY
from compass_agent.routing.dictionary import descriptions, intent_for_scene
from compass_agent.schemas import (
    ChatMessage,
    DecisionInput,
    DecisionQuestion,
    GenerateInput,
    StrictModel,
)


class ScenePrediction(StrictModel):
    scene_id: str


Delivery = Literal[
    "business_qa", "data_analysis", "dashboard", "business_report", "existing", "clarify"
]


class DeliveryPrediction(ScenePrediction):
    deliverable: Delivery


def decision_input(value, *, managed=False, facts: RunFacts | None = None):
    options: dict[str, str | dict | list | None] = {key: key for key in Delivery.__args__}
    state = {"messages": [message.model_dump() for message in value.messages]}
    if facts and facts.organization:
        state["runOrganization"] = json.loads(facts.organization)
    return DecisionInput(
        state=state,
        questions={
            "scene": DecisionQuestion(
                type="choice",
                instructions=(prompt_text("shared/stages/identify.md") + "\n" if managed else "")
                + prompt_text("support/routing/classify-jev.md")
                + ("\n\n" + facts.instructions if facts else ""),
                criteria=dict(descriptions()),
            ),
            **(
                {
                    "deliverable": DecisionQuestion(
                        type="choice",
                        instructions=prompt_text("support/routing/deliverable.md"),
                        criteria=options,
                    )
                }
                if managed
                else {}
            ),
        },
    )


def flash_input(value, *, managed=False, facts: RunFacts | None = None):
    scenes = descriptions()
    schema = (DeliveryPrediction if managed else ScenePrediction).model_json_schema()
    schema["properties"]["scene_id"]["enum"] = list(scenes)
    instructions = (
        (prompt_text("shared/stages/identify.md") + "\n" if managed else "")
        + prompt_text(
            "support/routing/classify-flash-managed.md"
            if managed
            else "support/routing/classify-flash.md"
        )
        + "\n"
        + (prompt_text("support/routing/deliverable.md") + "\n" if managed else "")
        + json.dumps(scenes, ensure_ascii=False)
    )
    messages = [
        ChatMessage(role="system", content=instructions),
        ChatMessage(
            role="user",
            content=json.dumps(
                [message.model_dump() for message in value.messages], ensure_ascii=False
            ),
        ),
    ]
    return GenerateInput(
        messages=facts.messages(messages) if facts else messages,
        output_format="schema",
        output_schema=schema,
        max_tokens=512,
    )


def choice(response, key):
    if response.status != "completed" or not response.answers or key not in response.answers:
        return None
    answer = response.answers[key]
    probs = answer.probabilities
    if answer.type != "choice" or not probs or answer.choice not in probs:
        return None
    ranked = sorted(probs, key=lambda label: probs[label], reverse=True)
    probability = probs[ranked[0]]
    margin = probability - probs[ranked[1]] if len(ranked) > 1 else probability
    if (
        answer.choice != ranked[0]
        or probability < POLICY.intent_probability
        or margin < POLICY.intent_margin
    ):
        return None
    return answer.choice


def from_jev(response, *, managed=False, facts: RunFacts | None = None):
    if set(response.answers or {}) != ({"scene", "deliverable"} if managed else {"scene"}):
        return None
    scene = choice(response, "scene")
    if scene is None:
        return None
    try:
        return intent_for_scene(scene)
    except ModelError:
        return None


def from_flash(response):
    if response.status != "completed":
        raise ModelError("intent_unavailable", 503)
    try:
        return intent_for_scene(ScenePrediction.model_validate_json(response.text).scene_id)
    except ValueError as exc:
        raise ModelError("intent_invalid", 503) from exc
