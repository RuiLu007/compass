"""Search publication contracts use real typed expressions and immutable source values."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from compass_agent.context_runtime.search_claims import (
    Checked,
    DraftRevision,
    Proposal,
    SearchDraft,
    SearchGap,
    evaluate,
    next_workday,
    render,
)
from compass_agent.context_runtime.search_sql import source_columns
from compass_agent.schemas import CitationDetail


def record(kind, title, text=None, **kw) -> dict:
    return dict(
        kind=kind,
        title=title,
        version="2",
        scope="企业甲 / 北区 / 2025",
        locator=title,
        current=True,
        available={},
        record={"text": text, "title": title},
        **kw,
    )


def metric(name, value, unit="CNY_fen", status="OK"):
    row = record("metric", name)
    row["record"] = {
        "rows": [["total", value, status]],
        "columns": ["group_key", "value", "status"],
        "unit": unit,
    }
    row["available"] = {k: k for k in row["record"]["columns"]}
    return row


def proposal(k, op, **kwargs):
    return {"key": k, "op": op, **kwargs}


def draft(proposals, keys):
    return SearchDraft.model_validate(
        {
            "targets": [{"key": k} for k in dict.fromkeys(keys)],
            "claimProposals": proposals,
            "answerBlocks": keys,
        }
    )


def setup_offer(price):
    detail = record("detail", "仓库库存")
    detail.update(
        record={"columns": ["available_qty"], "rows": [[150]]},
        available={"available_qty": "available_qty"},
        units={"available_qty": "piece"},
    )
    data = {
        "balance": record("history", "会议纪要", "试验预算有 20,000 元。"),
        "policy": record("knowledge", "预算制度", "本期余额可启用 40%。择高原则为选择最高单档。"),
        "threshold": record("knowledge", "履约门槛", "最新库存须至少 100 件。"),
        "current": metric("本期净额", 150000),
        "old": metric("前期净额", 120000),
        "stock": detail,
        "offer": record(
            "external",
            "试投报价",
            "只能选择一个完整档位。\n|档位|金额|\n|---|---:|\n"
            f"|基础档|{price} 元|\n|全部档|15,000 元|",
        ),
    }
    claims = [
        proposal("balance", "measure", evidenceId="balance", quote="20,000 元"),
        proposal("fraction", "measure", evidenceId="policy", quote="40%"),
        proposal("cap", "multiply", parents=["balance", "fraction"]),
        proposal("rule", "quote", evidenceId="policy", quote="选择最高单档"),
        proposal("current", "cell", evidenceId="current", row=0, column="value"),
        proposal("old", "cell", evidenceId="old", row=0, column="value"),
        proposal("growth", "compare", parents=["current", "old"], comparator="gt"),
        proposal("available", "cell", evidenceId="stock", row=0, column="available_qty"),
        proposal("minimum", "measure", evidenceId="threshold", quote="100 件"),
        proposal("stock_ok", "compare", parents=["available", "minimum"], comparator="ge"),
        proposal(
            "purchase",
            "whole_tier",
            evidenceId="offer",
            parents=["cap", "rule", "growth", "stock_ok"],
        ),
        proposal("retained", "subtract", parents=["balance", "purchase"]),
    ]
    return data, claims


def test_private_revision_reopens_operands_and_cannot_publish_a_stale_purchase():
    data, claims = setup_offer("6,000")
    original = draft(claims, ["purchase", "retained"])
    no_edits = DraftRevision(revise=True)
    assert "建议订基础档" in render(uuid4(), no_edits.apply(original), data)[0]
    replaced, _ = setup_offer("9,000")
    body, _ = render(uuid4(), no_edits.apply(original), replaced)
    assert "暂缓采购" in body and "20,000.00 元" in body
    patch = DraftRevision.model_validate(
        {
            "revise": True,
            "claimProposals": [
                {"key": "retained", "op": "series", "parents": ["new"]},
                {"key": "new", "op": "cell", "evidenceId": "absent", "column": "value", "row": 0},
            ],
        }
    )
    with pytest.raises(ValueError, match="断言 new"):
        render(uuid4(), patch.apply(original), data)
    with pytest.raises(ValueError, match="断言依赖"):
        DraftRevision(revise=True, remove=["cap"]).apply(original)


@pytest.mark.parametrize(
    ("price", "action", "retained"),
    [("6,000", "建议订基础档", "14,000.00 元"), ("9,000", "暂缓采购", "20,000.00 元")],
)
def test_offer_change_recomputes_full_parent_chain_and_server_anchors(price, action, retained):
    data, claims = setup_offer(price)
    body, manifest = render(uuid4(), draft(claims, ["cap", "purchase", "retained", "cap"]), data)
    assert action in body and retained in body and "8,000.00 元" in body
    assert [a["ordinal"] for a in manifest["anchors"]] == [1, 2, 3, 1]
    detail = CitationDetail.model_validate(
        manifest["details"][manifest["anchors"][2]["citationId"]]
    )
    assert {s.kind for s in detail.sources} == {
        "history",
        "knowledge",
        "external",
        "metric",
        "detail",
    }
    assert all(body[a["start"] : a["end"]] == f"[{a['ordinal']}]" for a in manifest["anchors"])


def test_cross_run_claim_ids_unicode_literal_citation_and_undeclared_text():
    data = {"d": record("history", "🔍纪要", "🔎本次讨论保留原来的 [1] 标注。")}
    d = draft([proposal("fact", "quote", evidenceId="d", quote="保留原来的")], ["fact"])
    body, first = render(uuid4(), d, data)
    _, second = render(uuid4(), d, data)
    marker = first["anchors"][0]
    assert body[marker["start"] : marker["end"]] == "[1]" and marker["start"] > body.index("[1")
    assert not set(first["details"]) & set(second["details"])
    with pytest.raises(ValueError):
        render(uuid4(), d, {})
    sneaky = d.model_dump(mode="json", by_alias=True)
    sneaky["body"] = "追加成交 999 元"
    with pytest.raises(ValueError):
        SearchDraft.model_validate(sneaky)


def test_constant_projection_alias_and_untrusted_currency_cannot_supply_facts():
    columns, units = source_columns(
        "SELECT 99 AS paid, sum(o.paid_fen) AS invented, count(*) AS orders FROM core.orders o",
        ["paid", "invented", "orders"],
    )
    assert columns == {"invented": "sum(o.paid_fen)", "orders": "count(*)"}
    assert units == {"invented": "CNY_fen", "orders": "count"}
    for sql in (
        "SELECT 1 AS value",
        "WITH t AS (SELECT 44 AS value) SELECT value FROM t",
        "SELECT x.value FROM (SELECT 44 AS value) x",
    ):
        assert not source_columns(sql, ["value"])[0]
    d = draft(
        [proposal("fact", "cell", evidenceId="signed", row=0, column="value", unit="piece")],
        ["fact"],
    )
    with pytest.raises(ValueError, match="单位"):
        render(uuid4(), d, {"signed": metric("净额", 125)})


def test_incomplete_receipt_cannot_become_true_zero_or_qualify_purchase():
    data, claims = setup_offer("6,000")
    data["current"] = metric("本期净额", None, status="NO_FACTS")
    with pytest.raises(ValueError, match="父断言"):
        render(uuid4(), draft(claims, ["purchase"]), data)
    d = draft(
        [proposal("current", "cell", evidenceId="current", row=0, column="value")], ["current"]
    )
    body, manifest = render(uuid4(), d, data)
    assert "缺少样本" in body and "0.00" not in body
    assert next(iter(manifest["details"].values()))["status"] == "insufficient"
    data["current"] = metric("本期净额", 0)
    assert "0.00 元" in render(uuid4(), d, data)[0]


def test_original_plan_sources_open_gaps_and_counterevidence():
    data, claims = setup_offer("6,000")
    progress = SimpleNamespace(
        targets={
            "growth": SimpleNamespace(
                key="growth", required=True, source="semantic", goal="核验近月变化"
            )
        },
        pin={"kinds": ["semantic", "detail"]},
        stop_reason=None,
        stalled={},
    )
    with pytest.raises(SearchGap, match="保留"):
        render(uuid4(), draft(claims, ["cap"]), data, progress=progress)
    with pytest.raises(SearchGap, match="指定来源"):
        render(
            uuid4(),
            draft(
                [proposal("growth", "cell", evidenceId="stock", row=0, column="available_qty")],
                ["growth"],
            ),
            data,
            progress=progress,
        )
    with pytest.raises(SearchGap, match="可查询"):
        render(
            uuid4(),
            draft([proposal("growth", "gap", reason="missing_receipt")], ["growth"]),
            data,
            progress=progress,
        )
    signed = metric("🍒净收", 900)
    contradicted = deepcopy(signed)
    contradicted["record"]["rows"][0][1] = 1200
    contradicted["locator"] = "另一份签收"
    facts = [
        proposal("first", "cell", evidenceId="a", row=0, column="value"),
        proposal("extra", "cell", evidenceId="b", row=0, column="value"),
    ]
    body, manifest = render(uuid4(), draft(facts, ["first"]), {"a": signed, "b": contradicted})
    assert "分歧" in body and "9.00" not in body
    assert len(next(iter(manifest["details"].values()))["sources"]) == 2


def test_calendar_uses_signed_exceptions_and_requires_coverage():
    calendar = {
        "title": "2025 年工作日历",
        "text": "10 月 1 日至 8 日连休。10 月 11 日虽为星期六，按工作日执行。",
    }
    assert (
        next_workday(
            calendar, "10 月 10 日 10:00 上线", "首个工作日上午 10 时复核", "Asia/Shanghai"
        )
        == "2025-10-11 10:00 Asia/Shanghai"
    )
    with pytest.raises(ValueError, match="覆盖"):
        next_workday(calendar, "2026 年 10 月 10 日上线", "工作日上午 10 时复核", "Asia/Shanghai")


def test_closed_expression_rejects_truncated_sum_and_mismatched_balance():
    n = Checked("数值", (), value=Decimal(10), unit="CNY_fen", coordinate="a", truncated=True)
    with pytest.raises(ValueError):
        evaluate(Proposal(key="sum", op="sum", parents=["a"]), None, {"a": n})
    data, claims = setup_offer("6,000")
    data["other"] = record("history", "其他预算", "另一余额为 20,000 元。")
    claims.insert(-1, proposal("other", "measure", evidenceId="other", quote="20,000 元"))
    claims[-1]["parents"] = ["other", "purchase"]
    with pytest.raises(ValueError, match="同一余额"):
        render(uuid4(), draft(claims, ["retained"]), data)


def test_scope_fields_are_in_details_and_not_repeated_as_answer_lines():
    row = metric("现金流净额", 18084020)
    row["record"].update(
        metric={"summary": "按期间扣除同期退款。"},
        time_range=[{"start": "2025-09-01", "end": "2025-10-01", "filters": {"region": 1}}],
    )
    row["record"]["columns"] += ["unit", "period_start", "period_end"]
    row["record"]["rows"][0] += ["CNY_fen", "2025-09-01", "2025-10-01"]
    row["available"] = {k: k for k in row["record"]["columns"]}
    keys = ["value", "unit", "period_start", "period_end"]
    d = draft([proposal(k, "cell", evidenceId="r", column=k, row=0) for k in keys], keys)
    body, index = render(uuid4(), d, {"r": row})
    assert "180,840.20 元" in body and "2025-09-30" in body and "按期间扣除同期退款" in body
    assert "地区 1" in body and "CNY" not in body and len(index["anchors"]) == 1


def test_expired_threshold_cannot_authorize_later_business_window():
    store = metric("门店实收", 8421280)
    store["record"]["columns"] += ["period_start", "period_end"]
    store["record"]["rows"][0] += ["2025-09-01", "2025-10-01"]
    evidence = {
        "store": store,
        "policy": record(
            "knowledge", "门店批复", "门店 2025 年 8 月 1 日至 31 日单独执行 80,000 元门槛。"
        ),
    }
    d = draft(
        [
            proposal("paid", "cell", evidenceId="store", column="value", row=0),
            proposal("limit", "measure", evidenceId="policy", quote="80,000 元"),
            proposal("qualified", "compare", parents=["paid", "limit"], comparator="ge"),
        ],
        ["qualified"],
    )
    with pytest.raises(ValueError, match="适用期"):
        render(uuid4(), d, evidence)


def test_cte_lineage_and_counterexample_keep_original_meanings():
    columns, units = source_columns(
        "WITH sums AS (SELECT date_trunc('month',original_at) AS m, "
        "SUM(refund_amount_cent) AS cents, 42 AS manufactured FROM core.refunds GROUP BY 1) "
        "SELECT m,cents/100.0 AS amount,manufactured FROM sums",
        ["month", "amount", "invalid"],
    )
    assert "invalid" not in columns and {"month", "amount"} <= set(columns)
    assert units["amount"] == "CNY_yuan"
    data = {"a": metric("本月支付", 1400000), "b": metric("上月支付", 1000000)}
    d = draft(
        [
            proposal("a", "cell", evidenceId="a", row=0, column="value"),
            proposal("b", "cell", evidenceId="b", row=0, column="value"),
            proposal("fell", "test_hypothesis", parents=["a", "b"], comparator="lt"),
        ],
        ["fell"],
    )
    body, manifest = render(uuid4(), d, data)
    assert next(iter(manifest["details"].values()))["status"] == "refuted"
    assert "14,000.00" in body and "10,000.00" in body


def test_referenced_blocks_use_signed_values_and_rule_threshold_direction():
    sales = metric("门店支付", 800, "CNY_fen")
    sales["record"]["time_range"] = [
        {"start": "2025-03-01", "end": "2025-04-01", "group_by": "site", "filters": {"channel": 2}}
    ]
    sales["record"]["rows"][0][0] = "71"
    dictionary = record(
        "definition", "销售场所", "正式指标 filters.site 使用整数业务键。城西店 = 71。"
    )
    dictionary["record"].update(code="dimension.site", kind="entity")
    sales["dictionaries"] = [dictionary]
    data = {"s": sales, "policy": record("knowledge", "申报条件", "满 10 元的门店可申报。")}
    document = SearchDraft.model_validate(
        {
            "targets": [{"key": "store"}],
            "claimProposals": [
                {
                    "key": "raw",
                    "op": "cell",
                    "cell": {"evidenceId": "s", "row": 0, "column": "value"},
                },
                {"key": "money", "op": "multiply", "parents": ["raw"], "factor": 0.01},
                {
                    "key": "minimum",
                    "op": "measure",
                    "quote": {"evidenceId": "policy", "quote": "10 元"},
                },
                {
                    "key": "store",
                    "op": "compare",
                    "comparator": "lt",
                    "parents": ["money", "minimum"],
                },
            ],
            "answerBlocks": [{"key": "store", "text": "可申报 999 元", "claimKeys": ["store"]}],
        }
    )
    body, index = render(uuid4(), document, data)
    assert "城西店" in body and "未达到该门槛" in body and "999" not in body
    assert {"metric", "knowledge", "definition"} == {
        s["kind"] for d in index["details"].values() for s in d["sources"]
    }
    d = draft([proposal("goal", "gap", reason="missing_receipt")], ["goal"])
    progress = SimpleNamespace(targets={}, stop_reason=None, pin={"kinds": ["detail"]})
    with pytest.raises(SearchGap, match="仍有可查询"):
        render(uuid4(), d, {}, progress=progress)
    progress.stop_reason = "budget_exhausted"
    assert "缺少" in render(uuid4(), d, {}, progress=progress)[0]


def test_cast_cannot_publish_altered_type_under_original_money_unit():
    cols, units = source_columns(
        "SELECT paid_cent::boolean AS fake, paid_cent::numeric AS paid FROM core.t",
        ["fake", "paid"],
    )
    assert set(cols) == {"paid"} and units == {"paid": "CNY_fen"}


def test_empty_batch_replay_checks_current_access_and_input_identity(monkeypatch):
    from contextlib import nullcontext

    from compass_agent.context_runtime import search_verification as verification
    from compass_agent.context_runtime import source_access
    from compass_agent.models.errors import ModelError

    private = {}
    scope = {"permit": True}
    uid = uuid4()
    run = SimpleNamespace(id=uid)
    store = SimpleNamespace(
        transaction=lambda: nullcontext(None),
        lock=lambda *args: (None, run),
        require_epoch=lambda *args: None,
    )
    journal = SimpleNamespace(
        store=store, context_binding={"search": {}}, run_id=uid, epoch=uuid4()
    )
    step = SimpleNamespace(index=8, id=uuid4(), tool_calls=[])
    monkeypatch.setattr(
        verification, "assistant_message", lambda tx, uid: SimpleNamespace(source_refs=[])
    )
    monkeypatch.setattr(verification, "allows", lambda *a, **kw: scope["permit"])
    monkeypatch.setattr(source_access, "allows", lambda *a, **kw: scope["permit"])
    monkeypatch.setattr(verification, "record_sources_locked", lambda *a: None)
    monkeypatch.setattr(
        verification,
        "save_private",
        lambda store, tx, run, k, value, refs: private.__setitem__(k, value),
    )
    monkeypatch.setattr(verification, "read_private", lambda store, tx, run, k: private.get(k))
    assert verification.verify_batch(journal, step, [], None) == []
    monkeypatch.setattr(
        verification, "record_sources_locked", lambda *a: pytest.fail("replayed the admission")
    )
    assert verification.verify_batch(journal, step, [], None) == []
    scope["permit"] = False
    with pytest.raises(ModelError) as denied:
        verification.verify_batch(journal, step, [], None)
    assert denied.value.status == 403
    scope["permit"] = True
    step.id = uuid4()
    with pytest.raises(ModelError) as mismatch:
        verification.verify_batch(journal, step, [], None)
    assert mismatch.value.status == 409


def test_partial_projection_uses_read_sections_and_lineage_checked_rows_only():
    from compass_agent.context_runtime.search_claims import format_checked
    from compass_agent.context_runtime.search_verification import _partial_rows

    opened = record("knowledge", "试行规定", "适用范围为本季度。后续条款中还有其他金额。")
    opened["record"]["heading"] = "适用期间"
    snippet = {**opened, "kind": "candidate"}
    assert not list(_partial_rows(snippet))
    rows = list(_partial_rows(opened))
    assert len(rows) == 1 and "其他金额" not in rows[0].text
    detail = record("detail", "授权明细")
    detail.update(
        available={"settled": "sum(p.amount_cent)"},
        units={"settled": "CNY_fen"},
        record={"columns": ["settled", "constant"], "rows": [[1234, 999]], "truncated": False},
    )
    rows.extend(_partial_rows(detail))
    body, manifest = format_checked(uuid4(), rows)
    assert len(manifest["anchors"]) == 2 and "12.34 元" in body and "999" not in body


def test_verified_coverage_limit_closes_its_target_without_retrying_same_query():
    progress = SimpleNamespace(
        targets={
            "stock": SimpleNamespace(
                key="stock", required=True, source="semantic", goal="核实本次库存"
            )
        },
        pin={"kinds": ["semantic"]},
        stop_reason=None,
        stalled={},
    )
    row = metric("最近日结库存", None, "piece", status="OUTSIDE_SNAPSHOT_COVERAGE")
    row["record"]["columns"] += ["period_start", "period_end"]
    row["record"]["rows"][0] += ["2025-09-10", "2025-09-11"]
    evidence = {"receipt": row}
    body, manifest = render(
        uuid4(),
        draft(
            [
                proposal("snap", "cell", evidenceId="receipt", row=0, column="value"),
                proposal("stock", "gap", reason="no_sample", parents=["snap"]),
            ],
            ["stock"],
        ),
        evidence,
        progress=progress,
    )
    assert "2025-09-10" in body and "齐备" in body and "0 件" not in body
    assert next(iter(manifest["details"].values()))["status"] == "insufficient"


def test_case_partition_keeps_the_original_filter_for_additive_aggregate():
    sql = (
        "SELECT CASE WHEN p.paid_at < DATE '2025-04-01' THEN 'earlier' ELSE 'current' END AS part, "
        "sum(p.paid_cent) AS settled FROM core.payment p GROUP BY 1"
    )
    cols, units = source_columns(sql, ["part", "settled"])
    assert "p.paid_at" in cols["part"] and "2025-04-01" in cols["part"]
    row = record("detail", "收款")
    row.update(
        available=cols,
        units=units,
        record={"columns": ["part", "settled"], "rows": [["earlier", 125], ["current", 175]]},
    )
    body, manifest = render(
        uuid4(),
        draft(
            [
                proposal("a", "cell", evidenceId="r", row=0, column="settled"),
                proposal("b", "cell", evidenceId="r", row=1, column="settled"),
                proposal("total", "sum", parents=["a", "b"]),
            ],
            ["total"],
        ),
        {"r": row},
    )
    assert "3.00 元" in body and all(
        "p.paid_at" in s["excerpt"] for d in manifest["details"].values() for s in d["sources"]
    )
    assert not source_columns(
        "SELECT CASE WHEN 5 > 2 THEN 'earlier' ELSE 'current' END AS invented FROM core.payment",
        ["invented"],
    )[0]


def test_business_timezone_month_is_admitted_by_readonly_dispatch_and_lineage():
    from compass_agent.tools.builtin.database_query import SqlRejected, parse_read_sql

    query = (
        "SELECT to_char(p.attempted_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM') AS paid_month, "
        "sum(p.paid_cent) AS paid FROM core.payment p GROUP BY 1"
    )
    checked, relations = parse_read_sql(query)
    assert relations and ("timezone" in checked.lower() or "time zone" in checked.lower())
    labels, units = source_columns(query, ["paid_month", "paid"])
    assert "paid_month" in labels and units["paid"] == "CNY_fen"
    with pytest.raises(SqlRejected):
        parse_read_sql(
            "SELECT other_schema.timezone('Asia/Shanghai', p.paid_at) FROM core.payment p"
        )
