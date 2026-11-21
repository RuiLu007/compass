"""Project a fixed purpose-specific set of published files into editable UI inputs."""

import json
from importlib.resources import files

from sqlalchemy.orm import Session

from compass_agent.models.errors import ModelError
from compass_agent.schemas import (
    PromptStarterCards,
    StarterDecision,
    StarterGroup,
    StarterQuestion,
    StarterSuggestion,
)

PREFIX = "support/starters/"
CATALOG = json.loads(
    files(__package__).joinpath("starter_catalog.json").read_text(encoding="utf-8")
)
ANALYSIS_SHOWCASES = json.loads(
    files(__package__).joinpath("analysis_showcases.json").read_text(encoding="utf-8")
)
SAVED_WORKFLOW = "analysis/saved-workflow.md"
DEFAULT_CHOICE = "models/new-choice-criteria.md"
TOOL_DESCRIPTION = "models/sales-tool-description.md"


def optional_showcase_paths() -> set[str]:
    return {PREFIX + item["path"] for group in ANALYSIS_SHOWCASES for item in group["suggestions"]}


def analysis_groups():
    # The base catalog ends with the general questions, after the course examples.
    *lessons, general = CATALOG["analysis"]
    return [*lessons, *ANALYSIS_SHOWCASES, general]


def starter_usages() -> dict[str, str]:
    usages = {PREFIX + DEFAULT_CHOICE: "平台模型测试", PREFIX + TOOL_DESCRIPTION: "平台模型测试"}
    usages[PREFIX + SAVED_WORKFLOW] = "分析对话"
    for group in analysis_groups():
        for item in group["suggestions"]:
            usages[PREFIX + item["path"]] = "分析对话"
    for model in CATALOG["models"]:
        usages[PREFIX + model["state"]] = "平台模型测试"
        for question in model["questions"]:
            usages[PREFIX + question["instructions"]] = "平台模型测试"
            if "criteria" in question:
                usages[PREFIX + question["criteria"]] = "平台模型测试"
    return usages


def validate_starter(path: str, body: str) -> None:
    """Validate protocol-bearing examples at publish and read with the same checks."""
    if not path.startswith(PREFIX):
        return
    rel = path[len(PREFIX) :]
    if rel.startswith("analysis/") and (not body.strip() or len(body.rstrip("\n")) > 8000):
        raise ValueError("分析预填问题须为 1–8000 个字符")
    keys = ["A", "B"] if rel == DEFAULT_CHOICE else None
    for model in CATALOG["models"]:
        if model["stateFormat"] == "json" and rel == model["state"]:
            if not isinstance(json.loads(body), dict):
                raise ValueError("结构化测试场景须为 JSON 对象")
        for q in model["questions"]:
            if q.get("criteria") != rel:
                continue
            if q["type"] == "choice":
                keys = q["choices"]
            elif q["type"] == "score" and not 2 <= len(body.strip().splitlines()) <= 20:
                raise ValueError("评分等级须为 2–20 行")
    if keys:
        values = json.loads(body)
        if (
            not isinstance(values, dict)
            or sorted(values) != sorted(keys)
            or not all(isinstance(v, str) and v.strip() for v in values.values())
        ):
            raise ValueError("选择题须保留已登记的选项 ID 并填写说明")


def starter_cards(session: Session, context) -> PromptStarterCards:
    from compass_agent.promptops.reader import PromptReader
    from compass_agent.schemas import RunConstraints

    if context.scope.kind == "tenant" and context.purpose == "chat":
        purpose = "分析对话"
    elif context.scope.kind == "platform" and context.purpose == "test":
        purpose = "平台模型测试"
    else:
        raise ModelError("forbidden", 403)
    generation = PromptReader.pin_current(session)
    paths = [p for p, use in starter_usages().items() if use == purpose]
    if purpose == "分析对话":
        # Upgrading the bundle registers new examples before an admin publishes them.
        published = PromptReader.published_paths(session, generation)
        unpublished_examples = optional_showcase_paths() - published
        paths = [p for p in paths if p not in unpublished_examples]
    by_path = {}
    for item in PromptReader.load_exact(session, generation, paths):
        try:
            validate_starter(item.path, item.body)
        except ValueError as exc:
            raise ModelError("unavailable", 503) from exc
        by_path[item.path[len(PREFIX) :]] = item.body.removesuffix("\n")
    result = PromptStarterCards(generation=generation)
    if purpose == "分析对话":
        from compass_agent.promptops.template import parts

        # Each gap is the single declared workflow name. No arbitrary template reaches the client.
        result.workflow_parts = [
            literal for literal, name in parts(by_path[SAVED_WORKFLOW]) if name is None
        ]
        for group in analysis_groups():
            items = []
            for item in group["suggestions"]:
                if item["path"] not in by_path:
                    continue
                constraint = item.get("constraints")
                if constraint:
                    constraint = RunConstraints.model_validate(constraint)
                items.append(
                    StarterSuggestion(
                        label=item["label"], question=by_path[item["path"]], constraints=constraint
                    )
                )
            if items:
                result.analysis.append(StarterGroup(title=group["title"], suggestions=items))
    else:
        result.default_choice = by_path[DEFAULT_CHOICE]
        result.tool_description = by_path[TOOL_DESCRIPTION]
        for model in CATALOG["models"]:
            questions = [
                StarterQuestion(
                    id=q["id"],
                    type=q["type"],
                    instructions=by_path[q["instructions"]],
                    criteria=by_path[q["criteria"]] if q.get("criteria") else "",
                )
                for q in model["questions"]
            ]
            result.models.append(
                StarterDecision(
                    id=model["id"],
                    title=model["title"],
                    description=model["description"],
                    stateFormat=model["stateFormat"],
                    state=by_path[model["state"]],
                    questions=questions,
                )
            )
    return result
