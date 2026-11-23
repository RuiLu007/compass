"""Answer routing, reference integrity, semantic feedback and tool isolation."""

import copy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from compass_agent.context_runtime.answer_verification import before_delivery, source_value
from compass_agent.context_runtime.answers import ANSWER, SourceRef, answer_schema
from compass_agent.context_runtime.profiles import resolve_profile
from compass_agent.runtime.private_state import digest
from compass_agent.tools.batch import dispatch_batch
from compass_agent.tools.contracts import ToolBatchPolicy
from compass_agent.tools.registry import validate_schema

from .test_tool_dispatcher import Budget, call, context, dispatcher, ok, registration


def test_markdown_artifact_reuses_checked_body_with_document_sources():
    from compass_agent.context_runtime.answer_artifacts import build_artifact

    record, evidence = facts()
    value = record["candidate"]
    value["artifact"] = {"format": "markdown", "title": "核对意见", "evidenceIds": ["doc"]}
    evidence["doc"] = {"kind": "document", "record": {"text": "原始材料"}}
    artifact = build_artifact(
        ANSWER.validate_python(value), evidence, "已核正文[1]", "root", "2026-11-22"
    )
    assert artifact is not None and artifact.data.decode() == "# 核对意见\n\n已核正文[1]\n"
    value["artifact"]["format"] = "csv"
    with pytest.raises(ValueError, match="查询结果"):
        build_artifact(ANSWER.validate_python(value), evidence, "已核正文", "root", "2026-11-22")


def test_child_inherits_registered_read_capabilities_with_existing_effect_boundaries():
    from compass_agent.runtime.subagent import child_tools
    from compass_agent.tools.contracts import InProcessBinding, ToolPolicy

    read = registration("read_documents", ok)
    entries = {
        "read_documents": read,
        "read_metrics": replace(read, name="read_metrics"),
        "database_query": replace(read, name="database_query"),
        "hidden_read": replace(read, name="hidden_read"),
        "write": replace(read, name="write", policy=ToolPolicy("external_write", "required")),
        "local": replace(read, name="local", local_effect=True),
        "complete": replace(read, name="complete", completion=True),
        "nested": replace(read, name="nested", binding=InProcessBinding(ok, orchestrates=True)),
    }
    visible = set(entries) - {"hidden_read"}
    assert child_tools(entries, visible, 2) == {"read_documents", "read_metrics", "database_query"}
    assert child_tools(entries, visible, 1) == {"database_query"}
    profile = resolve_profile(
        "finance",
        "business_qa",
        "SC-01",
        "analyze",
        available=tuple(visible),
        plugins=True,
        answer_protocol=2,
        intermediate=True,
    )
    assert "read_documents" in profile.tools
    assert "shared/fragments/answer-protocol.md" not in profile.paths


def test_review_keeps_facts_and_revision_without_copying_private_permission_records():
    import json

    from compass_agent.context_runtime.answer_verification import review_material

    record, evidence = facts()
    evidence["e1"]["sourceRefs"] = [{"kind": "fixture", "privatePermissionMarker": "retained"}]
    material = json.loads(review_material("核对金额", record, evidence))
    assert material["evidenceRevision"] == digest(evidence)
    assert material["evidence"]["e1"]["record"] == evidence["e1"]["record"]
    assert "sourceRefs" not in material["evidence"]["e1"]
    assert evidence["e1"]["sourceRefs"][0]["privatePermissionMarker"] == "retained"


def test_evidence_observation_references_exact_receipts_without_losing_or_mutating_data():
    from compass_agent.context_runtime.evidence import model_observation

    record = {"columns": ["amount"], "rows": [[1700]], "text": "完整原文"}
    payload = {"nested/key~": [record]}
    observed = {
        "evidence": [
            {"evidenceId": "one", "record": copy.deepcopy(record)},
            {"evidenceId": "two", "record": {**record, "unit": "分"}},
        ]
    }
    original = copy.deepcopy(observed)
    out = model_observation(payload, observed)
    assert out["result"] == payload
    assert out["verification"]["evidence"][0] == {
        "evidenceId": "one",
        "recordRef": "#/result/nested~1key~0/0",
    }
    assert out["verification"]["evidence"][1] == observed["evidence"][1]
    assert observed == original
    legacy = {"evidence": [{"evidenceId": "legacy", "columns": ["amount"]}]}
    assert model_observation(payload, legacy)["verification"] == legacy
    assert (
        model_observation(record, {"evidence": [original["evidence"][0]]})["verification"][
            "evidence"
        ][0]["recordRef"]
        == "#/result"
    )


