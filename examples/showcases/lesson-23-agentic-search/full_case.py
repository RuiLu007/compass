"""Instructor-driven examination of a single Run's complete retail case."""

from datetime import date, timedelta
from decimal import Decimal

# The document query is completed before the recovery dates are passed to any detail query.
WINDOWS = """
WITH windows(lo,hi) AS (VALUES (DATE '{fault}',DATE '{after_fault}'),
                               (DATE '{received}',DATE '{after_received}'))
"""
FUNNEL = (
    WINDOWS
    + """
, reports AS (
  SELECT DISTINCT ON (d.report_date) d.report_date,d.product_visitors,d.submitted_orders,
         d.paid_orders,d.paid_amount_cent
  FROM core.channel_product_daily d JOIN windows w ON d.report_date>=w.lo AND d.report_date<w.hi
  WHERE d.tenant_id=101 AND d.region_id=1 AND d.channel_id=4 AND d.sku_id=435
    AND d.available_at<='{known}'
  ORDER BY d.report_date,d.report_version DESC
), periods AS (
  SELECT DISTINCT ON (s.period_start) s.period_start,s.status FROM ops.asset_period_status s
  JOIN windows w ON s.period_start>=w.lo AND s.period_start<w.hi
  WHERE s.tenant_id=101 AND s.asset_id='core.channel_product_daily'
    AND s.period_end=s.period_start+1 AND s.available_at<='{known}'
  ORDER BY s.period_start,s.observed_at DESC
)
SELECT min(rpt.report_date) AS first_day,max(rpt.report_date) AS last_day,
 count(rpt.report_date) AS report_days,
 count(rpt.report_date) FILTER(WHERE p.status='COMPLETE') AS complete,
 sum(rpt.product_visitors) AS visitor_days,sum(rpt.submitted_orders) AS submitted,
 sum(rpt.paid_orders) AS paid,sum(rpt.paid_amount_cent) AS paid_cent
FROM windows w JOIN reports rpt ON rpt.report_date>=w.lo AND rpt.report_date<w.hi
JOIN periods p ON p.period_start=rpt.report_date GROUP BY w.lo ORDER BY w.lo
"""
)
SESSIONS = (
    WINDOWS
    + """
SELECT min((b.event_at AT TIME ZONE 'Asia/Shanghai')::date) AS first_day,
 count(DISTINCT b.session_id) AS sessions,
 count(DISTINCT b.session_id) FILTER (WHERE c.campaign_id IS NOT NULL) AS tagged,
 count(DISTINCT b.session_id) FILTER (WHERE s.campaign_id IS NULL) AS unmarked
FROM windows w JOIN core.behavior_event b ON b.event_at>=w.lo AND b.event_at<w.hi
JOIN core.web_session s ON s.tenant_id=b.tenant_id AND s.region_id=b.region_id
 AND s.session_id=b.session_id AND s.channel_id=4 AND s.available_at<='{known}'
LEFT JOIN core.campaign c ON c.tenant_id=s.tenant_id AND c.campaign_id=s.campaign_id
 AND c.target_channel_id=s.channel_id AND b.event_at>=c.starts_at AND b.event_at<c.ends_at
 AND c.available_at<='{known}'
WHERE b.tenant_id=101 AND b.region_id=1 AND b.sku_id=435 AND b.event_type='PRODUCT_VIEW'
 AND b.available_at<='{known}' GROUP BY w.lo ORDER BY w.lo
"""
)
ALLOCATION = (
    WINDOWS
    + """
SELECT min((a.attempted_at AT TIME ZONE 'Asia/Shanghai')::date) AS first_day,
 count(*) AS attempts,count(*) FILTER(WHERE a.outcome='INSUFFICIENT') AS short,
 count(*) FILTER(WHERE a.outcome='COMMITTED') AS committed,
 count(*) FILTER(WHERE a.outcome='ROLLED_BACK') AS rolled_back
FROM windows w JOIN core.stock_allocation_attempt a ON a.attempted_at>=w.lo AND a.attempted_at<w.hi
JOIN core.order_header o ON o.tenant_id=a.tenant_id AND o.order_id=a.order_id
 AND o.region_id=a.region_id AND o.channel_id=4 AND o.available_at<='{known}'
WHERE a.tenant_id=101 AND a.region_id=1 AND a.sku_id=435 AND a.site_id=7
 AND a.available_at<='{known}' GROUP BY w.lo ORDER BY w.lo
"""
)
WINDOW_COMPARISON = f"""
WITH daily AS ({FUNNEL}), visits AS ({SESSIONS}), locks AS ({ALLOCATION})
SELECT daily.first_day,daily.last_day,daily.report_days,daily.complete,daily.visitor_days,
       daily.submitted,daily.paid,daily.paid_cent,visits.sessions,visits.tagged,visits.unmarked,
       locks.attempts,locks.short,locks.committed,locks.rolled_back
FROM daily JOIN visits ON visits.first_day=daily.first_day
JOIN locks ON locks.first_day=daily.first_day
ORDER BY daily.first_day
"""
TRANSFER = """
SELECT m.event_at,m.movement_type,sum(m.sellable_delta) AS sellable_delta
FROM core.inventory_movement m
WHERE m.tenant_id=101 AND m.region_id=1 AND m.site_id=7 AND m.sku_id=435
 AND m.movement_type='TRANSFER_IN' AND m.available_at<='{known}'
 AND m.event_at>=DATE '{fault}' AND m.event_at<DATE '{after_received}'
 GROUP BY m.event_at,m.movement_type ORDER BY m.event_at
"""
STOCK = """
SELECT s.snapshot_date,s.sellable_qty,s.reserved_qty,s.available_qty FROM core.inventory_snapshot s
WHERE s.tenant_id=101 AND s.region_id=1 AND s.site_id=7 AND s.sku_id=435
 AND s.snapshot_date=DATE '2025-10-08' AND s.as_of='2025-10-08 23:59:59+08'
 AND s.available_at<='{known}' AND EXISTS (
  SELECT 1 FROM core.assortment a WHERE a.tenant_id=s.tenant_id AND a.region_id=s.region_id
  AND a.site_id=s.site_id AND a.sku_id=s.sku_id AND a.available_at<='{known}'
  AND a.valid_from<=s.snapshot_date AND a.valid_to>s.snapshot_date)
"""
COVERAGE = """
SELECT min(s.snapshot_date) AS first_snapshot FROM core.inventory_snapshot s
WHERE s.tenant_id=101 AND s.region_id=1 AND s.site_id=7 AND s.sku_id=435
 AND s.available_at<='{known}'
"""


