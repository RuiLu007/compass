"""Score user-visible Chat snapshots against frozen labels and independent source aggregates."""

import argparse
import csv
import io
import json
import os
import re
from collections import Counter
from contextlib import ExitStack
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from compass_agent.database import get_engine
from compass_agent.runtime.private_state import PrivateState
from compass_agent.semantic.entities import SemanticMetricOffer
from compass_agent.settings import get_settings
from effect_cohort import samples
from score_sql_bindings import FINANCE, FIRST, LATEST, NET, ORDER, PAID, STOCK, expected
from sqlalchemy import select
from sqlalchemy.orm import Session
from verify_retail_content import FINAL_K


def number(text, amount, money=False):
    if amount is None:
        return False
    text = re.sub(
        r"(?<![\d.])\d{1,3}(?:[,，]\d{3})+(?![\d,，])",
        lambda m: m.group().replace(",", "").replace("，", ""),
        text.replace("−", "-").replace("－", "-").replace("＋", "+"),
    )
    forms = {str(int(amount))}
    if money:
        yuan = Decimal(amount) / 100
        forms.update({f"{yuan:.2f}", format(yuan.normalize(), "f")})
    sign = r"\+?" if amount >= 0 else ""
    return any(re.search(r"(?<![\d.+-])" + sign + re.escape(v) + r"(?![\d.])", text) for v in forms)


def report_text(answer, artifacts):
    parts = [answer]
    for item in artifacts:
        if item.get("status") != 200:
            continue
        content = item["text"]
        if item["name"].lower().endswith(".csv"):
            content = "\n".join(" | ".join(row) for row in csv.reader(io.StringIO(content)))
        parts.append(content)
    return "\n".join(parts)


def grouped(db, metric, axis, p):
    times = (p["start"] + " 00:00+08", p["end"] + " 00:00+08", FINAL_K)
    if metric == ORDER:
        queries = {
            "region": """SELECT region_id,count(DISTINCT order_id) FROM order_header
                WHERE ordered_at >= %s AND ordered_at < %s AND available_at <= %s
                GROUP BY region_id""",
            "channel": """SELECT channel_id,count(DISTINCT order_id) FROM order_header
                WHERE ordered_at >= %s AND ordered_at < %s AND available_at <= %s
                GROUP BY channel_id""",
        }
        return dict(db.execute(queries[axis], times))
    if metric == FIRST:
        return {
            str(k): v
            for k, v in db.execute(
                """SELECT report_date,sum(paid_amount_cent) FROM channel_product_daily
                   WHERE report_version=1 AND channel_id=6 AND report_date >= %s
                     AND report_date < %s AND available_at <= %s GROUP BY report_date""",
                (p["start"], p["end"], FINAL_K),
            )
        }
    if metric == LATEST:
        rows = expected(db, metric, {**p, "as_of": FINAL_K, "group_by": "channel"})
        return {k: pair[0] for k, pair in rows.items()}
    if metric == NET:
        return dict(
            db.execute(
                """WITH events AS (
                       SELECT tenant_id,order_id,amount_cent,attempted_at,available_at FROM payment
                       WHERE status='SUCCEEDED'
                       UNION ALL
                       SELECT tenant_id,order_id,-amount_cent,attempted_at,available_at FROM refund
                       WHERE status='SUCCEEDED')
                   SELECT h.channel_id,sum(e.amount_cent) FROM events e JOIN order_header h
                   USING(tenant_id,order_id) WHERE e.attempted_at >= %s AND e.attempted_at < %s
                   AND e.available_at <= %s AND h.available_at <= %s GROUP BY h.channel_id""",
                (*times, FINAL_K),
            )
        )
    raise ValueError(metric)


def rows_visible(text, rows, *, cash, names=None):
    for key, value in rows.items():
        name = names.get(str(key)) if names else None
        numeric = re.compile(r"(?<![\d.])" + re.escape(str(key)) + r"(?![\d.])")
        date_short = str(key)[5:] if isinstance(key, str) and len(key) == 10 else None
        if not any(
            (numeric.search(line) or (name and name in line) or (date_short and date_short in line))
            and number(line, value, cash)
            for line in text.splitlines()
        ):
            return False
    return bool(rows)


def stock_slice(query, report, value):
    filters = query.get("filters") or {}
    if filters == {"site": 1, "sku": 9}:
        return True
    axis = query.get("group_by")
    target = {
        "site": ({"sku": 9}, r"(?:地点|站点|仓库|site(?:_id)?)\s*[:=：]?\s*1(?!\d)"),
        "sku": ({"site": 1}, r"SKU(?:_id)?\s*[:=：]?\s*9(?!\d)"),
    }
    if axis not in target or filters != target[axis][0]:
        return False
    for row in re.split(r"[\n、;；]", report):
        if re.search(target[axis][1], row, re.IGNORECASE):
            values = row
            for _, label in target.values():
                values = re.sub(label, "", values, flags=re.IGNORECASE)
            if number(values, value):
                return True
    return False


