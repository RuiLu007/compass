"""Emit fixed questions and expected semantic decisions from the L22 requirements as JSONL."""

import json
from datetime import date, timedelta

# Display names and business rules come from the content PRD. All five identities below
# are fixtures. A persona only selects the signed-in fixture, never an HTTP permission.
DATES = ("2025-10-01", "2025-10-15", "2025-11-01", "2025-11-17", "2025-12-01")
INTENTS = [
    ("101.sales", "sales.order_count", "total", ("下单订单数", "下单了多少单")),
    ("102.sales", "sales.order_count", "region", ("分地区列出下单数", "各销售地区的订单数")),
    ("101.sales", "sales.paid_gmv", "day", ("每日支付成交额", "支付 GMV 按天给我")),
    ("101.sales", "sales.net_gmv", "channel", ("分渠道现金流净 GMV", "各渠道退款后成交额")),
    ("101.finance", "finance.recognized_revenue", "total", ("确认收入合计", "妥投净收入总额")),
    ("101.finance", "finance.recognized_revenue", "day", ("每日确认收入", "每天的妥投净收入")),
    ("101.marketing", "marketing.net_roas", "total", ("平台净 ROAS", "广告投放净回报倍数")),
    (
        "101.sales",
        "inventory.closing_available_qty",
        "total",
        ("期末可用库存件数", "收盘可售未预留件数"),
    ),
    (
        "101.sales",
        "channel.reported_paid_amount",
        "channel",
        ("分渠道的报表实付金额", "按渠道汇总最新报表实付金额"),
    ),
    (
        "101.sales",
        "channel.first_report_paid_amount",
        "day",
        ("每日渠道首报实付金额", "按日报渠道的首报实付金额"),
    ),
]
AMBIGUITY = {
    "101.sales": ("resolve", ["sales.paid_gmv"]),
    "101.finance": ("resolve", ["finance.recognized_revenue"]),
    "102.sales": ("resolve", ["sales.net_gmv"]),
    "101.sales_finance": ("clarify", ["sales.paid_gmv", "finance.recognized_revenue"]),
    "101.no_query": ("deny", []),
}
SALES_QUESTIONS = (
    "销售额合计多少？",
    "帮我取一下销售额。",
    "想核对这段时间的销售额。",
    "给周会准备个销售额数字。",
    "这几天销售额一共多少？",
)
# Every report must preserve metric identity and revenue/cash grain, time window and scope.
# The assertion keys refer to business observations in TEST_PLAN.md, independent of tool order.
COMPLEX = [
    (
        "101.sales_finance",
        "对账支付GMV、现金流净GMV和财务确认收入，分别列金额和口径，再算两个口径差额。",
        ["sales.paid_gmv", "sales.net_gmv", "finance.recognized_revenue"],
        ["three_values", "paid_minus_net", "net_minus_recognized", "payment_refund_posting_clocks"],
    ),
    (
        "101.sales",
        "列出最新报表各渠道实付汇总，与渠道6首报的逐日实付另列，并核对首报日额合计。",
        ["channel.reported_paid_amount", "channel.first_report_paid_amount"],
        ["latest_by_channel", "pos_first_by_day", "pos_first_total", "revision_labels"],
    ),
    (
        "101.sales",
        "先给全租户订单总数，再按原销售地区和原订单渠道分别列出订单数，核对两种分组。",
        ["sales.order_count"],
        ["total_orders", "orders_by_region", "orders_by_channel", "both_sums_match"],
    ),
    (
        "101.sales",
        "比较期末地点1、SKU9可用库存和同期成功支付总额，列出期间、截止时刻和两种单位。",
        ["inventory.closing_available_qty", "sales.paid_gmv"],
        ["closing_site_sku", "paid_total", "piece_and_currency", "last_snapshot_only"],
    ),
    (
        "102.sales",
        "对比本期和前一等长期间的净GMV，按原渠道列两期净额、差额及本期总额，注明地区范围。",
        ["sales.net_gmv"],
        ["current_by_channel", "previous_by_channel", "channel_deltas", "region_1_total"],
    ),
]


def period(start):
    day = date.fromisoformat(start)
    return {"start": start, "end": (day + timedelta(days=7)).isoformat()}


def prompt_dates(p):
    return f"从 {p['start']}（含）到 {p['end']}（不含），"


def samples():
    for tag, values, dates in (
        ("identification", INTENTS, DATES),
        ("ambiguity", AMBIGUITY, DATES[:4]),
    ):
        index = 0
        for start in dates:
            p = period(start)
            if tag == "identification":
                for persona, metric, dim, phrases in values:
                    for phrase in phrases:
                        index += 1
                        yield {
                            "id": f"I{index:03}",
                            "stratum": tag,
                            "persona": persona,
                            "question": prompt_dates(p) + phrase + "。",
                            "expected": {
                                "action": "resolve",
                                "metrics": [metric],
                                "group_by": dim,
                                **p,
                            },
                        }
            else:
                for persona, (action, metrics) in values.items():
                    for phrase in SALES_QUESTIONS:
                        index += 1
                        yield {
                            "id": f"D{index:03}",
                            "stratum": tag,
                            "persona": persona,
                            "question": prompt_dates(p) + phrase,
                            "expected": {"action": action, "metrics": metrics, **p},
                        }
    for n, start in enumerate(DATES[:4]):
        p = period(start)
        for i, (persona, task, metrics, assertions) in enumerate(COMPLEX, start=1):
            yield {
                "id": f"C{n * len(COMPLEX) + i:03}",
                "stratum": "complex",
                "persona": persona,
                "question": prompt_dates(p) + task,
                "expected": {"metrics": metrics, "assertions": assertions, **p},
            }


if __name__ == "__main__":
    for sample in samples():
        print(json.dumps(sample, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