def rows(payload):
    return [dict(zip(payload["columns"], r, strict=True)) for r in payload["rows"]]


async def collect_full(call, query, read, evidence, source, claim, known, received, stop):
    await read("hypotheses", "history", "H-0912", "解释")
    await read("interpret", "knowledge", "K-08", "§3")
    fault = date(2025, 9, 7)
    assert "9 月 7 日至 11 日" in source["balance"]["sections"][0]["text"]
    sql_dates = dict(
        known=known,
        fault=fault,
        after_fault=fault + timedelta(days=5),
        received=received,
        after_received=stop,
    )
    for schema, table_name in (
        ("core", "channel_product_daily"),
        ("ops", "asset_period_status"),
        ("core", "stock_allocation_attempt"),
        ("core", "inventory_snapshot"),
        ("core", "inventory_document_line"),
        ("core", "procurement_revision"),
    ):
        await call(
            "database_query", {"op": "describe_table", "schema": schema, "table": table_name}
        )
    for key, stmt in (
        ("windows", WINDOW_COMPARISON),
        ("transfer", TRANSFER),
        ("closing", STOCK),
        ("coverage", COVERAGE),
    ):
        await call("database_query", {"op": "query", "sql": stmt.format(**sql_dates)}, key)
    sales = {"region": 1, "channel": 4}
    for month, end, key in (
        ("2025-07-01", "2025-08-01", "jul"),
        ("2025-08-01", "2025-09-01", "aug"),
        ("2025-09-01", "2025-10-01", "sep"),
    ):
        await query(key + "_paid", "sales.paid_gmv", month, end, sales)
        if key == "jul":
            await query("jul_net", "sales.net_gmv", month, end, sales)
    for key, start, end in (("fault", fault, fault + timedelta(days=5)), ("back", received, stop)):
        await query(
            key + "_money", "channel.reported_paid_amount", start, end, {**sales, "sku": 435}
        )
    await query(
        "fault_rate",
        "fulfillment.allocation_insufficient_rate",
        fault,
        fault + timedelta(days=5),
        {**sales, "sku": 435, "fulfillment_site": 7},
    )

    def cell(key, record, row, col):
        claim(key, "cell", evidenceId=evidence[record], row=row, column=col)
        return key

    def table(key, record, cols):
        parts = [
            cell(key + str(i) + col, record, i, col)
            for i in range(len(source[record]["rows"]))
            for col in cols
        ]
        claim(key, "series", parents=parts)

    for key, record, quote in (
        (
            "context",
            "balance",
            "对应商品是逐风·晨跑 R100 缓震跑鞋，黑色、欧码 42，由苏州华东配送中心备货。",
        ),
        ("rivals", "hypotheses", "渠道组怀疑进店流量不足，仓配组提到个别尺码接单困难。"),
        (
            "traffic",
            "interpret",
            "没有活动标记的会话保留为“未标记”。商品日报逐日去重的访客数可用于看每日热度，"
            "跨天加总时须写明单位为访客人日。",
        ),
    ):
        claim(key, "quote", evidenceId=evidence[record], quote=quote)
    paid = [cell(key, key, 0, "value") for key in ("jul_paid", "aug_paid", "sep_paid")]
    net = [cell("jul_net", "jul_net", 0, "value"), "previous", "current"]
    claim(
        "monthly", "series", parents=[key for pair in zip(paid, net, strict=True) for key in pair]
    )
    for month, (a, b) in enumerate(zip(paid, net, strict=True)):
        claim("refund" + str(month), "subtract", parents=[a, b])
    claim("refunds", "series", parents=["refund0", "refund1", "refund2"])
    # The signed columns and their scope carry the provenance of every displayed cell.
    for key, record, cols in (
        (
            "daily",
            "windows",
            [
                "first_day",
                "sessions",
                "submitted",
                "paid",
                "paid_cent",
                "short",
                "committed",
                "tagged",
            ],
        ),
        ("counting", "windows", ["first_day", "complete", "visitor_days", "attempts", "unmarked"]),
        ("transfer", "transfer", ["event_at", "movement_type", "sellable_delta"]),
        ("closing", "closing", ["snapshot_date", "sellable_qty", "reserved_qty", "available_qty"]),
    ):
        table(key, record, cols)
    row = next(i for i, r in enumerate(rows(source["arrival"])) if r["document_no"] == "DOC13392")
    plan = [
        cell(k, "arrival", row, k)
        for k in ("quantity", "planned_at", "notified_at", "revised_due_at")
    ]
    claim("purchase_plan", "series", parents=plan)
    claim("unpaid_delta", "subtract", parents=["daily1submitted", "daily1paid"])
    cell("first_snapshot", "coverage", 0, "first_snapshot")
    for key in ("fault_money", "back_money", "fault_rate"):
        cell(key, key, 0, "value")
    claim("checked_metrics", "series", parents=["fault_money", "back_money", "fault_rate", "rate"])
    return [
        "context",
        "rivals",
        "monthly",
        "refunds",
        "daily",
        "unpaid_delta",
        "counting",
        "checked_metrics",
        "purchase_plan",
        "transfer",
        "arrival",
        "closing",
        "first_snapshot",
        "traffic",
        "expansion",
        "cap",
    ]


