"""Independently check the distributed gold, the restricted role and published metric bindings."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import psycopg
from compass_agent.data_connections.connections import DataConnections
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.schemas import TenantScope
from compass_agent.semantic.execution import calculate, choose_connection
from compass_agent.settings import Settings
from freeze import COLUMNS, RELEASE, ROOT, K, schema
from materials import TENANT, WORKSPACE
from publish_metrics import TRIALS
from sqlalchemy import create_engine

GOLD = {
    "G1": [
        "2025-07-01|35296820|5923215|29373605",
        "2025-08-01|7368475|5387405|1981070",
        "2025-09-01|20455095|2371075|18084020",
    ],
    "G2-G4": [
        "DOC13392|400|2025-09-06 00:00:00+08|2025-09-04 00:00:00+08|"
        "2025-09-16 00:00:00+08|2025-09-16 00:00:00+08|400",
        "2025-09-14|80",
        "故障窗|5|5|80|68|0|0|68|68|0|0|100.00|80|0",
        "实收后|5|5|79|57|52|1867320|57|0|57|0|0.00|80|0",
    ],
    "G5": ["2025-10-08|407|1|406|2025-10-09 00:00:00+08"],
    "B": ["2025-07-01|22|899400", "2025-08-01|16|436060", "2025-09-01|33|1035615"],
    "C": ["1|1408875", "5|8421280"],
}
ALIASES = ["成交额", "扣退款的成交额", "商品日报实付", "上个日结可用件数", "锁不住的比例"]
ACTOR = "99500000-0000-4000-8000-000000000003"


def check_database(root):
    conn = json.loads((root / "connection.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    gold = ROOT / "mai-specs/973-lesson-23-agentic-search/spec/retail-gold.sql"
    output = subprocess.check_output(
        [
            "psql",
            "-X",
            "-qAt",
            "-h",
            "/tmp",
            "-p",
            str(conn["port"]),
            "-d",
            conn["database_name"],
            "-f",
            str(gold),
        ],
        text=True,
    )
    lines = output.splitlines()
    for cases in GOLD.values():
        assert all(row in lines for row in cases), (cases, lines)
    with psycopg.connect(
        host=conn["host"],
        port=conn["port"],
        dbname=conn["database_name"],
        user=conn["role"],
        password=conn["password"],
    ) as db:
        role, readonly, can_teach = db.execute("""
            SELECT session_user,current_setting('transaction_read_only'),
                   has_schema_privilege('retail_course','usage')""").fetchone()
        assert role == conn["role"] and readonly == "on" and not can_teach
        relation_names = {
            (row[0], row[1])
            for row in db.execute(
                """SELECT table_schema,table_name FROM information_schema.tables
                   WHERE table_schema IN ('core','ops','retail_course')"""
            )
        }
        assert relation_names == {(schema(t), t) for t in COLUMNS}
        hashes = {}
        for table, cols in COLUMNS.items():
            fields = cols.split()
            actual = db.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position""",
                (schema(table), table),
            ).fetchall()
            assert [row[0] for row in actual] == fields, table
            exclude = ["tenant_id<>101"]
            if "region_id" in fields:
                exclude.append("region_id<>1")
            times = [f + " > %(k)s" for f in ("available_at", "observed_at") if f in fields]
            stmt = (
                f"SELECT EXISTS(SELECT FROM {schema(table)}.{table} WHERE "
                + " OR ".join(exclude + times)
                + ")"
            )
            assert not db.execute(stmt, {"k": K}).fetchone()[0], table
            expected_hash = manifest.get("relationSha256", {}).get(table)
            if expected_hash:
                primary = db.execute(
                    """SELECT a.attname FROM pg_index i,
                        unnest(i.indkey) WITH ORDINALITY AS cols(num,pos)
                        JOIN pg_attribute a ON a.attnum=cols.num
                       WHERE i.indrelid=%s::regclass AND i.indisprimary
                         AND a.attrelid=i.indrelid ORDER BY cols.pos""",
                    (schema(table) + "." + table,),
                ).fetchall()
                keys = [row[0] for row in primary]
                assert keys and set(keys) <= set(fields), table
                digest = hashlib.sha256()
                with db.cursor().copy(
                    f"COPY (SELECT * FROM {schema(table)}.{table} ORDER BY {','.join(keys)}) "
                    "TO STDOUT (FORMAT BINARY)"
                ) as rows:
                    for chunk in rows:
                        digest.update(chunk)
                hashes[table] = digest.hexdigest()
                assert hashes[table] == expected_hash, table
        if manifest.get("dataSha256"):
            assert len(hashes) == len(COLUMNS)
            assert (
                hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
                == (manifest["dataSha256"])
            )
        if manifest.get("sliceVersion", 1) >= 2:
            assert not db.execute(
                """SELECT EXISTS(SELECT FROM core.inventory_document i
                    WHERE i.from_site_id IS NOT NULL AND NOT EXISTS(
                        SELECT FROM core.site s
                        WHERE s.tenant_id=i.tenant_id AND s.site_id=i.from_site_id))"""
            ).fetchone()[0]
        assert (
            db.execute("SELECT min(snapshot_date) FROM core.inventory_snapshot")
            .fetchone()[0]
            .isoformat()
            == "2025-10-01"
        )
    return {"goldGroups": list(GOLD), "restrictedRelations": len(COLUMNS)}


