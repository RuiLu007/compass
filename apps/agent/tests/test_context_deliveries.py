"""Regression cases for artifact grounding and one-time source access."""

import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from compass_agent.context_runtime.delivery import accepted_views, complete_delivery
from compass_agent.context_runtime.source_access import allows
from compass_agent.schemas import TenantScope


def deliver(task, summary, *, columns=("amount",)):
    observation = {"resultId": "source", "columns": columns, "rows": [[120], [80]]}
    step = SimpleNamespace(
        id=uuid4(),
        tool_calls=[SimpleNamespace(function=SimpleNamespace(name="database_query"))],
        tool_results=[SimpleNamespace(status="ok", observation_json=json.dumps(observation))],
    )
    draft = {
        "title": "分区比较",
        "scope": "获准区域",
        "body": summary,
        "summary": summary,
        "views": [{"kind": "table", "title": "销售额", "factRef": "source", "columns": ["amount"]}],
    }
    return complete_delivery(
        task,
        json.dumps(draft),
        SimpleNamespace(steps=[step]),
        step,
        SimpleNamespace(context_date=date(2026, 11, 19)),
    )


def test_structured_claims_and_fact_references_use_accepted_observations():
    assert "200 元" in deliver("business_report", "两区共 200 元。").final_answer
    with pytest.raises(ValueError, match="measurement"):
        deliver("business_report", "两区共 300 元。")
    with pytest.raises(ValueError, match="columns"):
        deliver("dashboard", "区域对比", columns=("changed",))
    file = deliver("data_analysis", "两区共 200 元。")._delivery
    assert file.data.decode("utf-8-sig").splitlines() == ["销售额", "amount", "120", "80"]


def test_formal_report_retains_verified_rows_period_and_effective_scope():
    record = {
        "metric": {"asset_id": "ship", "name": "交付件数", "version": 2},
        "resultId": "receipt",
        "columns": ["group_key", "value", "status"],
        "rows": [["north|west", 42, "OK"]],
        "unit": "piece",
        "as_of": "2026-04-01T00:00:00Z",
        "time_range": [{"start": "2026-03-01", "end": "2026-03-08", "group_by": "area"}],
        "data_scope": {"tenant_id": 7, "all_regions": False, "region_ids": [3]},
    }
    step = SimpleNamespace(
        id=uuid4(),
        tool_calls=[SimpleNamespace(function=SimpleNamespace(name="query_metric"))],
        tool_results=[SimpleNamespace(status="ok", observation_json=json.dumps(record))],
    )
    draft = {
        "title": "交付",
        "scope": "地区",
        "body": "截至本期",
        "summary": "本期交付 42 件。",
        "views": [
            {
                "kind": "table",
                "title": "分区交付",
                "factRef": "receipt",
                "columns": ["group_key", "value"],
            }
        ],
    }

    def complete():
        return complete_delivery(
            "business_report",
            json.dumps(draft),
            SimpleNamespace(steps=[step]),
            step,
            SimpleNamespace(),
        )

    done = complete()
    assert "2026-03-08" in done.final_answer and "租户 7 地区 3" in done.final_answer
    assert "north\\|west | 42" in done._delivery.data.decode()
    second = {**record, "resultId": "second", "rows": [["south", 19, "OK"]]}
    step.tool_calls.append(SimpleNamespace(function=SimpleNamespace(name="query_metric")))
    step.tool_results.append(SimpleNamespace(status="ok", observation_json=json.dumps(second)))
    draft["summary"] = "图中地区为 42 件，另一个授权地区为 19 件。"
    assert "19 件" in complete().final_answer
    draft.update(views=[], summary="本期交付 41 件。")
    with pytest.raises(ValueError, match="measurement"):
        complete()


