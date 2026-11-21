"""Unit regressions for fixed plans, row authority and the receipt content boundary."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from compass_agent.schemas import SemanticResultDecision
from compass_agent.semantic.execution import choose_connection, json_value
from compass_agent.semantic.service import RegistryTools, open_digest
from compass_agent.tools.builtin.database_query import DatabaseResult
from compass_agent.tools.business.query_metric import Input
from compass_agent.tools.contracts import ToolRejected


def test_query_ref_is_sufficient_but_changed_parameters_cannot_reuse_it():
    ref = uuid4()
    assert Input(query_ref=ref).parameters is None
    metric = {"source_ref": "absent", "access_requirements": {}}
    with pytest.raises(ToolRejected):
        choose_connection(
            [SimpleNamespace(connection_id=uuid4(), name="other", scope_check=None)], metric
        )


def test_persisted_result_hash_tracks_values_columns_and_truncation():
    data = DatabaseResult("opaque", ("amount",), ((100,),), False)
    assert open_digest(data) == open_digest(DatabaseResult("replay", ("amount",), ((100,),), False))
    assert open_digest(data) != open_digest(DatabaseResult("opaque", ("amount",), ((99,),), False))
    assert open_digest(data) != open_digest(DatabaseResult("opaque", ("amount",), ((100,),), True))
    assert json_value(Decimal("10.200")) == "10.200"


def test_temporal_or_group_changes_require_a_new_resolution(monkeypatch):
    import asyncio

    from compass_agent.semantic import service

    obj = object.__new__(RegistryTools)
    obj.journal = SimpleNamespace(search_snapshot=None)
    params: dict[str, Any] = {
        "start": "2026-03-01",
        "end": "2026-04-01",
        "as_of": "2026-05-01T00:00:00+00:00",
    }
    plan = {"asset_id": "a", "version_id": "v", "binding_id": "b", "parameters": params}
    monkeypatch.setattr(obj, "get", lambda *_: plan)
    obj.api = AsyncMock(return_value={"binding": {}})
    obj.store = SimpleNamespace(engine=None, source_settings=None)
    obj.trusted = SimpleNamespace(scope=None)
    monkeypatch.setattr(
        service, "DataConnections", lambda *_: SimpleNamespace(bindings_for_run=lambda _: [])
    )
    monkeypatch.setattr(service, "choose_connection", lambda *_: None)
    for change in (
        {"as_of": "2026-05-02T00:00:00Z"},
        {"group_by": "region"},
        {"end": "2026-04-02"},
    ):
        with pytest.raises(ToolRejected, match="semantic_rebind_parameters"):
            asyncio.run(obj.query({"query_ref": "r", "parameters": {**params, **change}}, None))

    params["group_by"] = "district"
    with pytest.raises(ToolRejected, match="invalid_parameters"):
        asyncio.run(obj.query({"query_ref": "r", "operation": "drilldown"}, None))
    params["filters"] = {"country": "CN"}
    with pytest.raises(ToolRejected, match="semantic_rebind_parameters"):
        asyncio.run(
            obj.query(
                {"query_ref": "r", "operation": "drilldown", "parent_group": {"country": "JP"}},
                None,
            )
        )

    calculate = AsyncMock(side_effect=[([], {"region_ids": [1, 2]}), ([], {"region_ids": [1]})])
    monkeypatch.setattr(service, "calculate", calculate)
    previous = {**params, "start": "2026-02-01", "end": "2026-03-01"}
    with pytest.raises(ToolRejected, match="source_access_denied"):
        asyncio.run(
            obj.query(
                {"query_ref": "r", "operation": "compare", "comparison": previous},
                SimpleNamespace(control=None),
            )
        )


def test_search_cursor_is_bound_to_its_release_and_original_conditions(monkeypatch):
    import asyncio
    from contextlib import nullcontext

    from compass_agent.semantic.entities import SemanticMetricOffer

    obj = object.__new__(RegistryTools)
    obj.journal = SimpleNamespace(run_id=uuid4(), epoch=uuid4())
    obj.trusted = SimpleNamespace()
    obj.pins = [str(uuid4())]
    saved = {}
    offsets = []
    visible_id = "one"

    async def api(op, args=None):
        if op == "context":
            return {"max_search_rounds": 2}
        assert args is not None
        offsets.append(args["cursor"])
        return {"items": [{"asset_id": visible_id}], "cursor": 5 if args["cursor"] == 0 else None}

    tx = SimpleNamespace(
        get=lambda model, ref: saved.get(ref),
        add=lambda row: saved.update({row.offer_id: row}),
        scalar=lambda _: sum(row.kind == "search" for row in saved.values()),
    )
    obj.store = SimpleNamespace(
        lock=lambda *_: (SimpleNamespace(id=uuid4(), owner_user_id=uuid4()), None),
        transaction=lambda: nullcontext(tx),
        require_epoch=lambda *_: None,
        private=SimpleNamespace(
            seal=lambda _, key, payload: payload, open=lambda _, key, payload: payload
        ),
    )
    monkeypatch.setattr(obj, "api", api)
    monkeypatch.setattr(obj, "record_page", lambda *_: None)
    monkeypatch.setattr(
        obj, "get", lambda ref, kind: tx.get(SemanticMetricOffer, UUID(ref)).private_payload
    )

    def call():
        return SimpleNamespace(step_id=uuid4(), call_id=str(uuid4()))

    query = {"query": "件数", "limit": 5}
    original = call()
    page = asyncio.run(obj.run("search", query, original))
    assert page["cursor"] and page["cursor"] != 5
    assert asyncio.run(obj.run("search", query, original)) == page
    assert asyncio.run(obj.run("search", query, call()))["status"] == "budget_exhausted"
    more = {**query, "cursor": page["cursor"]}
    assert asyncio.run(obj.run("search", more, call()))["cursor"] is None
    with pytest.raises(ToolRejected, match="invalid_parameters"):
        asyncio.run(obj.run("search", {**more, "query": "退货"}, call()))
    visible_id = "changed"
    with pytest.raises(ToolRejected, match="source_access_denied"):
        asyncio.run(obj.run("search", query, original))
    obj.pins = [str(uuid4())]
    with pytest.raises(ToolRejected, match="invalid_parameters"):
        asyncio.run(obj.run("search", more, call()))
    assert offsets == [0, 0, 0, 5, 0]


def test_period_difference_preserves_sign_precision_and_missing_groups():
    from compass_agent.semantic.execution import period_comparison

    def row(group, value):
        return {
            "group_key": group,
            "value": value,
            "status": "OK" if value is not None else "NO_DATA",
        }

    current = [row("east", "103.10"), row("north", 0), row("south", 10), row("west", None)]
    previous = [row("north", 7), row("east", "100.05"), row("west", 10)]
    compared = period_comparison(current, previous)
    assert [r["current_minus_comparison"] for r in compared] == [
        "3.05",
        "-7",
        None,
        None,
        None,
        None,
        None,
    ]
    assert all("current_minus_comparison" not in r for r in current + previous)


def test_complete_additive_money_aligns_both_sides_but_missing_coverage_stays_unknown():
    from compass_agent.semantic.execution import (
        group_totals,
        missing_group_is_zero,
        period_comparison,
    )

    def row(key, value, status="OK"):
        return {"group_key": key, "value": value, "status": status}

    info = {
        "aggregation": "additive_at_declared_grain",
        "metric": {"unit": "CNY_fen"},
        "binding": {
            "result_schema": {
                "properties": {
                    "unit": {"const": "CNY_fen"},
                    "status": {"enum": ["OK", "NO_FACTS", "SOURCE_DELAYED"]},
                }
            }
        },
    }
    assert missing_group_is_zero(info)
    assert not missing_group_is_zero({**info, "metric": {"unit": "piece"}})
    assert not missing_group_is_zero({**info, "aggregation": "non_additive"})
    a = [row("new", "198.01"), row(None, "0.12")]
    b = [row("old", "15.004"), row(None, "0.11")]
    result = period_comparison(a, b, empty_is_zero=True)
    assert [r["current_minus_comparison"] for r in result] == ["198.01", "0.01", "-15.004", None]
    assert (result[2]["current_value"], result[2]["comparison_value"]) == ("0", "15.004")
    assert result[2]["period_side"] == "comparison"
    windows = [
        {"start": "2026-02-08", "end": "2026-02-15", "group_by": "area"},
        {"start": "2026-02-01", "end": "2026-02-08", "group_by": "area"},
    ]
    assert [r["value"] for r in group_totals([a, b], windows, info)] == ["198.13", "15.114"]
    empty = [row(None, None, "NO_FACTS")]
    assert [r["value"] for r in group_totals([empty], windows[:1], info)] == ["0"]
    assert (
        period_comparison(empty, b, empty_is_zero=True)[1]["current_minus_comparison"] == "-15.004"
    )
    missing = [row(None, None, "SOURCE_DELAYED")]
    assert group_totals([missing], windows[:1], info) == []
    assert all(
        r["current_minus_comparison"] is None
        for r in period_comparison(a, missing, empty_is_zero=True)
    )
    prior_report = {
        **info,
        "binding": {
            "result_schema": {
                "properties": {
                    "unit": {"const": "CNY_fen"},
                    "status": {"enum": ["OK", "NO_REPORT_ROWS"]},
                }
            }
        },
    }
    assert not missing_group_is_zero(prior_report)


def test_invalid_filter_returns_its_registered_type_before_issuing_a_plan():
    from compass_agent.semantic.execution import parameter_feedback

    schema = {
        "type": "object",
        "properties": {
            "filters": {"type": "object", "properties": {"bin": {"type": "integer"}}},
            "group_by": {"enum": ["total", "facility"]},
        },
    }
    assert parameter_feedback(schema, {"filters": {"bin": 7}}) is None
    feedback = parameter_feedback(schema, {"filters": {"bin": "7"}})
    assert feedback is not None and "search_semantics" in feedback.pop("repair")
    assert feedback == {
        "status": "invalid_parameters",
        "field": "filters.bin",
        "filter_types": {"bin": "integer"},
        "group_by_options": ["total", "facility"],
    }


def test_requested_metric_differences_share_verified_source_time_and_filters():
    from compass_agent.semantic.receipts import reconciliation_lines

    w = {"start": "2026-01-01", "end": "2026-02-01", "group_by": "total"}
    metrics = [{"asset_id": "a", "name": "发出"}, {"asset_id": "b", "name": "收到"}]

    def observation(metric, value):
        return {
            "metric": metric,
            "time_range": [w],
            "source_scope_hash": "verified",
            "manifest_hash": "fixed",
            "as_of": "2026-03-01",
            "aggregation": "additive",
            "unit": "箱",
            "columns": ["group_key", "value", "status", "period_start", "period_end"],
            "rows": [["total", value, "OK", w["start"], w["end"]]],
        }

    a, b = observation(metrics[0], 3), observation(metrics[1], 7)
    assert "发出 减 收到 = -4 箱" in reconciliation_lines({"a": a, "b": b}, metrics)[0]
    for patch in (
        {"source_scope_hash": "other"},
        {"unit": "件"},
        {"as_of": "2026-04-01"},
        {"aggregation": "non_additive"},
        {"time_range": [{**w, "filters": {"route": 1}}]},
    ):
        assert not reconciliation_lines({"a": a, "b": {**b, **patch}}, metrics)
    assert not reconciliation_lines({"a": a, "b": b}, metrics[:1])


def test_reconcile_grouped_results_against_the_same_independent_total():
    from compass_agent.semantic.receipts import group_reconciliation_lines

    window = {"start": "2026-01-01", "end": "2026-02-01", "group_by": "total"}
    total = {
        "metric": {"asset_id": "fulfilled", "version_id": "v1", "name": "履约数"},
        "binding_hash": "binding",
        "source_scope_hash": "scope",
        "manifest_hash": "release",
        "as_of": "2026-02-04",
        "unit": "件",
        "aggregation": "additive_at_declared_grain",
        "time_range": [window],
        "columns": ["group_key", "value", "status", "period_start", "period_end"],
        "rows": [["total", "32", "OK", window["start"], window["end"]]],
    }

    def grouped(axis, value):
        return {
            **total,
            "time_range": [{**window, "group_by": axis}],
            "rows": [],
            "period_totals": [{"start": window["start"], "end": window["end"], "value": value}],
        }

    district = grouped("district", "32.0")
    lines = group_reconciliation_lines(
        {"all": total, "districts": district, "sites": grouped("site", "28")}
    )
    assert len(lines) == 2 and "核对结果一致" in lines[0] and "差额 -4" in lines[1]
    for patch in (
        {"binding_hash": "other"},
        {"source_scope_hash": "restricted"},
        {"as_of": "2026-02-05"},
        {"aggregation": "non_additive"},
        {"period_totals": []},
        {"time_range": [{**district["time_range"][0], "filters": {"zone": 1}}]},
    ):
        assert not group_reconciliation_lines({"all": total, "districts": {**district, **patch}})
    assert not group_reconciliation_lines(
        {"all": total, "a": district, "b": grouped("district", "30")}
    )


def test_previous_equal_window_preserves_half_open_dates_and_original_scope():
    from compass_agent.semantic.execution import previous_equal_window

    params = {
        "start": "2024-03-01",
        "end": "2024-03-08",
        "as_of": "2024-04-01T00:00:00Z",
        "filters": {"location": "north"},
        "group_by": "supplier",
    }
    assert previous_equal_window(params) == {**params, "start": "2024-02-23", "end": "2024-03-01"}
    assert previous_equal_window({**params, "start": "0001-01-01", "end": "0001-01-02"}) is None
    for end in ("2024-03-01", "2024-02-30"):
        with pytest.raises(ToolRejected, match="invalid_parameters"):
            previous_equal_window({**params, "end": end})


def test_result_confirmation_has_no_scope_or_author_supplied_by_caller():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SemanticResultDecision.model_validate(
            {"result_ref": str(uuid4()), "action": "approve", "tenantId": str(uuid4())}
        )


def test_draft_preview_and_legacy_confirmation_use_the_recorded_source_fields():
    from compass_agent.semantic.draft_fields import confirmed_fields, result_preview
    from compass_agent.tools.business.discover_semantic_candidates import Input as Discover

    ref = uuid4()
    original = {
        "summary": "发货统计",
        "formula": "count(*)",
        "dimensions": ["warehouse"],
        "filters": ["shipped_at"],
    }
    suggestions = {
        "name": "出库件数",
        "filters": "warehouse = 1",
        "data_source": "other",
        "owner_scope_id": str(uuid4()),
    }
    tool_args = Discover.model_validate({"result_ref": str(ref), "fields": suggestions})
    fields = result_preview(original, tool_args.model_dump(exclude_none=True)["fields"], ref)
    assert fields["name"] == "出库件数" and fields["filters"] == ["shipped_at"]
    assert "data_source" not in fields and "owner_scope_id" not in fields
    edited = {**fields, "summary": "", "domain": "履约", "usage_scope_id": str(uuid4())}
    assert confirmed_fields(original, edited, ref) == edited
    repaired = confirmed_fields(
        original, {**edited, "data_source": "other", "filters": "warehouse = 1"}, ref
    )
    assert (
        repaired["filters"] == ["shipped_at"]
        and repaired["usage_scope_id"] == edited["usage_scope_id"]
    )
    assert "data_source" not in repaired


def test_cross_turn_save_prefers_the_previous_answer_over_an_unpublished_repeat(monkeypatch):
    import asyncio

    from compass_agent.semantic import service

    store = object.__new__(RegistryTools)
    store.journal = store.trusted = None
    monkeypatch.setattr(store, "open_result", lambda ref: (ref, {}))

    def current(ref, _kind):
        if ref != "unpublished":
            raise ToolRejected("semantic_reference_expired")
        return {}

    monkeypatch.setattr(store, "get", current)
    monkeypatch.setattr(store, "chosen_result", lambda: None)
    monkeypatch.setattr(service, "session_results", lambda *_: [{"result_ref": "prior"}])
    assert asyncio.run(store.requested_result({"result_ref": "unpublished"})) == "prior"
    assert asyncio.run(store.requested_result({"result_ref": "prior"})) == "prior"


def test_wire_result_and_decision_round_trip_in_strict_http_models():
    from compass_agent.schemas import SemanticResults
    from compass_agent.semantic.results import project

    identifier, asset = uuid4(), uuid4()
    row = SimpleNamespace(offer_id=identifier, status="saved")
    data = {
        "columns": ["件数"],
        "created_at": datetime.now(UTC).isoformat(),
        "saved": {"asset_id": str(asset)},
    }
    result = SemanticResults.model_validate({"items": [project(row, data)]}).model_dump(mode="json")
    assert result["items"][0]["draft_id"] == str(asset)
    assert (
        SemanticResultDecision.model_validate(
            {"result_ref": str(identifier), "action": "confirm"}
        ).result_ref
        == identifier
    )


def test_source_definition_extracts_typed_time_bounds_and_parameterizes_private_constants():
    from compass_agent.semantic.extraction import definition_from_query

    query = (
        "SELECT district, sum(CASE WHEN state='private-rule' THEN units ELSE 0 END) AS units "
        "FROM stock WHERE business_day BETWEEN DATE '2026-03-01' AND DATE '2026-03-31' "
        "GROUP BY district"
    )
    result = definition_from_query(query, ["district", "units"], "按地区汇总件数")
    by_position = definition_from_query(query.replace("GROUP BY district", "GROUP BY 1"), [], "")
    assert by_position["fields"]["dimensions"] == ["district"]
    assert result["fields"]["aggregation"] == "sum"
    assert result["fields"]["event_time"] == "business_day"
    assert result["parameter_examples"] == ["2026-03-01", "2026-03-31"]
    assert "private-rule" not in result["fields"]["formula"]
    assert (
        result["query_signature"]
        == definition_from_query(query.replace("2026-03", "2026-04"), [], "")["query_signature"]
    )
    assert (
        result["query_signature"]
        != definition_from_query(query.replace("private-rule", "another-rule"), [], "")[
            "query_signature"
        ]
    )


def test_only_one_equivalent_confirmed_result_can_invite(monkeypatch):
    from compass_agent.semantic import results

    monkeypatch.setattr(results.client, "request", lambda *a: {"hashes": ["published"]})

    def row(ref, status, key):
        return ref, status, {"definition_hash": key, "reusable": True}

    sample = [
        row("1", "active", "same"),
        row("2", "confirmed", "same"),
        row("3", "confirmed", "published"),
    ]
    store = SimpleNamespace(source_settings=None)
    assert results.invitation_eligibility(sample, store, None, None) == {"2"}
    assert not results.invitation_eligibility(
        [*sample, row("4", "dismissed", "same")], store, None, None
    )


def test_single_term_requires_owner_choice_only_while_two_versions_are_usable():
    import asyncio

    from compass_agent.schemas import RunCompleted, RunWaiting
    from compass_agent.semantic.clarification import check

    choices = [
        {
            "asset_id": str(uuid4()),
            "version_id": str(uuid4()),
            "name": name,
            "summary": "已发布口径",
            "unit": "件",
            "version": 1,
        }
        for name in ("发出件数", "签收件数")
    ]
    journal = SimpleNamespace(run_id=uuid4())
    status: dict[str, str | None] = {"status": "needs_clarification", "mention": "履约量"}

    async def registry(op, _args):
        return {"resolution": status} if op == "context" else {**status, "options": choices}

    current = None
    svc = SimpleNamespace(journal=journal, api=registry, choice=lambda _: current)
    waiting = asyncio.run(check(svc, "查询该期间的履约量"))
    assert isinstance(waiting, RunWaiting)
    assert journal.pending_proposal["origin"] == "registry"
    assert [o["id"] for o in journal.pending_proposal["options"]] == [
        m["asset_id"] for m in choices
    ]
    current = choices[1]
    assert asyncio.run(check(svc, "查询该期间的履约量")) is None
    assert journal.registry_selected == choices[1]
    status["status"] = "unavailable_binding"
    denied = asyncio.run(check(svc, "查询该期间的履约量"))
    assert isinstance(denied, RunCompleted) and "权限" in denied.final_answer
    status["mention"] = None
    assert asyncio.run(check(svc, "查询多个不同指标")) is None


def test_comparison_reports_each_approved_definition_and_clock_once():
    from compass_agent.semantic.receipts import scope_lines

    def observation(identity, clock):
        return {
            "metric": {
                "name": "发货数",
                "version": 1,
                "asset_id": identity,
                "event_time": clock,
                "summary": "按审核定义计算",
                "known_at": "交接记录生效后可见",
                "formula": f"各货单取{clock}的最新有效修订",
                "exceptions": "修订作废时回到首报",
            },
            "time_range": [{"start": "2026-02-01", "end": "2026-02-10"}],
            "unit": "件",
        }

    posted = observation("dispatch", "出库交接时间")
    signed = observation("delivered", "收货人签收时间")
    text = "\n".join(scope_lines({"period1": posted, "period2": posted, "different": signed}))
    assert text.count("业务时间 出库交接时间") == 1 and text.count("业务时间 收货人签收时间") == 1
    assert text.count("交接记录生效后可见") == 2
    assert text.count("最新有效修订") == 2 and text.count("修订作废时回到首报") == 2


def test_source_recheck_retries_temporary_failure_and_immediately_honors_revocation(monkeypatch):
    from unittest.mock import Mock

    from compass_agent.context_runtime import source_access
    from compass_agent.models.errors import ModelError
    from compass_agent.schemas import TenantScope
    from compass_agent.semantic import service

    trusted = SimpleNamespace(
        scope=TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4())
    )
    check = Mock(
        side_effect=[
            TimeoutError(),
            True,
            ModelError("forbidden", 403),
            TimeoutError(),
            TimeoutError(),
        ]
    )
    monkeypatch.setattr(service, "read_sources", check)
    monkeypatch.setattr(source_access.time, "sleep", lambda _: None)
    refs = [{"kind": "semantic"}]
    assert source_access.allows(None, trusted, refs)
    assert not source_access.allows(None, trusted, refs)
    assert check.call_count == 3
    with pytest.raises(ModelError, match="source_authorization_unavailable"):
        source_access.allows(None, trusted, refs)
    assert check.call_count == 5


def test_evidence_for_one_named_metric_does_not_complete_a_multi_metric_request():
    import json

    from compass_agent.semantic.clarification import pending_queries

    metrics = [{"name": name, "asset_id": name, "code": name} for name in ("sent", "delivered")]
    journal = SimpleNamespace(registry_initial_context={"requested_metrics": metrics})

    def observation(tool, item, **fields):
        return SimpleNamespace(
            tool_calls=[SimpleNamespace(function=SimpleNamespace(name=tool))],
            tool_results=[
                SimpleNamespace(
                    status="ok", observation_json=json.dumps({"metric": item, **fields})
                )
            ],
        )

    steps = [
        observation("query_metric", metrics[0], resultId="receipt-1"),
        observation("resolve_semantics", metrics[1], query_ref="issued-plan"),
    ]
    correction = pending_queries(journal, steps)
    assert correction and correction["missing"] == [
        {"name": "delivered", "code": "delivered", "query_ref": "issued-plan"}
    ]
    assert (
        pending_queries(journal, [*steps, observation("query_metric", metrics[1], resultId="r2")])
        is None
    )


@pytest.mark.parametrize("selected", [False, True], ids=["single-result", "selected-from-several"])
def test_approved_draft_uses_original_receipt_and_owner_edits(monkeypatch, selected):
    import asyncio
    from unittest.mock import Mock

    from compass_agent.schemas import RunCompleted
    from compass_agent.semantic import results
    from compass_agent.semantic.clarification import continue_selected_result

    run, action, ref, epoch = (uuid4() for _ in range(4))
    fields = {"code": "weekly_shipments", "name": "每周发货数", "domain": "履约"}
    original = (
        {"origin": "selected-result", "result_ref": str(ref)}
        if selected
        else {"args": {"result_ref": str(ref)}}
    )
    approved = {**original, "kind": "semantic_draft", "actionId": str(action), "fields": fields}
    journal = SimpleNamespace(run_id=run, epoch=epoch, approved_proposals=[approved])
    service = SimpleNamespace(journal=journal, store=object(), trusted=object())
    decide = Mock()
    monkeypatch.setattr(results, "decide_result", decide)
    completed = asyncio.run(continue_selected_result(service))
    assert isinstance(completed, RunCompleted) and fields["name"] in completed.final_answer
    decide.assert_called_once_with(
        service.store,
        service.trusted,
        run,
        SemanticResultDecision(result_ref=ref, action="save", fields={**fields}),
        for_tool=True,
        action_id=action,
        epoch=epoch,
    )


def test_finance_scope_discovers_user_plugins_only_when_granted_in_registry_mode():
    from compass_agent.context_runtime.profiles import resolve_profile

    def available(registry, plugins):
        return resolve_profile(
            "finance",
            "business_qa",
            "SC-01",
            "query",
            available=("database_query", "resolve_semantics", "mcp_private_shipping"),
            plugins=plugins,
            registry=registry,
        ).tools

    assert available(False, True) == ("database_query",)
    assert available(True, False) == ("database_query", "resolve_semantics")
    assert "mcp_private_shipping" in available(True, True)


def test_dated_definition_and_cross_turn_save_follow_their_respective_routes(monkeypatch):
    import asyncio
    from typing import Any

    from compass_agent.execution_context import current_execution
    from compass_agent.runtime.task_state import TaskState
    from compass_agent.runtime.turn import RuntimeTurn
    from compass_agent.schemas import AnalysisFileNone, ChatMessage, RunCompleted
    from compass_agent.semantic import clarification

    done = RunCompleted(
        status="completed", finalAnswer="已处理", file=AnalysisFileNone(status="none")
    )
    native_check = AsyncMock()
    continuation = AsyncMock(return_value=done)
    monkeypatch.setattr(clarification, "check", native_check)
    monkeypatch.setattr(clarification, "continue_selected_result", continuation)
    runner: Any = object.__new__(RuntimeTurn)
    runner.execute_tools = AsyncMock(return_value=done)
    state = TaskState(
        [ChatMessage(role="user", content="请解释 2026-03-01 至 2026-03-08 的统计定义。")]
    )
    journal: Any = SimpleNamespace(
        store=None, context_binding={"registry": {"pins": []}}, approved_proposals=[]
    )
    token = current_execution.set(journal)
    try:
        route = SimpleNamespace(deliverable="business_qa", task_type="指标解释")
        assert asyncio.run(runner.execute_turn(state, route, None, None, None, None)) == done
        native_check.assert_not_awaited()
        state = TaskState([ChatMessage(role="user", content="本月履约量是多少？")])
        route.task_type = "查数查询"
        native_check.return_value = done
        assert asyncio.run(runner.execute_turn(state, route, None, None, None, None)) == done
        native_check.assert_awaited_once()
        route.deliverable = "existing"
        journal.approved_proposals = [{"kind": "semantic_result_choice"}]
        assert asyncio.run(runner.execute_turn(state, route, None, None, None, None)) == done
        continuation.assert_awaited_once()
        runner.execute_tools.assert_awaited_once()
    finally:
        current_execution.reset(token)


def test_cancel_during_context_reads_drains_metadata_before_releasing_the_worker():
    import asyncio

    from compass_agent.context_runtime.model_step import finish_context_read
    from compass_agent.tools.contracts import PhaseCanceled

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def read():
            entered.set()
            await release.wait()

        pending = asyncio.create_task(finish_context_read(read()))
        await entered.wait()
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()
        release.set()
        with pytest.raises(PhaseCanceled) as canceled:
            await pending
        assert canceled.value.cleanup_confirmed

    asyncio.run(exercise())


def test_known_at_limits_published_metrics_and_historical_queries():
    from datetime import UTC, datetime

    from compass_agent.semantic.service import effective_cutoff
    from compass_agent.tools.contracts import ToolRejected

    accepted = datetime(2026, 1, 2, tzinfo=UTC)
    cutoff = datetime(2025, 10, 9, 1, tzinfo=UTC)
    snapshot = {"knownAt": "2025-10-09T09:00:00+08:00"}
    assert effective_cutoff(accepted, None) == accepted
    assert effective_cutoff(accepted, snapshot) == cutoff
    assert effective_cutoff(cutoff, {"knownAt": accepted.isoformat()}) == cutoff
    earlier = datetime(2025, 10, 8, tzinfo=UTC)
    assert effective_cutoff(accepted, snapshot, earlier.isoformat()) == earlier
    for candidate in (accepted.isoformat(), "2025-10-08", "invalid"):
        with pytest.raises(ToolRejected, match="invalid_parameters"):
            effective_cutoff(accepted, snapshot, candidate)
    with pytest.raises(ToolRejected, match="semantic_source_unavailable"):
        effective_cutoff(accepted, {"knownAt": "2025-10-09"})
