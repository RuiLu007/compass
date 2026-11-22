"""Compare 100 frozen retail queries with independent PostgreSQL aggregates or PRD values."""

import argparse
import hashlib
import json
import os
import sys
from contextlib import ExitStack
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from verify_retail_content import FINAL_K, RELEASE

# The eight distinct business windows cover both tenants and source grains. The last four
# examples fix reporting vintages and a one-second availability boundary from the content PRD.
WINDOWS = (1, 2, 3, 5, 7, 8, 10, 11)
ORDER = "sales.order_count"
PAID = "sales.paid_gmv"
NET = "sales.net_gmv"
FINANCE = "finance.recognized_revenue"
FIRST = "channel.first_report_paid_amount"
LATEST = "channel.reported_paid_amount"
ROAS = "marketing.net_roas"
STOCK = "inventory.closing_available_qty"
UNITS = {
    ORDER: "order",
    PAID: "CNY_fen",
    NET: "CNY_fen",
    FINANCE: "CNY_fen",
    FIRST: "CNY_fen",
    LATEST: "CNY_fen",
    ROAS: "ratio",
    STOCK: "piece",
}


def cases():
    for month in WINDOWS:
        start = date(2025, month, 1)
        end = start + timedelta(days=7)
        params = {"start": start.isoformat(), "end": end.isoformat(), "as_of": FINAL_K}
        for tenant in (101, 102):
            for metric in (ORDER, PAID, NET, FIRST, LATEST):
                filters = {"channel": 6} if metric == FIRST else {}
                yield tenant, metric, {**params, "filters": filters, "group_by": "total"}
        yield 101, FINANCE, {**params, "filters": {}, "group_by": "total"}
        yield 101, LATEST, {**params, "filters": {}, "group_by": "channel"}
    for cutoff in ("2025-06-10T00:00:00+08:00", "2025-08-02T00:00:00+08:00"):
        yield (
            101,
            ROAS,
            {
                "start": "2025-06-01",
                "end": "2025-06-02",
                "as_of": cutoff,
                "filters": {"campaign": 30, "channel": 3},
                "group_by": "total",
            },
        )
    for cutoff in ("2025-10-01T23:59:59+08:00", "2025-10-02T00:00:00+08:00"):
        yield (
            101,
            STOCK,
            {
                "start": "2025-10-01",
                "end": "2025-10-02",
                "as_of": cutoff,
                "filters": {"site": 1, "sku": 9},
                "group_by": "total",
            },
        )


