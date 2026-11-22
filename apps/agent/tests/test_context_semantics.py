from datetime import date
from uuid import uuid4

import pytest
from compass_agent.context_runtime.pipeline import fit
from compass_agent.context_runtime.semantics import Dictionary, MetricCatalog
from compass_agent.harness.context import ContextLimit
from compass_agent.schemas import ChatMessage, GenerateInput, TenantScope
from pydantic import ValidationError


def test_metric_scope_units_grain_ambiguity_and_authorized_dependency_closure(tmp_path):
    scope = TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4())
    base = {
        "name": "订单数",
        "definition": "支付成功订单去重",
        "revision": "1",
        "unit": "单",
        "grain": "天",
        "object": "订单",
        "aggregation": "去重计数",
        "source": "orders",
        "tenantId": str(scope.tenant_id),
        "workspaceId": str(scope.workspace_id),
    }
    records = [
        {**base, "metricId": "net", "aliases": ["实付单数"]},
        {**base, "metricId": "period", "grain": "周"},
        {
            **base,
            "metricId": "ratio",
            "name": "退款率",
            "numerator": "refunds",
            "denominator": "net",
            "unit": "%",
        },
        {**base, "metricId": "refunds", "name": "退款笔数", "source": "finance"},
        {**base, "metricId": "past", "validUntil": "2024-01-01"},
        {**base, "metricId": "other", "workspaceId": str(uuid4())},
    ]
    dictionary = Dictionary.model_validate({"version": "v1", "metrics": records})
    target = tmp_path / "current.json"
    target.write_text(dictionary.model_dump_json(by_alias=True))
    catalog = MetricCatalog(tmp_path)
    assert catalog.resolve(catalog.active) == dictionary

    def match(q, *, grants=("orders",)):
        return dictionary.match(q, scope, date(2026, 1, 1), allowed_sources=set(grants))

    many = match("订单数")
    assert many.status == "ambiguous" and many.candidates == ("net", "period")
    assert match("按天查看订单数").candidates == ("net",)
    assert match("实付单数").status == "unique"
    assert match("订单数单位为元").status == "no_match"
    assert match("退款率").records == ()
    derived = match("退款率", grants=("orders", "finance"))
    assert {m.metric_id for m in derived.records} == {"net", "ratio", "refunds"}
    assert match("完全无关").status == "no_match"
    with pytest.raises(ValidationError, match="cyclic"):
        Dictionary.model_validate(
            {"version": "bad", "metrics": [{**base, "metricId": "x", "numerator": "x"}]}
        )


def test_budget_compacts_complete_optional_turns_and_preserves_active_prefix():
    messages = [
        ChatMessage(role="system", content="policy"),
        ChatMessage(role="user", content="old" * 1000),
        ChatMessage(role="assistant", content="previous"),
        ChatMessage(role="user", content="current"),
    ]
    request = GenerateInput(messages=messages)
    prepared, before, after, selected, transforms = fit(request, 600)
    assert [m.content for m in prepared.messages] == ["policy", "current"]
    assert before > 600 >= after and len(transforms) == 1
    assert [s.decision for s in selected] == ["selected", "omitted", "omitted", "selected"]
    with pytest.raises(ContextLimit):
        fit(request.model_copy(update={"continuation_token": "active"}), 600)
    with pytest.raises(ContextLimit):
        fit(request, 30)


def test_report_uses_user_units_and_routed_intent_without_date_syntax():
    from compass_agent.context_runtime.profiles import numeric_intent
    from compass_agent.context_runtime.semantics import report_variables

    for query, expected in [
        ("请以工时为单位", "工时"),
        ("单位为 kg/件", "kg/件"),
        ("以前怎么计算", "已核实口径的计量单位"),
    ]:
        assert report_variables(query, date(2026, 2, 2), "platform")["unit"] == expected
    assert numeric_intent("查数查询", "business_qa")
    assert not numeric_intent("指标解释", "business_qa")
    assert not numeric_intent("其他", None)
