"""Versioned selection rules for runtime assembly and PromptOps usage previews."""

import json
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Task = Literal["business_qa", "data_analysis", "dashboard", "business_report"]
Phase = Literal["identify", "plan", "query", "analyze", "generate"]
TASKS: tuple[Task, ...] = ("business_qa", "data_analysis", "dashboard", "business_report")
PHASES: tuple[Phase, ...] = ("identify", "plan", "query", "analyze", "generate")
POLICY_VERSION = "context-v1"


def _policy_digest():
    root = files("compass_agent")
    digest = sha256()
    for path in (
        "context_runtime/profiles.py",
        "promptops/catalog.py",
        "promptops/template.py",
        "promptops/layout.py",
        "promptops/structured.py",
        "promptops/legacy_layout.json",
        "promptops/starter_catalog.json",
        "routing/dictionary.py",
    ):
        data = root.joinpath(*path.split("/")).read_bytes()
        digest.update(path.encode() + b"\0" + sha256(data).digest())
    scene_document = json.loads(root.joinpath("prompts/support/routing/scenes.json").read_text())
    fixed = {
        key: {
            field: value for field, value in row.items() if field not in {"description", "examples"}
        }
        for key, row in scene_document["scenes"].items()
    }
    digest.update(json.dumps(fixed, sort_keys=True, ensure_ascii=False).encode())
    return digest.hexdigest()


POLICY_DIGEST = _policy_digest()
BASE = "shared/fragments/base.md"

# Task form selects the method. The business scene independently selects an output slot.
SCENE_FILES = {
    ("business_qa", "SC-01"): "tasks/business_qa/scenes/metric_lookup/answer.md",
    ("data_analysis", "SC-02"): "tasks/data_analysis/scenes/period_comparison/answer.md",
    ("dashboard", "SC-25"): "tasks/dashboard/scenes/channel_overview/layout.md",
    ("business_report", "SC-29"): "tasks/business_report/scenes/weekly/report.md",
}
FINANCE_REPORT = "tasks/business_report/scenes/weekly/finance.md"


class ScopeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID = Field(alias="tenantId")
    workspace_id: UUID | None = Field(None, alias="workspaceId")
    profile: Literal["finance", "operations"]


def scope_profile(scope, rules: list[ScopeRule]) -> str:
    matches = [rule for rule in rules if rule.tenant_id == scope.tenant_id]
    by_scope = {r.workspace_id: r.profile for r in matches}
    if len(matches) != len(by_scope):
        raise ValueError("duplicate context scope rule")
    return by_scope.get(scope.workspace_id, by_scope.get(None, "platform"))


@dataclass(frozen=True, slots=True)
class PathUsage:
    path: str
    task: str | None = None
    phase: str | None = None
    scope: str = "platform"
    variables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillSpec:
    key: str
    path: str
    tools: frozenset[str]
    phases: tuple[Phase, ...]
    tasks: tuple[Task, ...]


SKILLS = (
    SkillSpec(
        "agentic-search",
        "shared/skills/agentic-search.md",
        frozenset(),
        ("plan", "query", "analyze"),
        TASKS,
    ),
    SkillSpec(
        "metric-check",
        "shared/skills/metric-check.md",
        frozenset({"database_query"}),
        ("plan", "query", "analyze"),
        TASKS,
    ),
    SkillSpec(
        "report-outline",
        "shared/skills/report-outline.md",
        frozenset(),
        ("plan", "generate"),
        ("business_report",),
    ),
)


@dataclass(frozen=True, slots=True)
class Profile:
    paths: tuple[str, ...]
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]


def enumerate_usages(policy_version=POLICY_VERSION) -> tuple[PathUsage, ...]:
    if policy_version != POLICY_VERSION:
        raise ValueError("incompatible context policy")
    return (
        PathUsage(BASE),
        PathUsage("shared/fragments/search-protocol.md"),
        PathUsage("shared/fragments/retail.md"),
        PathUsage("shared/fragments/semantics.md"),
        PathUsage("shared/fragments/semantic-governance.md"),
        *(PathUsage(f"tasks/{t}/system.md", t) for t in TASKS),
        *(PathUsage(f"shared/stages/{s}.md", phase=s) for s in PHASES),
        *(
            PathUsage(
                p, t, variables=("period", "unit", "audience") if t == "business_report" else ()
            )
            for (t, _), p in SCENE_FILES.items()
        ),
        PathUsage(FINANCE_REPORT, "business_report", scope="finance", variables=("period", "unit")),
        *(PathUsage(s.path, t, phase) for s in SKILLS for t in s.tasks for phase in s.phases),
    )


def resolve_profile(
    scope: str,
    task: Task,
    scene: str,
    phase: Phase,
    *,
    available: tuple[str, ...],
    plugins: bool,
    enabled: bool = True,
    registry: bool = False,
    search: dict | None = None,
) -> Profile:
    stage_tools = {
        "identify": frozenset(),
        "plan": frozenset({"todo_write"}),
        "query": None,
        "analyze": None,
        "generate": frozenset(),
    }[phase]
    from compass_agent.tools.catalog import TOOLS

    semantic_tools = {t.name for t in TOOLS if t.capability.startswith("semantic_")}
    allowed, excluded = [], []
    for name in available:
        if search:
            from compass_agent.context_runtime.search_control import READ_TOOLS

            if name not in READ_TOOLS | {"todo_write"}:
                excluded.append((name, "scope"))
                continue
        if not enabled or (name.startswith("mcp_") and not plugins):
            excluded.append((name, "permission"))
        elif (
            scope == "finance"
            and not (search and name in {"search_documents", "read_document"})
            and not (
                registry
                and (name in semantic_tools or (plugins and name.startswith(("mcp_", "workflow_"))))
            )
            and name not in {"todo_write", "database_query"}
        ):
            excluded.append((name, "scope"))
        elif stage_tools is not None and name not in stage_tools:
            excluded.append((name, "stage"))
        else:
            allowed.append(name)
    paths = [BASE]
    if search and phase != "identify":
        paths.append("shared/fragments/search-protocol.md")
        if search.get("scenePack") == "retail" and scene not in {
            "SC-33",
            "SC-36",
            "SC-37",
            "SC-38",
            "SC-39",
        }:
            paths.append("shared/fragments/retail.md")
    if registry and phase != "identify":
        paths.append("shared/fragments/semantics.md")
    if phase != "identify":
        paths.append(f"tasks/{task}/system.md")
        scene_path = SCENE_FILES.get((task, scene))
        if task == "business_report" and scene == "SC-29" and scope == "finance":
            scene_path = FINANCE_REPORT
        if scene_path:
            paths.append(scene_path)
    paths.append(f"shared/stages/{phase}.md")
    skills = []
    for skill in SKILLS:
        if skill.key == "agentic-search" and not (search and search.get("skillEnabled")):
            continue
        if phase not in skill.phases or task not in skill.tasks:
            continue
        if not plugins or not skill.tools.issubset(allowed):
            excluded.append((skill.key, "dependency" if plugins else "permission"))
            continue
        paths.append(skill.path)
        skills.append(skill.key)
    return Profile(tuple(paths), tuple(allowed), tuple(skills), tuple(excluded))
