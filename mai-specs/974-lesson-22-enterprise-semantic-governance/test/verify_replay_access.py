"""Verify that removing a department membership protects prior Chat results and an open choice."""

import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from prepare_effect_accounts import install
from run_chat_cohort import Actor, close_captured_wait, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument("personas", type=Path)
    parser.add_argument("provider", type=UUID)
    parser.add_argument(
        "--relative-only", action="store_true", help="ask a dual-department user about this month"
    )
    args = parser.parse_args()
    if urlsplit(args.origin).hostname not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("local acceptance server required")
    dsn = os.environ["WEB_TEST_MIGRATION_DATABASE_URL"]
    if not urlsplit(dsn).path.startswith("/compass_web_test_"):
        parser.error("dedicated Web test database required")
    if args.relative_only:
        roster = json.loads(args.personas.read_text())
        actor = Actor(args.origin, roster["101.sales_finance"])
        try:
            with TemporaryDirectory() as storage:
                path = Path(storage)
                case = {
                    "id": "relative",
                    "question": "本月销售额是多少？",
                    "persona": "101.sales_finance",
                }
                assert run(case, actor, args.provider, path, 360) == "waiting"
                snapshot = json.loads((path / "relative.json").read_text())
                card = snapshot["wait"]
                assert card["semanticKind"] == "choice"
                assert {o["name"] for o in card["options"]} == {"支付 GMV", "确认收入"}
                close_captured_wait(actor, path / "relative.json")
                return {"relative_question": case["question"], "offered": 2, "owner_declined": True}
        finally:
            actor.http.close()
    handle = "retention." + uuid4().hex[:12]
    with psycopg.connect(dsn) as c:
        c.execute("SET LOCAL ROLE compass_web_owner")
        ephemeral = install(c, handle, 10, 20, ["sales", "finance"], False, True)
        member = c.execute(
            "SELECT id FROM web.tenant_member WHERE tenant_id=%s AND user_id=%s",
            (ephemeral["tenantId"], ephemeral["actor"]),
        ).fetchone()[0]
        department = c.execute(
            """SELECT scope_id FROM web.semantic_scopes
               WHERE tenant_id=%s AND department_key='sales'""",
            (ephemeral["tenantId"],),
        ).fetchone()[0]
    reader = Actor(args.origin, ephemeral)
    operator = Actor(args.origin, json.loads(args.personas.read_text())["operator"])

    def membership(active):
        response = operator.http.post(
            "/api/semantic/grants",
            params=operator.scope,
            json={
                "request_key": str(uuid4()),
                "scope_id": str(department),
                "member_id": str(member),
                "capability": "member",
                "active": active,
            },
        )
        response.raise_for_status()

    def submit(directory, case_id, question, expected):
        case = {"id": case_id, "question": question, "persona": handle}
        actual = run(case, reader, args.provider, directory, 420)
        if actual != expected:
            raise AssertionError(f"{case_id} expected {expected}, observed {actual}")
        return json.loads((directory / (case_id + ".json")).read_text())

    def assistant(packets):
        return next(
            p["event"]["payload"]["content"]["text"]
            for p in packets
            if p["event"]["type"] == "message.upsert"
            and p["event"]["payload"].get("role") == "assistant"
        )

    try:
        with TemporaryDirectory() as storage:
            directory = Path(storage)
            paid = submit(
                directory,
                "paid",
                "经营问答：2025-11-17（含）至2025-11-24（不含）支付 GMV 合计多少？",
                "completed",
            )
            with psycopg.connect(os.environ["SEMANTIC_RETAIL_101_URL"]) as retail:
                retail.execute("SET TRANSACTION READ ONLY")
                cents = retail.execute(
                    """SELECT sum(amount_cent) FROM retail_course.payment WHERE status='SUCCEEDED'
                       AND attempted_at >= '2025-11-17T00:00:00+08:00'
                       AND attempted_at < '2025-11-24T00:00:00+08:00'"""
                ).fetchone()[0]
            visible = assistant(paid["packets"]).replace(",", "").replace("，", "")
            assert str(cents) in visible or f"{cents / 100:.2f}" in visible, "paid amount differs"
            membership(False)
            _, packets = reader.messages(paid["receipt"])
            assert "来源当前不可读取" in assistant(packets), "historical answer remains visible"
            membership(True)
            _, packets = reader.messages(paid["receipt"])
            assert assistant(packets).replace(",", "").replace("，", "") == visible
            choice = submit(
                directory,
                "choice",
                "经营问答：2025-11-17（含）至2025-11-24（不含）的销售额是多少？",
                "waiting",
            )
            wait = choice["wait"]
            assert wait["semanticKind"] == "choice" and len(wait["options"]) == 2
            lost = next(o["id"] for o in wait["options"] if "支付" in o["name"])
            membership(False)
            api = "/api/web/v1/analysis/runs/" + choice["receipt"]["runId"]
            refreshed = reader.http.get(api + "/waits/" + wait["waitId"], params=reader.scope)
            if refreshed.status_code == 200:
                projected = refreshed.json()
                assert lost not in {v["id"] for v in projected.get("options") or []}
            else:
                assert refreshed.status_code in {403, 404, 409}
            decision = reader.http.post(
                api + "/decisions",
                json={
                    **reader.scope,
                    **{
                        k: wait[k]
                        for k in ("waitId", "proposalId", "proposalRevision", "proposalDigest")
                    },
                    "expectedVersion": wait["runVersion"],
                    "requestId": str(uuid4()),
                    "decisionId": str(uuid4()),
                    "decision": "approve",
                    "choiceId": lost,
                },
            )
            assert decision.status_code in {403, 404, 409}, decision.status_code
            return {
                "historical_answer_before_removal": "paid total matched source",
                "historical_answer_after_removal": "restricted",
                "restored_membership": "original answer readable",
                "changed_choice_view": refreshed.status_code,
                "stale_decision": decision.status_code,
            }
    finally:
        reader.http.close()
        operator.http.close()


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