def verify_full(source):
    for key, expected in (
        (
            "windows",
            [
                ["2025-09-07", "2025-09-11", 5, 5, 80, 68, 0, 0, 80, 0, 80, 68, 68, 0, 0],
                ["2025-09-16", "2025-09-20", 5, 5, 79, 57, 52, 1867320, 80, 0, 80, 57, 0, 57, 0],
            ],
        ),
        ("closing", [["2025-10-08", 407, 1, 406]]),
        ("coverage", [["2025-10-01"]]),
    ):
        values = [[str(cell) for cell in row] for row in source[key]["rows"]]
        assert values == [[str(cell) for cell in row] for row in expected], (key, values)
    for key, value in (
        ("jul_paid", 35296820),
        ("aug_paid", 7368475),
        ("sep_paid", 20455095),
        ("jul_net", 29373605),
        ("previous", 1981070),
        ("current", 18084020),
        ("fault_money", 0),
        ("back_money", 1867320),
        ("fault_rate", 1),
        ("rate", 0),
        ("stock", 406),
    ):
        (result,) = rows(source[key])
        assert result["status"] == "OK" and Decimal(str(result["value"])) == value, (key, result)
    received = next(r for r in rows(source["arrival"]) if r["document_no"] == "DOC13392")
    for key, day in (
        ("planned_at", 6),
        ("notified_at", 4),
        ("revised_due_at", 16),
        ("received_at", 16),
    ):
        assert str(received[key]).startswith(f"2025-09-{day:02}")
    assert int(received["quantity"]) == int(received["received_units"]) == 400
    (transfer,) = rows(source["transfer"])
    assert (
        str(transfer["event_at"]).startswith("2025-09-14") and int(transfer["sellable_delta"]) == 80
    )