def test_view_choices_only_reference_accepted_queries_and_unique_columns():
    state = SimpleNamespace(steps=[])
    assert accepted_views(state) == []
    for name, status, record in [
        ("database_query", "ok", {"resultId": "one", "columns": ["amount", "x", "x"]}),
        ("database_query", "ok", {"resultId": "two", "columns": ["orders", "region"]}),
        ("database_query", "ok", {"resultId": "ambiguous", "columns": ["x", "x"]}),
        ("database_query", "error", {"resultId": "rejected", "columns": ["amount"]}),
        ("database_query", "ok", {"columns": ["amount"]}),
        ("python_analysis", "ok", {"resultId": "other_tool", "columns": ["amount"]}),
    ]:
        state.steps.append(
            SimpleNamespace(
                tool_calls=[SimpleNamespace(function=SimpleNamespace(name=name))],
                tool_results=[SimpleNamespace(status=status, observation_json=json.dumps(record))],
            )
        )
    assert accepted_views(state) == [
        {"factRef": "one", "columns": ["amount"]},
        {"factRef": "two", "columns": ["orders", "region"]},
    ]


def test_transient_reads_end_with_live_connection_or_execution_ownership():
    scope = TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4())
    context = SimpleNamespace(scope=scope)
    epoch = uuid4()
    refs = [{"kind": "transient", "epoch": str(epoch), "capturedAt": "2026-11-19T01:01:00+00:00"}]
    assert allows(None, context, refs, epoch=epoch)
    assert allows(None, context, refs, live_since="2026-11-19T01:00:00+00:00")
    assert not allows(None, context, refs, epoch=uuid4())
    assert not allows(None, context, refs, live_since="2026-11-19T01:02:00+00:00")
    assert not allows(None, context, refs)


@pytest.mark.parametrize("task", ["business_report", "data_analysis"])
def test_report_after_failed_repair_only_lists_complete_authorized_formal_results(task):
    requested = [
        {"asset_id": "shipped", "name": "出库数"},
        {"asset_id": "signed", "name": "签收数"},
    ]
    window = [{"start": "2026-01-01", "end": "2026-02-01", "group_by": "zone"}]
    observations = [
        {
            "resultId": m["asset_id"],
            "metric": m,
            "columns": ["group_key", "value", "status"],
            "rows": [["west", str(v), "OK"]],
            "unit": "piece",
            "time_range": window,
            "as_of": "2026-03-01T00:00Z",
            "binding_hash": "b",
            "source_scope_hash": "s",
            "manifest_hash": "r",
        }
        for m, v in zip(requested, [32, 29], strict=True)
    ]
    step = SimpleNamespace(id=uuid4(), tool_calls=[], tool_results=[])
    journal = SimpleNamespace(registry_initial_context={"requested_metrics": requested})
    draft = {
        "title": "履约数",
        "scope": "授权地区",
        "summary": "发出 750 件。",
        "body": "错误推算占 26.21%。",
        "views": [],
    }

    def deliver_formal(raw, *, retry):
        step.tool_calls = [
            SimpleNamespace(function=SimpleNamespace(name="query_metric")) for _ in observations
        ]
        step.tool_results = [
            SimpleNamespace(status="ok", observation_json=json.dumps(r)) for r in observations
        ]
        return complete_delivery(
            task,
            json.dumps(raw) if raw is not None else None,
            SimpleNamespace(steps=[step]),
            step,
            journal,
            allow_verified_report=retry,
        )

    for malformed in [draft, {**draft, "summary": ""}, None]:
        with pytest.raises(ValueError):
            deliver_formal(malformed, retry=False)
        recovered = deliver_formal(malformed, retry=True)
        report = recovered._delivery.data.decode("utf-8-sig")
        sep = " | " if task == "business_report" else ","
        assert "未通过校验" in recovered.final_answer and sep.join(["west", "32", "OK"]) in report
        assert sep.join(["west", "29", "OK"]) in report
        assert "26.21%" not in report and "750" not in report
    observations.pop()
    with pytest.raises(ValueError):
        deliver_formal(draft, retry=True)
