"""Read-only acceptance against the installed Web pack and two restricted Retail logins."""

import argparse
import hashlib
import os
from collections import defaultdict
from contextlib import ExitStack
from decimal import Decimal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

RELEASE = "diagnostic-v3-20261019"
FINAL_K = "2026-04-01T00:00:00+08:00"


def expect(actual, expected, case):
    if actual != expected:
        raise AssertionError(f"{case}: expected {expected!r}, got {actual!r}")


def verify():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", choices=("learner", "managed"), default="learner")
    layout = parser.parse_args().layout
    with ExitStack() as stack:
        web = stack.enter_context(
            psycopg.connect(os.environ["WEB_TEST_DATABASE_URL"], row_factory=dict_row)
        )
        web.execute("SET TRANSACTION READ ONLY")
        bindings = {
            (row["source_ref"], row["code"]): row
            for row in web.execute("""
            SELECT a.code,b.source_ref,b.sql_text,b.target_hash,b.access_requirements
            FROM web.execution_bindings b
            JOIN web.semantic_versions v USING (version_id)
            JOIN web.semantic_assets a USING (asset_id)
            JOIN web.semantic_source s USING (source_id)
            WHERE s.locator='compass-retail/semantic-content' AND s.source_version='1'
        """)
        }
        sources = {}
        for tenant in (101, 102):
            conn = stack.enter_context(
                psycopg.connect(
                    os.environ[f"SEMANTIC_RETAIL_{tenant}_URL"],
                    cursor_factory=psycopg.RawCursor,
                    row_factory=dict_row,
                )
            )
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            names = bindings[f"retail:{tenant}", "sales.order_count"]["access_requirements"][
                "namespace_profiles"
            ][layout]
            conn.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, names))
                )
            )
            conn.execute("SET LOCAL statement_timeout='30s'")
            expect(
                conn.execute("SELECT tenant_id,release_id FROM tenant").fetchall(),
                [{"tenant_id": tenant, "release_id": RELEASE}],
                f"source tenant {tenant}",
            )
            rls = conn.execute("SELECT row_security_active('order_header') AS active").fetchone()[
                "active"
            ]
            expect(rls, True, "source RLS")
            sources[tenant] = conn
        expect(
            sources[102].execute("SELECT DISTINCT region_id FROM order_header").fetchall(),
            [{"region_id": 1}],
            "102 teaching region",
        )
        expect(
            sources[102]
            .execute(
                "SELECT has_column_privilege('campaign_attribution_daily',"
                "'attributed_paid_cent','SELECT') AS allowed"
            )
            .fetchone()["allowed"],
            False,
            "regional access to platform-wide attribution",
        )

        def query(code, start, end, *, at=FINAL_K, tenant=101, group="total", filters=None):
            b = bindings[f"retail:{tenant}", code]
            expect(
                hashlib.sha256(b["sql_text"].encode()).hexdigest(), b["target_hash"], "SQL bytes"
            )
            expect(b["access_requirements"]["retail_tenant_id"], tenant, "workspace source map")
            business = Jsonb(
                dict(start=start, end=end, as_of=at, group_by=group, filters=filters or {})
            )
            authority = Jsonb(
                dict(
                    tenant_id=tenant,
                    data_release=RELEASE,
                    all_regions=tenant == 101,
                    region_ids=[] if tenant == 101 else [1],
                )
            )
            result = sources[tenant].execute(b["sql_text"], (business, authority)).fetchall()
            for row in result:
                expect(row["data_release"], RELEASE, code)
                expect(row["business_timezone"], "Asia/Shanghai", code)
            return result

        for tenant in (101, 102):
            direct = (
                sources[tenant]
                .execute("""
                SELECT count(*) AS n FROM order_header WHERE ordered_at >= '2025-06-01 00:00+08'
                  AND ordered_at < '2025-06-02 00:00+08' AND available_at <= '2026-04-01 00:00+08'
            """)
                .fetchone()["n"]
            )
            order = query("sales.order_count", "2025-06-01", "2025-06-02", tenant=tenant)[0]
            expect(
                (order["value"], order["status"]),
                (direct, "OK"),
                f"public definition / local orders {tenant}",
            )
        denied = query(
            "sales.order_count", "2025-06-01", "2025-06-02", tenant=102, filters={"region": 2}
        )[0]
        expect(
            (denied["value"], denied["status"]), (None, "SCOPE_DENIED"), "region outside identity"
        )
        for code, expected in [
            ("sales.paid_gmv", 6275850),
            ("sales.net_gmv", 5663060),
            ("finance.recognized_revenue", 5472540),
        ]:
            row = query(code, "2025-06-01", "2025-06-02")[0]
            expect((row["value"], row["status"], row["unit"]), (expected, "OK", "CNY_fen"), code)
        late = query(
            "sales.order_count", "2025-09-28", "2025-09-29", at="2025-09-29T00:00:00+08:00"
        )[0]
        expect((late["status"], late["value"]), ("SOURCE_DELAYED", None), "OMS late batches")

        for at, version, ratio in [
            ("2025-06-10T00:00:00+08:00", 1, "2.8306"),
            ("2025-08-02T00:00:00+08:00", 2, "1.9862"),
        ]:
            row = query(
                "marketing.net_roas",
                "2025-06-01",
                "2025-06-02",
                at=at,
                filters={"campaign": 30, "channel": 3},
            )[0]
            detail = row["detail"]
            expect(
                (
                    row["status"],
                    row["value"].quantize(Decimal(".0001")),
                    detail["spend_cent"],
                    detail["activity_days"][0]["attribution_version"],
                ),
                ("OK", Decimal(ratio), 8053, version),
                "attribution K",
            )
        for at, status, value in [
            ("2025-10-01T23:59:58+08:00", "BEFORE_CLOSE", None),
            ("2025-10-01T23:59:59+08:00", "SNAPSHOT_NOT_AVAILABLE", None),
            ("2025-10-02T00:00:00+08:00", "OK", 9),
        ]:
            row = query(
                "inventory.closing_available_qty",
                "2025-10-01",
                "2025-10-02",
                at=at,
                filters={"site": 1, "sku": 9},
            )[0]
            expect((row["status"], row["value"]), (status, value), "closing stock K")
        pos = query(
            "channel.first_report_paid_amount", "2025-11-17", "2025-11-24", filters={"channel": 6}
        )[0]
        expect((pos["value"], pos["detail"]["row_count"]), (4459720, 453), "POS first report")
        pos_days = query(
            "channel.first_report_paid_amount",
            "2025-11-17",
            "2025-11-24",
            filters={"channel": 6},
            group="day",
        )
        expect(
            (len(pos_days), sum(r["value"] for r in pos_days)),
            (7, pos["value"]),
            "POS daily reconciliation",
        )

        code = "channel.reported_paid_amount"
        channel_deltas = defaultdict(Decimal)
        windows = [("2025-11-17", "2025-11-24", -1), ("2025-11-24", "2025-12-01", 1)]
        for start, end, sign in windows:
            rows = query(code, start, end, group="channel")
            # Window ranking independently checks selection of the latest report per key.
            oracle = (
                sources[101]
                .execute(
                    """
              WITH ranked AS (SELECT channel_id,paid_amount_cent,
                   row_number() OVER (PARTITION BY tenant_id,report_date,channel_id,sku_id,region_id
                                      ORDER BY report_version DESC) AS rn FROM channel_product_daily
                   WHERE report_date >= $1::date AND report_date < $2::date
                     AND available_at <= $3::timestamptz)
              SELECT channel_id::text AS key,sum(paid_amount_cent) AS cents
              FROM ranked WHERE rn=1 GROUP BY channel_id
            """,
                    (start, end, FINAL_K),
                )
                .fetchall()
            )
            expect(
                {r["group_key"]: r["value"] for r in rows},
                {r["key"]: r["cents"] for r in oracle},
                "weekly by channel",
            )
            for row in rows:
                expect(row["status"], "OK", "weekly completeness")
                channel_deltas[row["group_key"]] += sign * row["value"]
        leader = min(channel_deltas, key=lambda c: (-abs(channel_deltas[c]), int(c)))
        product_deltas = defaultdict(Decimal)
        for start, end, sign in windows:
            for row in query(code, start, end, group="spu", filters={"channel": int(leader)}):
                product_deltas[row["group_key"]] += sign * row["value"]
        expect(sum(product_deltas.values()), channel_deltas[leader], "all SPU deltas reconcile")
        print(
            f"8 metric contracts passed, 2 restricted source identities; leading channel {leader}, "
            f"delta {channel_deltas[leader]} CNY fen; POS 453 rows / 44,597.20 CNY."
        )


if __name__ == "__main__":
    verify()