def verify_full_citations(by_key):
    """Look up each statement in the final published report's authorized citation manifest."""
    expected = {
        "monthly": (
            "metric",
            "352,968.20",
            "73,684.75",
            "204,550.95",
            "293,736.05",
            "19,810.70",
            "180,840.20",
        ),
        "refunds": ("metric", "59,232.15", "53,874.05", "23,710.75"),
        "daily": (
            "detail",
            "2025-09-07",
            "2025-09-16",
            "80",
            "68 单",
            "57 单",
            "52 单",
            "18,673.20",
        ),
        "unpaid_delta": ("detail", "5 单"),
        "counting": ("detail", "2025-09-07", "2025-09-16", "5 次", "79"),
        "checked_metrics": ("metric", "2025-09-11", "2025-09-20", "100%", "0%"),
        "purchase_plan": ("detail", "2025-09-04", "2025-09-06", "2025-09-16", "400"),
        "transfer": ("detail", "2025-09-14", "TRANSFER_IN", "80"),
        "arrival": ("detail", "DOC13392", "2025-09-16", "400", "2025-09-20"),
        "traffic": ("knowledge", "未标记"),
        "closing": ("detail", "2025-10-08", "407 件", "1 件", "406 件"),
        "first_snapshot": ("detail", "2025-10-01"),
        "expansion": ("knowledge", "0 次", "20 次", "试投的实际消耗与回款"),
        "cap": ("history", "20,000.00", "40%", "8,000.00"),
        "action": ("external", "华东渠道经理", "2025-10-09 15:00", "2025-10-11 10:00"),
    }
    for key, (kind, *values) in expected.items():
        citation = by_key[key]
        assert citation["status"] == "supported" and kind in {
            s["kind"] for s in citation["sources"]
        }
        assert all(v in citation["statement"] for v in values), (key, citation["statement"])
