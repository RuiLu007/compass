"""Exercise document HTTP routes against the classroom package and real workspace memberships."""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from materials import publish

PROBES = [
    ("history", "H-0912", "待办 3", "华东九月周会挂账待办", "上回压着的跑鞋款由谁跟？"),
    ("knowledge", "K-17", "§1", "十月自营渠道复投额度", "这类留着的钱现在能动几成？"),
    ("knowledge", "K-08", "§2", "采购延期恢复验收窗", "调来的算到货，还是等采购收货再数天数？"),
    ("external", "E-1009", "本期排期", "十月跑步专场当期排期", "媒介方这次最小一档多少钱？"),
    ("external", "E-CAL-2025", "十月调休", "2025 年十月调休", "十一长假后的头个周六总部上班吗？"),
    ("knowledge", "K-31", "附批", "九月城西体验店返补门槛", "杭州这家试点店有单批吗？"),
]


def exercise(url, connection=None):
    migration = os.environ["WEB_TEST_MIGRATION_DATABASE_URL"]
    if urlsplit(url).hostname not in {"127.0.0.1", "localhost"} or not urlsplit(
        migration
    ).path.startswith("/compass_web_test_"):
        raise ValueError("a local Web instance bound to a dedicated Web test database is required")
    root = Path(os.environ["COMPASS_DOCUMENTS_ROOT"])
    tenant, workspace, group, actor, member, grant = (str(uuid4()) for _ in range(6))

    def release(**kwargs):
        return publish(root, tenant=tenant, workspace=workspace, group=group, **kwargs)

    release()
    with psycopg.connect(migration, autocommit=True) as db:
        db.execute(
            "INSERT INTO web.tenant(id,name,timezone) VALUES(%s,%s,'Asia/Shanghai')",
            (tenant, "资料验收 " + tenant[:8]),
        )
        for key in (workspace, group):
            db.execute(
                "INSERT INTO web.workspace(id,tenant_id,name) VALUES(%s,%s,%s)",
                (key, tenant, "资料组 " + key[:8]),
            )
        db.execute(
            "INSERT INTO web.app_user(id,login_name,password_hash,display_name) "
            "VALUES(%s,%s,'test-only','资料验收')",
            (actor, "d." + actor),
        )
        db.execute(
            "INSERT INTO web.tenant_member(id,tenant_id,user_id,role_key) VALUES(%s,%s,%s,'admin')",
            (member, tenant, actor),
        )
        db.execute(
            "INSERT INTO web.workspace_member(id,tenant_id,workspace_id,tenant_member_id,role_key) "
            "VALUES(%s,%s,%s,%s,'analyst')",
            (str(uuid4()), tenant, workspace, member),
        )
        db.execute(
            "INSERT INTO web.workspace_member(id,tenant_id,workspace_id,tenant_member_id,role_key) "
            "VALUES(%s,%s,%s,%s,'analyst')",
            (grant, tenant, group, member),
        )
        runtime = {}
        if connection:
            import asyncio

            from runtime_sources_acceptance import exercise_runtime

            runtime = asyncio.run(
                exercise_runtime(
                    db, tenant, workspace, group, member, actor, grant, release, connection, url
                )
            )
        run = str(uuid4())
        payload = dict(actor_user_id=actor, tenant_id=tenant, workspace_id=workspace)
        with httpx.Client(
            base_url=url,
            headers={"Authorization": "Bearer " + os.environ["COMPASS_AGENT_SERVICE_TOKEN"]},
            trust_env=False,
            timeout=40,
        ) as client:

            def call(op, args=None, *, run_id=run):
                response = client.post(
                    "/api/internal/documents/" + op,
                    json={**payload, "run_id": run_id, "args": args or {}},
                )
                response.raise_for_status()
                assert "no-store" in response.headers["Cache-Control"]
                return response.json()

            with ThreadPoolExecutor(max_workers=4) as pool:
                pins = list(pool.map(lambda _: call("pin"), range(4)))
            pin = pins[0]
            assert pins == [pin] * 4 and pin["scenePack"] == "retail"
            passed = 0
            refs = {}
            for kind, code, heading, *queries in PROBES:
                for query in queries:
                    rows = call("search", {"kind": kind, "query": query})["sections"]
                    assert len(rows) <= 3
                    matched = [
                        r
                        for r in rows
                        if r["docId"] == code
                        and (
                            r["heading"].startswith(heading)
                            or (code == "K-17" and r["heading"].startswith("§2"))
                        )
                    ]
                    assert matched, (query, [(r["docId"], r["heading"]) for r in rows])
                    ref = matched[0]["sourceRef"]
                    original = call("read", {"sourceRef": ref})["sections"][0]
                    assert original["sectionHash"] == ref["sectionHash"] and original["text"]
                    if code == "K-17":
                        assert original["version"] == "2"
                        offset = 1 if original["heading"].startswith("§1") else -1
                        adjacent = call("read", {"sourceRef": ref, "offset": offset})["sections"][0]
                        assert {original["heading"][:2], adjacent["heading"][:2]} == {"§1", "§2"}
                    refs[code] = ref
                    passed += 1

            historical = call(
                "search", {"query": "K-17 额度", "historical": True, "businessDate": "2025-09-15"}
            )
            assert historical["sections"] and {r["version"] for r in historical["sections"]} == {
                "1"
            }
            assert call("verify", {"refs": list(refs.values())})["allowed"]
            offer = refs["E-1009"]
            other = str(uuid4())
            release(variant="price")
            assert (
                call("pin") == pin
                and call("pin", run_id=other)["collectionDigest"] != pin["collectionDigest"]
            )
            assert "6,000" in call("read", {"sourceRef": offer})["sections"][0]["text"]
            assert not call("read", {"sourceRef": offer}, run_id=other)["sections"]
            new = call("search", {"kind": "external", "query": "E-1009 档位"}, run_id=other)[
                "sections"
            ]
            new_ref = next(r["sourceRef"] for r in new if r["heading"] == "本期排期")
            assert (
                "9,000" in call("read", {"sourceRef": new_ref}, run_id=other)["sections"][0]["text"]
            )
            altered = {**offer, "sectionHash": "f" * 64}
            assert not call("verify", {"refs": [altered]})["allowed"]
            release(withdraw=["E-CAL-2025"])
            assert not call("verify", {"refs": [refs["E-CAL-2025"]]})["allowed"]
            assert not call("read", {"sourceRef": refs["E-CAL-2025"]})["sections"]
            assert call("verify", {"refs": [offer]})["allowed"]
            assert call("read", {"sourceRef": offer})["sections"]
            release()
            db.execute("DELETE FROM web.workspace_member WHERE id=%s", (grant,))
            assert not call("verify", {"refs": [offer]})["allowed"]
            assert not call("read", {"sourceRef": offer})["sections"]
            assert all(
                r["docId"] != "E-1009"
                for r in call("search", {"kind": "external", "query": "E-1009 档位"})["sections"]
            )
            assert call("verify", {"refs": [refs["E-CAL-2025"]]})["allowed"]
            release(exclude=["knowledge"])
            assert not call("verify", {"refs": [refs["K-17"]]})["allowed"]
            assert not call("search", {"kind": "knowledge", "query": "额度"})["sections"]
            print(
                json.dumps(
                    {
                        "passedProbes": passed,
                        **runtime,
                        "historicalVersion": True,
                        "pinIdempotency": True,
                        "immutableEdition": True,
                        "newQuote": True,
                        "crossRunDenied": True,
                        "tamperedLocatorDenied": True,
                        "audienceRevocation": True,
                        "publicCalendarRetained": True,
                        "privateQuoteRetainedWithoutCalendar": True,
                        "sourceDisabled": True,
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--web", required=True)
    parser.add_argument("--runtime-connection", type=Path)
    args = parser.parse_args()
    exercise(args.web, args.runtime_connection)