@pytest.mark.parametrize(
    "tokens,cost,child_tokens,expected",
    [(0, 0, 0, False), (601, 0, 0, True), (0, 501, 0, True), (0, 0, 1000, True)],
)
def test_delivery_keeps_existing_model_bounds_for_submission_and_review(
    tokens, cost, child_tokens, expected
):
    from compass_agent.runtime.answer_turn import needs_delivery_reserve

    route = SimpleNamespace(provider_id=uuid4(), binding_version=1)
    profile = SimpleNamespace(
        provider_id=route.provider_id,
        binding_version=1,
        input_tokens=100,
        input_microusd=1,
        output_microusd=2,
    )
    budget = SimpleNamespace(
        profiles=[profile],
        attempts=3,
        steps=3,
        tokens=tokens,
        cost_microusd=cost,
        limits=SimpleNamespace(tokens=1200, cost_microusd=1400, steps=100),
        children=[SimpleNamespace(limits=SimpleNamespace(tokens=child_tokens, cost_microusd=0))],
    )
    assert needs_delivery_reserve(budget, route, 100) == expected
    assert not needs_delivery_reserve(None, route, 100)
    budget.steps = 98
    assert needs_delivery_reserve(budget, route, 100)
    budget.steps = 0
    budget.attempts = 0
    assert not needs_delivery_reserve(budget, route, 100)


def test_whole_paragraph_reference_does_not_require_model_character_counting():
    evidence = {
        "d1": {
            "kind": "document",
            "title": "说明",
            "version": "2",
            "locator": "p1",
            "record": {"sectionId": "p1", "text": "当前余额为 20 元。"},
        }
    }
    value, source = source_value(SourceRef(evidenceId="d1", paragraphId="p1"), evidence)
    assert value == "当前余额为 20 元。" and source["locator"] == "p1"
    with pytest.raises(ValueError, match="字符区间"):
        source_value(SourceRef(evidenceId="d1", paragraphId="p1", start=1), evidence)


def candidate(value):
    value = ANSWER.validate_python(value).model_dump(mode="json", by_alias=True)
    return {"id": str(uuid4()), "digest": digest(value), "candidate": value}


def plain():
    return candidate(
        dict(
            mode="plain",
            completion="complete",
            coveredGoalIds=["root"],
            unresolved=[],
            body="GMV 是商品交易总额。",
        )
    )


def review(record, evidence, **updates) -> dict[str, Any]:
    return dict(
        candidateDigest=record["digest"],
        evidenceRevision=digest(evidence),
        decision="accept",
        modeValid=True,
        goals=[{"goalId": "root", "status": "covered", "reason": "覆盖原问题"}],
        claims=[],
        feedback="",
        **updates,
    )


def facts():
    evidence = {
        "e1": {
            "kind": "query",
            "title": "支付额",
            "version": "1",
            "locator": "result-1",
            "record": {"columns": ["amount"], "rows": [[120], [80]], "unit": "元"},
            "sourceRefs": [],
        }
    }
    record = candidate(
        dict(
            mode="evidence",
            completion="complete",
            coveredGoalIds=["root"],
            unresolved=[],
            claims=[
                dict(
                    id="c1",
                    kind="fact",
                    text="本期支付额为 120 元。",
                    refs=[dict(evidenceId="e1", row=0, column="amount")],
                )
            ],
            blocks=[dict(type="paragraph", claimIds=["c1"])],
        )
    )
    return record, evidence


def accepted_review(record, evidence):
    value = review(record, evidence)
    value["claims"] = [
        {"claimId": c["id"], "status": "supported", "reason": "值与范围一致"}
        for c in record["candidate"].get("claims", [])
    ]
    return value


def test_plain_candidate_requires_review_and_preserves_ordinary_body():
    validate_schema(answer_schema())
    record = plain()
    assert before_delivery(uuid4(), record, {}).action == "review"
    decision = before_delivery(uuid4(), record, {}, review(record, {}))
    assert decision.action == "accept" and decision.body == "GMV 是商品交易总额。"
    assert decision.citations == {"anchors": [], "details": {}}
    wrong_mode = review(record, {})
    wrong_mode.update(modeValid=False, feedback="正文包含工作区查询事实")
    assert before_delivery(uuid4(), record, {}, wrong_mode).action == "revise"


def test_citations_are_rendered_from_persisted_source_and_bound_to_text():
    record, evidence = facts()
    record["candidate"]["blocks"][0]["text"] = "## 核对 [范围]\n### 原始金额"
    record["digest"] = digest(record["candidate"])
    decision = before_delivery(uuid4(), record, evidence, accepted_review(record, evidence))
    assert decision.action == "accept"
    assert decision.body.startswith("## 核对 \\[范围\\]\n### 原始金额\n")
    anchor = decision.citations["anchors"][0]
    assert decision.body[anchor["start"] : anchor["end"]] == "[1]"
    source = next(iter(decision.citations["details"].values()))["sources"][0]
    assert source["excerpt"] == "120" and "行 1" in source["locator"]


@pytest.mark.parametrize("mutation", ["unknown", "row", "column", "cycle", "digest"])
def test_bad_references_and_changed_candidate_request_revision(mutation):
    record, evidence = facts()
    value = record["candidate"]
    if mutation == "digest":
        value["claims"][0]["text"] = "未经审阅的新结论"
    else:
        ref = value["claims"][0]["refs"][0]
        if mutation == "unknown":
            ref["evidenceId"] = "other-run"
        elif mutation == "row":
            ref["row"] = 9
        elif mutation == "column":
            ref["column"] = "missing"
        else:
            value["claims"][0]["refs"] = [{"claimId": "c1"}]
        record["digest"] = digest(value)
    assert before_delivery(uuid4(), record, evidence).action == "revise"


