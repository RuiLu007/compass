from uuid import uuid4

from compass_agent.context_runtime.search_claims import (
    Checked,
    Proposal,
    SearchDraft,
    evaluate,
    format_checked,
    series_table,
)
from compass_agent.context_runtime.search_sql import display_columns, source_columns


def test_detail_table_preserves_receipt_row_and_column_even_when_proposed_out_of_order():
    evidence = {
        "kind": "detail",
        "title": "授权明细",
        "version": "1",
        "locator": "signed",
        "scope": "workspace",
        "available": {"key": "orders.document_no", "count": "count(*)"},
        "units": {"count": "count"},
        "record": {"columns": ["key", "count"], "rows": [["A", 2], ["B", 7]]},
    }
    cells = [
        evaluate(Proposal(key="field", op="cell", row=i, column=c), evidence, {})
        for i in (1, 0)
        for c in ("key", "count")
    ]
    assert series_table(cells) == (("orders.document_no", "count(*)"), ("A", "2 次"), ("B", "7 次"))
    assert series_table(cells[:-1]) == ()
    _, units = source_columns(
        "SELECT sum(t.final_orders) AS total, sum(t.amount_cent) AS paid_orders FROM sales.log t",
        ["total", "paid_orders"],
    )
    assert units == {"total": "order", "paid_orders": "CNY_fen"}
    terms = {
        "sales.log.final_orders": {
            "description": "已结单数；按记账日",
            "source": {"kind": "detail"},
        }
    }
    label = display_columns(
        "SELECT sum(t.final_orders) AS invented FROM sales.log t",
        {"invented": "sum(t.final_orders)"},
        terms,
    )
    assert label == {"invented": ("合计（已结单数）", {"kind": "detail"})}
    assert not display_columns(
        "SELECT * FROM sales.log t WHERE EXISTS (SELECT 1 FROM other.orders t)",
        {"invented": "sum(t.final_orders)"},
        terms,
    )
    body, manifest = format_checked(uuid4(), [Checked("字" * 40, ()), cells[0]], byte_limit=90)
    assert "字" not in body
    marker = manifest["anchors"][0]
    assert body[marker["start"] : marker["end"]] == "[1]"


def test_month_buckets_use_actual_periods_and_amount_differences_in_the_cited_table():
    record = {
        "kind": "metric",
        "record": {
            "columns": ["group_key", "value", "status"],
            "rows": [["2025-07", 4000, "OK"], ["2025-08", 3000, "OK"], ["2025-09", 3450, "OK"]],
            "unit": "CNY_fen",
            "time_range": [{"start": "2025-07-01", "end": "2025-10-01", "group_by": "month"}],
        },
        "available": {"value": "value"},
        "title": "净额",
        "version": "1",
        "scope": "区域甲",
        "locator": "签收结果",
    }
    keys = ["july", "august", "september"]
    cells = {
        k: evaluate(Proposal(key=k, op="cell", row=i, column="value"), record, {})
        for i, k in enumerate(keys)
    }
    assert cells["august"].window == ("2025-08-01", "2025-09-01")
    result = evaluate(Proposal(key="series", op="series", parents=keys), None, cells)
    body, manifest = format_checked(uuid4(), [result])
    assert "| 期间 | 净额 |" in body and "较上期增加 4.50 元" in body
    assert "2025-08-31" in body
    marker = manifest["anchors"][0]
    assert body[marker["start"] : marker["end"]] == "[1]"
    decline = evaluate(
        Proposal(key="falling", op="test_hypothesis", comparator="gt", parents=keys), None, cells
    )
    assert decline.status == "refuted"
    shorthand = SearchDraft.model_validate(
        {
            "targets": [{"key": "trend"}],
            "claimProposals": [
                {
                    "op": "series",
                    "key": "trend",
                    "evidenceId": "e3_1",
                    "rows": [0, 1, 2],
                    "columns": ["value"],
                }
            ],
            "answerBlocks": ["trend"],
        }
    )
    leaves = shorthand.claim_proposals[:-1]
    assert [(leaf.evidence_id, leaf.row, leaf.column) for leaf in leaves] == [
        ("e3_1", i, "value") for i in range(3)
    ]
    assert shorthand.claim_proposals[-1].parents == [leaf.key for leaf in leaves]


def test_literal_window_filters_and_qualified_star_preserve_only_physical_aggregate_values():
    columns, units = source_columns(
        "WITH w(label) AS (VALUES ('period')), "
        "payments AS (SELECT p.*, 400 AS fabricated FROM finance.payment AS p) "
        "SELECT w.label, sum(p.amount_cent) FILTER (WHERE p.status = 'paid'), max(p.fabricated) "
        "FROM payments p CROSS JOIN w GROUP BY w.label",
        ["window", "amount", "fake"],
    )
    assert set(columns) == {"amount"}
    assert "p.amount_cent" in columns["amount"] and "FILTER" in columns["amount"]
    assert units["amount"] == "CNY_fen"
    assert not source_columns("WITH t(x) AS (VALUES (1)) SELECT count(*) FROM t", ["n"])[0]