async def check_metrics(dictionaries=None):
    settings = Settings()
    engine = create_engine(settings.database_url)
    tenant_scope = TenantScope(kind="tenant", tenant_id=UUID(TENANT), workspace_id=UUID(WORKSPACE))
    connections = DataConnections(engine, settings).bindings_for_run(tenant_scope)
    owner = OwnerKey(UUID(ACTOR), UUID(TENANT), UUID(WORKSPACE))
    control = RunRegistry().register(owner, lifetime_s=90)
    identity = dict(
        actor_user_id=ACTOR, tenant_id=TENANT, workspace_id=WORKSPACE, run_id=str(control.id)
    )
    checked = []
    with httpx.Client(
        base_url=os.environ["WEB_PUBLIC_ORIGIN"],
        timeout=50,
        trust_env=False,
        headers={"Authorization": "Bearer " + os.environ["COMPASS_AGENT_SERVICE_TOKEN"]},
    ) as client:
        pins = []

        def api(op, args):
            payload = {**identity, "args": args}
            if pins:
                payload["pins"] = pins
            response = client.post("/api/internal/semantic/" + op, json=payload)
            response.raise_for_status()
            return response.json()

        pins = api("manifest", {})["releases"]
        assert pins
        if dictionaries is not None:
            for alias, code, entry in [
                ("华东", "dimension.region", "华东 = 1"),
                ("小程序", "dimension.channel", "微信小程序 = 4"),
                ("苏州", "dimension.site", "苏州华东配送中心 = 7"),
                ("商品浏览", "dimension.behavior_event_type", "商品浏览 = PRODUCT_VIEW"),
                ("采购单", "dimension.inventory_document_type", "采购单 = PURCHASE"),
                ("实际收货", "dimension.inventory_movement_type", "实际收货 = RECEIPT"),
            ]:
                results = api("search", {"query": alias, "kinds": ["entity"]})["items"]
                hit = next((i for i in results[:3] if i["code"] == code), None)
                assert hit, (alias, code)
                ref = {k: hit[k] for k in ("asset_id", "version_id")}
                definition = api("definition", ref)
                assert entry in definition["summary"], (entry, definition["summary"])
                dictionaries.append(dict(alias=alias, code=code, entry=entry, **ref))
        for alias, (code, (start, end, filters, expected)) in zip(
            ALIASES, TRIALS.items(), strict=True
        ):
            entries = api("search", {"query": alias, "kinds": ["metric"]})["items"]
            assert code in [e["code"] for e in entries[:3]], (alias, entries)
            resolved = api("resolve", {"mention": alias, "dimensions": []})
            assert resolved.get("metric", {}).get("code") == code, (alias, resolved)
            metric = resolved["metric"]
            ref = dict(
                asset_id=metric["asset_id"],
                version_id=metric["version_id"],
                binding_id=resolved["binding_id"],
            )
            plan = api("binding", ref)["binding"]
            conn = choose_connection(connections, plan)

            async def run(
                start_date,
                end_date,
                selected=filters,
                cutoff=None,
                source=conn,
                approved=plan,
                **extra,
            ):
                rows, scope = await calculate(
                    source,
                    approved,
                    dict(
                        start=start_date,
                        end=end_date,
                        filters=selected,
                        as_of=cutoff or K.isoformat(),
                        **extra,
                    ),
                    control,
                )
                assert scope["data_release"] == RELEASE and scope["region_ids"] == [1]
                return rows

            rows = await run(start, end)
            assert (
                len(rows) == 1
                and rows[0]["status"] == "OK"
                and Decimal(str(rows[0]["value"])) == Decimal(expected)
            ), (alias, rows)
            checks = []
            if code in ("sales.paid_gmv", "sales.net_gmv"):
                monthly = await run("2025-07-01", end, group_by="month")
                values = (
                    ["35296820", "7368475", "20455095"]
                    if code == "sales.paid_gmv"
                    else ["29373605", "1981070", "18084020"]
                )
                expected_months = list(zip(["2025-07", "2025-08", "2025-09"], values, strict=True))
                assert [(r["group_key"], r["value"]) for r in monthly] == expected_months
                assert all(r["status"] == "OK" for r in monthly)
                checks.append({"months": dict(expected_months)})
            if code.startswith("inventory."):
                rows_missing = await run("2025-09-10", "2025-09-11")
                assert rows_missing[0]["value"] is None and rows_missing[0]["status"] != "OK"
                checks.append(rows_missing[0]["status"])
            if code.startswith("fulfillment."):
                fault = (await run("2025-09-07", "2025-09-12"))[0]
                daily = await run("2025-09-07", "2025-09-12", group_by="day")
                assert fault["detail"]["denominator"] == fault["detail"]["numerator"] == 68
                assert (
                    rows[0]["detail"]["denominator"] == 57 and rows[0]["detail"]["numerator"] == 0
                )
                assert sum(r["detail"]["denominator"] for r in daily) == 68
                no_sample = (await run(start, end, {**filters, "sku": 999999}))[0]
                delayed = (await run("2025-10-09", "2025-10-10"))[0]
                assert (no_sample["status"], no_sample["value"]) == ("NO_FACTS", None)
                assert (delayed["status"], delayed["value"]) == ("SOURCE_DELAYED", None)
                unknown = (await run(start, end, cutoff="2022-12-31T23:59:59+08:00"))[0]
                assert (unknown["status"], unknown["value"]) == ("COVERAGE_UNKNOWN", None)
                checks.extend(["68/68", "0/57", "NO_FACTS", "SOURCE_DELAYED", "COVERAGE_UNKNOWN"])
            checked.append(
                dict(
                    alias=alias,
                    code=code,
                    **ref,
                    targetHash=plan["target_hash"],
                    value=rows[0]["value"],
                    status=rows[0]["status"],
                    additionalChecks=checks,
                )
            )
    engine.dispose()
    return checked


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("frozen", type=Path)
    args = parser.parse_args()
    dictionaries = []
    print(
        json.dumps(
            {
                **check_database(args.frozen),
                "metrics": asyncio.run(check_metrics(dictionaries)),
                "dictionaries": dictionaries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