def report_checks(case, text, db, channel_names, params):
    p = case["expected"]
    mode = (int(case["id"][1:]) - 1) % 5
    base = {**p, "as_of": FINAL_K, "group_by": "total"}

    def amount(metric):
        return expected(db, metric, base)["total"][0]

    # A full start date anchors an abbreviated exclusive end in the same calendar year.
    short_end = re.escape(p["end"][5:])
    excludes_end = p["start"][:4] == p["end"][:4] and re.search(
        rf"(?:不含|不包括)\s*{short_end}(?!\d)|(?<!\d){short_end}\s*[（(](?:不含|不包括)", text
    )
    notes = {"period": p["start"] in text and (p["end"] in text or bool(excludes_end))}
    if mode == 0:
        paid, net, recognized = [amount(m) for m in (PAID, NET, FINANCE)]
        notes.update(
            three_values=all(number(text, v, True) for v in (paid, net, recognized)),
            paid_minus_net=number(text, paid - net, True),
            net_minus_recognized=number(text, net - recognized, True),
            payment_refund_posting_clocks=all(v in text for v in ("支付", "退款"))
            and ("妥投" in text or "确认收入" in text or "入账" in text),
        )
    elif mode == 1:
        latest = grouped(db, LATEST, "channel", p)
        first = grouped(db, FIRST, "day", p)
        notes.update(
            latest_by_channel=rows_visible(text, latest, cash=True, names=channel_names),
            pos_first_by_day=rows_visible(text, first, cash=True),
            pos_first_total=number(text, sum(first.values()), True),
            revision_labels="首报" in text and ("最新" in text or "修订" in text),
        )
    elif mode == 2:
        notes.update(
            total_orders=number(text, amount(ORDER)),
            orders_by_region=rows_visible(text, grouped(db, ORDER, "region", p), cash=False),
            orders_by_channel=rows_visible(
                text, grouped(db, ORDER, "channel", p), cash=False, names=channel_names
            ),
            both_sums_match=any(term in text for term in ("一致", "相等", "核对", "对齐", "勾稽")),
        )
    elif mode == 3:
        last_day = date.fromisoformat(p["end"]) - timedelta(days=1)
        stock = db.execute(
            """SELECT sum(sellable_qty-reserved_qty) FROM inventory_snapshot
               WHERE site_id=1 AND sku_id=9 AND snapshot_date=%s AND available_at <= %s""",
            (last_day, FINAL_K),
        ).fetchone()[0]
        notes.update(
            closing_site_sku=number(text, stock) and "SKU" in text.upper(),
            stock_filter=any(
                code == STOCK
                and version == 1
                and (q["start"], q["end"]) == (p["start"], p["end"])
                and stock_slice(q, text, stock)
                for code, version, q in params
            ),
            paid_total=number(text, amount(PAID), True),
            piece_and_currency="件" in text and any(u in text for u in ("元", "分", "CNY")),
            last_snapshot_only=any(t in text for t in ("快照", "期末日", str(last_day))),
        )
    else:
        start = date.fromisoformat(p["start"])
        end = date.fromisoformat(p["end"])
        previous = {"start": (start - (end - start)).isoformat(), "end": p["start"]}
        current = grouped(db, NET, "channel", p)
        past = grouped(db, NET, "channel", previous)
        delta = {k: current.get(k, 0) - past.get(k, 0) for k in current.keys() | past.keys()}
        notes.update(
            current_by_channel=rows_visible(text, current, cash=True, names=channel_names),
            previous_by_channel=rows_visible(text, past, cash=True, names=channel_names),
            channel_deltas=rows_visible(text, delta, cash=True, names=channel_names),
            region_1_total=number(text, sum(current.values()), True)
            and any(t in text for t in ("地区", "区域", "region")),
        )
    return notes


