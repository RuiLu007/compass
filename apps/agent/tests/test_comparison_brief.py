from uuid import uuid4

import pytest
from compass_agent.context_runtime.comparison_brief import ComparisonBrief, render_brief


def report(current_row=1):
    return ComparisonBrief.model_validate(
        {
            "comparisons": [
                {
                    "label": "直营北区",
                    "baseline": {"evidenceId": "ledger", "row": 0, "column": "paid"},
                    "current": {"evidenceId": "ledger", "row": current_row, "column": "paid"},
                }
            ],
            "notes": [
                {
                    "kind": "action",
                    "text": "安排仓管核实到架时间。",
                    "sources": [{"evidenceId": "handover"}],
                }
            ],
        }
    )


def evidence(first):
    return {
        "ledger": {
            "kind": "detail",
            "version": "2",
            "title": "支付账本",
            "scope": "北区",
            "locator": "汇总",
            "available": {"paid": "sum(paid_fen)"},
            "units": {"paid": "CNY_fen"},
            "record": {"columns": ["paid"], "rows": [[first], [7500]]},
        },
        "handover": {
            "kind": "knowledge",
            "version": "1",
            "title": "交接单",
            "scope": "北区",
            "locator": "签收",
            "record": {"text": "仓管负责复核上架时间。"},
        },
    }


@pytest.mark.parametrize(("first", "expected"), [(10000, "-25.00%"), (0, "基期为零")])
def test_comparison_recomputes_from_receipts_and_attributes_actions(first, expected):
    body, manifest = render_brief(uuid4(), report(), evidence(first).__getitem__)
    assert expected in body and "75.00 元" in body
    assert "建议行动" in body and "安排仓管" in body
    details = list(manifest["details"].values())
    assert [d["status"] for d in details] == ["supported", "insufficient"]
    assert details[0]["sources"][0]["locator"].endswith("行 1 · paid")
    assert details[1]["sources"][0]["excerpt"] == "仓管负责复核上架时间。"


def test_comparison_requires_receipt_cells_and_rejects_an_unopened_candidate():
    data = evidence(10000)
    with pytest.raises(ValueError, match="行列"):
        render_brief(uuid4(), report(9), data.__getitem__)
    data["handover"]["kind"] = "candidate"
    with pytest.raises(ValueError, match="原文"):
        render_brief(uuid4(), report(), data.__getitem__)
