"""Whole-turn JEV selection with shared candidate context and conservative fallback."""

import asyncio
from dataclasses import dataclass, field

from compass_agent.context_runtime.optimization_calls import auxiliary_call
from compass_agent.context_runtime.pipeline import canonical
from compass_agent.llm.policy import OutputLimits
from compass_agent.models.errors import ModelError
from compass_agent.runtime.private_state import digest
from compass_agent.schemas import DecisionInput, DecisionQuestion


@dataclass
class SelectionState:
    fingerprint: str
    groups: list
    protected: tuple[str, ...]
    dropped: set[str] = field(default_factory=set)
    judged: set[str] = field(default_factory=set)
    attempts: list[str] = field(default_factory=list)
    reason: str | None = None

    @property
    def retained(self):
        return [g for g in self.groups if g.id not in self.dropped]

    def snapshot(self):
        candidates = [g.id for g in self.groups if g.id not in self.protected]
        return {
            "inputFingerprint": self.fingerprint,
            "protected": list(self.protected),
            "kept": [g.id for g in self.retained],
            "dropped": sorted(self.dropped),
            "unjudged": [g for g in candidates if g not in self.judged],
            "batchAttemptRefs": list(self.attempts),
            "reason": self.reason,
        }


def decision_request(state, ids):
    return DecisionInput(
        state=state,
        questions={
            group_id: DecisionQuestion(
                type="choice",
                instructions={
                    "groupId": group_id,
                    "instruction": "按 state.policy 判断该完整历史轮。",
                },
                criteria={
                    "keep": "保留：相关、存在依赖或不确定",
                    "drop": "移出：无关且省略不会影响任务",
                },
            )
            for group_id in ids
        },
    )


def fit_batch(state, ids, limit):
    for size in range(min(16, len(ids)), 0, -1):
        request = decision_request(state, ids[:size])
        if len(canonical(request.model_dump(mode="json"))) + 64 <= limit:
            return request
    return None


async def select_history(
    journal,
    models,
    trusted,
    control,
    budget,
    groups,
    question,
    *,
    pressured=None,
    first_batch_only=False,
    previous=None,
    active=(),
    active_sources=(),
):
    await asyncio.to_thread(journal.prefetch_prompts, ["support/context/selection.md"])
    policy = journal.prompt("support/context/selection.md")
    protected = tuple(g.id for g in groups[-3:])
    state = {
        "policy": policy,
        "currentQuestion": question,
        "currentObservations": [m.model_dump(mode="json", exclude_none=True) for m in active],
        "recentTurns": [g.view() for g in groups[-3:]],
        "candidates": [g.view() for g in groups[:-3]],
    }
    provider_id = journal.store.source_settings.compass_routing_jev_provider_id
    fingerprint = digest(
        {
            "state": state,
            "provider": str(provider_id),
            "generation": journal.context_binding["generation"],
        }
    )
    selection = (
        previous
        if previous and previous.fingerprint == fingerprint
        else SelectionState(fingerprint, groups, protected)
    )
    if not groups[:-3] or (len(groups) < 8 and not (pressured and pressured(selection.retained))):
        return selection
    # No optional network attempt is made without a configured input/output cost bound.
    if budget is None:
        selection.reason = "selection_unconfigured"
        return selection
    try:
        if provider_id is None:
            provider_id, _ = await asyncio.to_thread(
                models.repository.routing_provider, "jev", None
            )
        view = await asyncio.to_thread(models.repository.get, provider_id)
    except ModelError:
        selection.reason = "selection_unavailable"
        return selection
    fingerprint = digest(
        {
            "state": state,
            "provider": str(provider_id),
            "binding": view.binding_version,
            "generation": journal.context_binding["generation"],
        }
    )
    selection = (
        previous
        if previous and previous.fingerprint == fingerprint
        else SelectionState(fingerprint, groups, protected)
    )
    profiles = [
        p
        for p in budget.profiles
        if p.provider_id == provider_id and p.binding_version == view.binding_version
    ]
    if len(profiles) != 1:
        selection.reason = "selection_budget_unavailable"
        return selection
    limit = min(profiles[0].input_tokens, OutputLimits().visible_input_bytes) - 32
    ids = [g.id for g in groups[:-3] if g.id not in selection.judged]
    if selection.judged and (first_batch_only or (pressured and not pressured(selection.retained))):
        return selection
    deadline = min(control.deadline_at, asyncio.get_running_loop().time() + 8)
    while ids and asyncio.get_running_loop().time() < deadline:
        request = fit_batch(state, ids, limit)
        if request is None:
            selection.reason = "selection_input_too_large"
            break
        batch_ids = list(request.questions)
        key = f"root/context-select:{fingerprint}:{digest([view.binding_version, batch_ids])}"
        try:
            response = await auxiliary_call(
                journal,
                models,
                trusted,
                control,
                budget,
                kind="ctx_select",
                key=key,
                provider_id=provider_id,
                binding_version=view.binding_version,
                request=request,
                optional=True,
                deadline=deadline,
                source_refs=(
                    *active_sources,
                    *(ref for group in groups for ref in group.source_deps),
                ),
            )
        except ModelError as error:
            if error.code in {"record_write_failed", "context_access_changed", "forbidden"}:
                raise
            selection.reason = error.code
            break
        if journal.last_attempt:
            ref = str(journal.last_attempt)
            if ref not in selection.attempts:
                selection.attempts.append(ref)
        answers = response.answers or {}
        # Adapter validates all IDs/types/probabilities. Retain the same protection
        # at this boundary for alternate transports and restored records.
        from compass_agent.llm.systemone_decision import validate_answers

        try:
            if response.status != "completed":
                raise ValueError("selection unavailable")
            answers = validate_answers(
                request.questions, {k: a.model_dump() for k, a in answers.items()}
            )
        except Exception:
            selection.reason = response.error_code or "selection_invalid"
            break
        for group_id, answer in answers.items():
            selection.judged.add(group_id)
            if answer.choice == "drop" and (answer.probabilities or {}).get("drop", 0) >= 0.8:
                selection.dropped.add(group_id)
        ids = ids[len(batch_ids) :]
        if first_batch_only or (pressured and not pressured(selection.retained)):
            break
    return selection