def expected(conn, metric, p):
    if metric == ROAS:
        refund = 0 if p["as_of"].startswith("2025-06") else 6_800
        ratio = Decimal(22_795 - refund) / Decimal(8_053)
        return {"total": (ratio.quantize(Decimal("0.0000000000000001")), "OK")}
    if metric == STOCK:
        if p["as_of"].startswith("2025-10-01"):
            return {"total": (None, "SNAPSHOT_NOT_AVAILABLE")}
        return {"total": (9, "OK")}

    stamps = (p["start"] + " 00:00+08", p["end"] + " 00:00+08", p["as_of"])
    if metric == ORDER:
        value = conn.execute(
            """SELECT count(DISTINCT order_id) FROM order_header
               WHERE ordered_at >= %s AND ordered_at < %s AND available_at <= %s""",
            stamps,
        ).fetchone()[0]
    elif metric in (PAID, NET):
        value = conn.execute(
            """SELECT coalesce(sum(amount_cent),0) FROM payment WHERE status='SUCCEEDED'
               AND attempted_at >= %s AND attempted_at < %s AND available_at <= %s""",
            stamps,
        ).fetchone()[0]
        if metric == NET:
            refunds = conn.execute(
                """SELECT coalesce(sum(amount_cent),0) FROM refund WHERE status='SUCCEEDED'
                   AND attempted_at >= %s AND attempted_at < %s AND available_at <= %s""",
                stamps,
            ).fetchone()[0]
            value -= refunds
    elif metric == FINANCE:
        value = conn.execute(
            """SELECT coalesce(sum(amount_cent),0) FROM revenue_entry
               WHERE recognized_at >= %s AND recognized_at < %s AND available_at <= %s""",
            stamps,
        ).fetchone()[0]
    else:
        dates = (p["start"], p["end"], p["as_of"])
        if metric == FIRST:
            value = conn.execute(
                """SELECT sum(paid_amount_cent) FROM channel_product_daily
                   WHERE channel_id=6 AND report_version=1 AND report_date >= %s
                     AND report_date < %s AND available_at <= %s""",
                dates,
            ).fetchone()[0]
            return {"total": (value, "OK" if value is not None else "NO_REPORT_ROWS")}
        # Independently retain the last visible version of each reporting key.
        rows = conn.execute(
            """WITH revisions AS (
                 SELECT channel_id, paid_amount_cent,
                        row_number() OVER (PARTITION BY tenant_id,report_date,channel_id,
                        sku_id,region_id ORDER BY report_version DESC) AS n
                 FROM channel_product_daily
                 WHERE report_date >= %s AND report_date < %s AND available_at <= %s
               ) SELECT channel_id::text,sum(paid_amount_cent) FROM revisions
                 WHERE n=1 GROUP BY channel_id""",
            dates,
        ).fetchall()
        if p["group_by"] == "channel":
            return {key: (value, "OK") for key, value in rows} or {None: (None, "NO_FACTS")}
        value = sum(row[1] for row in rows)
    return {"total": (value, "OK")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", choices=("1", "2"), default="1")
    edition = parser.parse_args().edition
    samples = list(cases())
    fingerprint = hashlib.sha256(
        json.dumps(samples, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    failures = []
    with ExitStack() as stack:
        web = stack.enter_context(psycopg.connect(os.environ["WEB_TEST_DATABASE_URL"]))
        web.execute("SET TRANSACTION READ ONLY")
        bindings = {
            (source, metric): (query, digest)
            for source, metric, query, digest in web.execute(
                """SELECT b.source_ref,a.code,b.sql_text,b.target_hash
                   FROM web.execution_bindings b JOIN web.semantic_versions v USING(version_id)
                   JOIN web.semantic_assets a USING(asset_id)
                   JOIN web.semantic_source s USING(source_id)
                   WHERE s.locator='compass-retail/semantic-content' AND s.source_version=%s""",
                (edition,),
            )
        }
        sources = {}
        for tenant in (101, 102):
            conn = stack.enter_context(psycopg.connect(os.environ[f"SEMANTIC_RETAIL_{tenant}_URL"]))
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL search_path=pg_catalog,retail_course,pg_temp")
            conn.execute("SET LOCAL statement_timeout='30s'")
            visibility = conn.execute(
                "SELECT tenant_id,release_id,row_security_active('order_header') FROM tenant"
            ).fetchall()
            if visibility != [(tenant, RELEASE, True)]:
                raise ValueError(f"Unexpected source authorization for tenant {tenant}")
            sources[tenant] = conn
        for number, (tenant, metric, p) in enumerate(samples, start=1):
            conn = sources[tenant]
            try:
                with conn.transaction():
                    oracle = expected(conn, metric, p)
                    query, digest = bindings[(f"retail:{tenant}", metric)]
                    if hashlib.sha256(query.encode()).hexdigest() != digest:
                        raise ValueError("Binding fingerprint changed")
                    auth = {
                        "tenant_id": tenant,
                        "data_release": RELEASE,
                        "all_regions": tenant == 101,
                        "region_ids": [] if tenant == 101 else [1],
                    }
                    with psycopg.RawCursor(conn, row_factory=dict_row) as cur:
                        rows = cur.execute(query, (Jsonb(p), Jsonb(auth))).fetchall()
                    projection = {r["group_key"]: (r["value"], r["status"]) for r in rows}
                    if len(rows) != len(projection) or projection != oracle:
                        raise ValueError(f"expected {oracle}, received {projection}")
                    if any(
                        (r["unit"], r["data_release"], r["business_timezone"])
                        != (UNITS[metric], RELEASE, "Asia/Shanghai")
                        or str(r["period_start"]) != p["start"]
                        or str(r["period_end"]) != p["end"]
                        for r in rows
                    ):
                        raise ValueError("Result unit, data release, timezone or window differs")
            except (ValueError, KeyError, psycopg.Error) as error:
                failures.append(
                    {"sample": number, "tenant": tenant, "metric": metric, "error": str(error)}
                )
    print(
        json.dumps(
            {
                "mode": "installed-sql",
                "edition": edition,
                "cohort": fingerprint,
                "samples": len(samples),
                "passed": len(samples) - len(failures),
                "failures": failures,
            },
            ensure_ascii=False,
        )
    )
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