def evaluate(case, data, offers, versions, retail, channels):
    p = case["expected"]
    action = p.get("action")
    status = data.get("terminal", "missing")
    messages = [
        packet["event"]["payload"]["content"]["text"]
        for packet in data.get("packets", [])
        if packet["event"]["type"] == "message.upsert"
        and packet["event"]["payload"].get("role") == "assistant"
    ]
    answer = messages[-1] if messages else ""
    results = [row for kind, row in offers if kind == "result"]
    params = []
    for result in results:
        plan = result["plan"]
        code, version = versions.get((plan["asset_id"], plan["version_id"]), (None, 0))
        params.append((code, version, result["parameters"]))
    wanted = set(p["metrics"])
    codes = {code for code, _, _ in params}
    matched = {
        code
        for code, version, query in params
        if version == 1 and (query["start"], query["end"]) == (p["start"], p["end"])
    }
    visible = status == "completed" and bool(answer) and "来源当前不可读取" not in answer
    if action == "clarify":
        wait = data.get("wait") or {}
        options = wait.get("options") or []
        actual = {versions.get((o["id"], o["versionId"])) for o in options}
        return {
            "action": status == "waiting" and wait.get("semanticKind") == "choice",
            "options": len(options) == len(wanted) and actual == {(code, 1) for code in wanted},
            "no_data": not any(kind in {"result", "open"} for kind, _ in offers),
        }
    if action == "deny":
        return {
            "no_data": not any(kind in {"result", "open"} for kind, _ in offers),
            "denied_to_user": visible
            and any(
                w in answer for w in ("无权", "权限", "不可执行", "无法查询", "绑定", "未授权")
            ),
        }
    tenant = int(case["persona"].split(".")[0])
    checks = {
        "visible": visible,
        "metric_version_window": codes == wanted and matched == wanted,
        "source_scope": bool(results)
        and all(
            r["source"]["witness"].get("tenant_id") == tenant
            and r["source"]["witness"].get("all_regions") == (tenant == 101)
            and (tenant == 101 or r["source"]["witness"].get("region_ids") == [1])
            for r in results
        ),
    }
    if case["stratum"] == "identification":
        checks["group_by"] = any(
            (q.get("group_by") or "total") == p["group_by"] and code in wanted
            for code, _, q in params
        )
    elif case["stratum"] == "complex":
        report = report_text(answer, data.get("artifacts", []))
        checks.update(report_checks(case, report, retail[tenant], channels[tenant], params))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", type=Path, help="private directory generated by run_chat_cohort.py"
    )
    parser.add_argument("--group", choices=("identification", "ambiguity", "complex"))
    args = parser.parse_args()
    cfg = get_settings()
    engine = get_engine()
    if not engine.url.database.startswith("compass_agent_test_") or not urlsplit(
        os.environ["WEB_TEST_DATABASE_URL"]
    ).path.startswith("/compass_web_test_"):
        parser.error("dedicated Agent and Web test databases are required")
    private = PrivateState(cfg.compass_runtime_state_key.get_secret_value())
    totals = {}
    detail = []
    with ExitStack() as stack:
        web = stack.enter_context(psycopg.connect(os.environ["WEB_TEST_DATABASE_URL"]))
        web.execute("SET TRANSACTION READ ONLY")
        versions = {
            (str(asset), str(version)): (code, n)
            for asset, version, code, n in web.execute(
                """SELECT a.asset_id,v.version_id,a.code,v.version_no FROM web.semantic_assets a
                JOIN web.semantic_versions v USING(asset_id)"""
            )
        }
        tx = stack.enter_context(Session(engine))
        dbs = {}
        labels = {}
        for tenant in (101, 102):
            source = stack.enter_context(
                psycopg.connect(os.environ[f"SEMANTIC_RETAIL_{tenant}_URL"])
            )
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            source.execute("SET LOCAL search_path=pg_catalog,retail_course,pg_temp")
            dbs[tenant] = source
            labels[tenant] = {
                str(k): v for k, v in source.execute("SELECT channel_id,channel_name FROM channel")
            }
        for case in samples():
            if args.group and case["stratum"] != args.group:
                continue
            path = args.directory / (case["id"] + ".json")
            reply = json.loads(path.read_text()) if path.is_file() else {}
            run = (reply.get("receipt") or {}).get("runId")
            offers = []
            if run:
                rows = tx.scalars(
                    select(SemanticMetricOffer).where(SemanticMetricOffer.run_id == UUID(run))
                )
                offers = [(r.kind, private.open(r.run_id, r.kind, r.private_payload)) for r in rows]
            checks = evaluate(case, reply, offers, versions, dbs, labels)
            status = reply.get("terminal", "missing")
            counts = totals.setdefault(case["stratum"], Counter())
            counts["total"] += 1
            counts[status] += 1
            counts["passed"] += int(all(checks.values()))
            detail.append(
                {
                    "case": case["id"],
                    "run": run,
                    "status": status,
                    "reason": reply.get("state", {}).get("reasonCode"),
                    "failed": [k for k, ok in checks.items() if not ok],
                }
            )
    print(
        json.dumps(
            {"mode": "live-chat-with-published-sources", "counts": totals, "details": detail},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