def test_stale_review_and_missing_goals_return_to_loop():
    record, evidence = facts()
    checked = accepted_review(record, evidence)
    changed = copy.deepcopy(evidence)
    changed["e1"]["record"]["rows"][0][0] = 130
    assert before_delivery(uuid4(), record, changed, checked).action == "review"
    checked["goals"].append({"goalId": "cause", "status": "missing", "reason": "漏答原因"})
    assert before_delivery(uuid4(), record, evidence, checked).action == "revise"
    checked.update(decision="act", feedback="补充库存记录")
    assert before_delivery(uuid4(), record, evidence, checked).action == "act"


def test_default_method_keeps_generic_tools_across_profiles():
    names = (
        "database_query",
        "python_analysis",
        "sub_agent",
        "search_documents",
        "mcp_user",
        "final_answer",
    )
    for scope in ("platform", "finance", "operations"):
        value = resolve_profile(
            scope,
            "data_analysis",
            "SC-02",
            "query",
            available=names,
            plugins=True,
            answer_protocol=2,
        )
        assert value.tools == names
        assert "shared/fragments/answer-protocol.md" in value.paths
        assert "shared/fragments/search-protocol.md" not in value.paths


@pytest.mark.anyio
async def test_completion_mixed_batch_rejects_every_call_before_any_effect():
    invoked = []

    async def effect(args, ctx):
        invoked.append(ctx.call_id)
        return await ok(args, ctx)

    finish = replace(registration("completion_test", effect), completion=True)
    regular = registration("regular", effect)
    results = await dispatch_batch(
        dispatcher(finish, regular),
        (call("regular"), call("completion_test")),
        context("regular", "completion_test"),
        ToolBatchPolicy(failure_mode="continue"),
    )
    assert not invoked
    assert all(r.reason_code == "completion_requires_single_call" for r in results)


@pytest.mark.anyio
async def test_completion_does_not_consume_work_tool_budget():
    finish = replace(registration("completion_test", ok), completion=True)
    ctx = context("completion_test")
    result = await dispatch_batch(
        dispatcher(finish), (call("completion_test"),), ctx, ToolBatchPolicy()
    )
    assert isinstance(ctx.budget, Budget)
    assert result[0].status == "ok" and ctx.budget.calls == 0


def test_answer_metadata_preserves_initial_and_legacy_message_projection():
    from compass_agent.schemas import MessageContent

    old = {"text": "", "artifactIds": []}
    assert MessageContent.model_validate(old).model_dump(mode="json", by_alias=True) == old
    new = {**old, "answerMode": "plain", "completion": "partial"}
    assert MessageContent.model_validate(new).model_dump(mode="json", by_alias=True) == new


@pytest.mark.parametrize(
    "op,unit,expected",
    [("difference", "元", "40"), ("ratio", "倍", "1.5"), ("percent_change", "%", "50")],
)
def test_program_calculation_materializes_stored_values_and_units(op, unit, expected):
    record, evidence = facts()
    value = record["candidate"]
    value["claims"][0]["calculation"] = {
        "op": op,
        "unit": unit,
        "inputs": [{"evidenceId": "e1", "row": r, "column": "amount"} for r in (0, 1)],
    }
    value["blocks"][0]["type"] = "comparison_table"
    record = candidate(value)
    answer = before_delivery(uuid4(), record, evidence, accepted_review(record, evidence))
    assert answer.action == "accept" and f"| {expected} |" in answer.body
    value["claims"][0]["calculation"]["unit"] = "千元"
    assert before_delivery(uuid4(), candidate(value), evidence).action == "revise"


def test_partial_goals_must_match_review_and_deliver_explicit_gaps():
    record = plain()
    record["candidate"].update(
        completion="partial",
        coveredGoalIds=[],
        unresolved=[{"goalId": "root", "reason": "无可用资料", "nextStep": "补充资料"}],
    )
    record = candidate(record["candidate"])
    checked = review(record, {})
    assert before_delivery(uuid4(), record, {}, checked).action == "revise"
    checked["goals"][0]["status"] = "unresolved"
    decision = before_delivery(uuid4(), record, {}, checked)
    assert decision.action == "accept" and "无可用资料" in decision.body


def test_qualified_partial_review_settles_instead_of_repeating_the_same_gap():
    record = plain()
    value = record["candidate"]
    value.update(
        completion="partial",
        coveredGoalIds=[],
        unresolved=[{"goalId": "root", "reason": "需用户确认范围", "nextStep": "确认后继续"}],
    )
    record = candidate(value)
    checked = review(record, {})
    checked.update(
        decision="partial", goals=[{"goalId": "root", "status": "unresolved", "reason": "已明确"}]
    )
    assert before_delivery(uuid4(), record, {}, checked).action == "accept"
    checked["goals"].append({"goalId": "omitted", "status": "missing", "reason": "尚未说明"})
    assert before_delivery(uuid4(), record, {}, checked).action == "revise"
