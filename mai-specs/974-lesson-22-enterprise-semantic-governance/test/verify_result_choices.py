"""Exercise retained temporary results and natural-language draft creation across Chat turns."""

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from prepare_effect_accounts import install
from run_chat_cohort import DONE, Actor

FIRST = (
    "经营问答：2025-10-01（含）至 2025-10-08（不含）成功支付使用了几种支付方式？"
    "这是临时口径，请用工作区只读连接的 retail_course.payment，按 attempted_at 划期，"
    "计算 status 为 SUCCEEDED 时 COUNT(DISTINCT method)，返回种数。"
)
SECOND = (
    "请再做另一项临时计算。相同日期和时间条件下，统计 retail_course.payment 中"
    "每种 method 的成功支付笔数，按支付方式逐行返回并保留这次的查询回执。"
)
SAVE = "我想把刚才同一会话的一项临时查询保存为个人指标草稿，请让我从现有结果中选择，确认后再保存。"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument("personas", type=Path)
    parser.add_argument("provider", type=UUID)
    scenario = parser.add_mutually_exclusive_group()
    scenario.add_argument(
        "--single", action="store_true", help="save the only prior result by natural reference"
    )
    scenario.add_argument(
        "--prior-runs",
        nargs=2,
        type=UUID,
        metavar=("SCALAR_RUN", "GROUPED_RUN"),
        help="reuse two unexpired results from one test owner after a restart",
    )
    args = parser.parse_args()
    migration = os.environ["WEB_TEST_MIGRATION_DATABASE_URL"]
    agent_db = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    agent_migration = os.environ["AGENT_MIGRATION_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://"
    )
    if urlsplit(args.origin).hostname not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("a local test origin is required")
    if not urlsplit(migration).path.startswith("/compass_web_test_"):
        parser.error("a dedicated Web test database is required")
    if not urlsplit(agent_db).path.startswith("/compass_agent_test_"):
        parser.error("the receipt-expiry fixture requires a dedicated Agent test database")
    if urlsplit(agent_migration).path != urlsplit(agent_db).path:
        parser.error("the Agent fixture migration credential must point at the same test database")
    session = None
    if args.prior_runs:
        with psycopg.connect(agent_db) as tx:
            tx.execute("SET TRANSACTION READ ONLY")
            rows = tx.execute(
                """SELECT s.id,s.owner_user_id,s.tenant_id,s.workspace_id FROM agent_run r
                   JOIN agent_session s ON s.id=r.session_id WHERE r.id=ANY(%s)
                   AND r.status='completed'
                   GROUP BY s.id HAVING count(*)=2""",
                (args.prior_runs,),
            ).fetchall()
            assert len(rows) == 1, "original runs must share an owner and a session"
            sid, actor, tenant, workspace = rows[0]
            session = str(sid)
        with psycopg.connect(migration) as tx:
            tx.execute("SET TRANSACTION READ ONLY")
            handle = tx.execute(
                "SELECT login_name FROM web.app_user WHERE id=%s", (actor,)
            ).fetchone()[0]
        assert handle.startswith("l22.opendraft."), "only this test's synthetic owners are eligible"
        who = {
            "handle": handle,
            "actor": str(actor),
            "tenantId": str(tenant),
            "workspaceId": str(workspace),
        }
    else:
        with psycopg.connect(migration) as tx:
            tx.execute("SET LOCAL ROLE compass_web_owner")
            who = install(
                tx, "opendraft." + uuid4().hex[:12], 10, 20, ["sales", "finance"], False, True
            )
    owner = Actor(args.origin, who)
    peer = Actor(args.origin, json.loads(args.personas.read_text())["101.sales"])

    def send(question):
        nonlocal session
        command = {
            **owner.scope,
            "question": question,
            "requestId": str(uuid4()),
            "selection": {"mode": "fixed", "provider_id": str(args.provider)},
        }
        if session:
            command["sessionId"] = session
        r = owner.http.post("/api/web/v1/analysis/sessions/submit", json=command)
        r.raise_for_status()
        receipt = r.json()
        if session:
            assert receipt["sessionId"] == session
        session = receipt["sessionId"]
        return receipt

    def at(receipt, want, *, after=None):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            status = owner.status(receipt)
            if status in DONE:
                state, packets = owner.messages(receipt)
                if state["status"] == status:
                    if status == "waiting":
                        if want == "completed" and after:
                            time.sleep(2)
                            continue
                        # A resumed run retains the preceding wait event in its public history.
                        pending = [
                            p
                            for p in reversed(packets)
                            if p["event"]["payload"].get("kind") == "approval"
                            and p["event"]["payload"]["value"].get("status") == "pending"
                            and p["event"]["payload"]["value"]["waitId"] != after
                        ]
                        if pending and want == "waiting":
                            card = owner.wait(receipt, pending)
                            assert card
                            return card
                        if not pending and after:
                            time.sleep(2)
                            continue
                    if status == want:
                        return packets
                    raise AssertionError((want, status, state.get("reasonCode"), receipt["runId"]))
            time.sleep(2)
        raise TimeoutError(receipt["runId"])

    def path(receipt):
        return "/api/web/v1/analysis/runs/" + receipt["runId"]

    def results(receipt):
        r = owner.http.get(path(receipt) + "/semantic-results", params=owner.scope)
        r.raise_for_status()
        return r.json()["items"]

    def expire(receipt, refs):
        with psycopg.connect(agent_migration) as tx:
            tx.execute("SET LOCAL ROLE compass_agent_owner")
            changed = tx.execute(
                """UPDATE semantic_metric_offer SET expires_at=clock_timestamp()-interval '1 second'
                   WHERE offer_id=ANY(%s) AND owner_id=%s AND run_id=%s AND kind='open'""",
                ([UUID(ref) for ref in refs], who["actor"], receipt["runId"]),
            )
            assert changed.rowcount == len(refs)

    def choose(receipt, card, *, repeat=False, **fields):
        command = {
            **owner.scope,
            **{k: card[k] for k in ("waitId", "proposalId", "proposalRevision", "proposalDigest")},
            "expectedVersion": card["runVersion"],
            "requestId": str(uuid4()),
            "decisionId": str(uuid4()),
            "decision": "approve",
            **fields,
        }
        for _ in range(2 if repeat else 1):
            r = owner.http.post(path(receipt) + "/decisions", json=command)
            r.raise_for_status()

    def save_preview(follow, draft, original, ref, name):
        assert draft["semanticKind"] == "draft", draft["semanticKind"]
        assert draft["fields"]["name"] and draft["fields"]["formula"]
        code = "qa.cross_turn_" + uuid4().hex[:10]
        choose(
            follow, draft, repeat=True, draftEdits={"name": name, "code": code, "domain": "销售"}
        )
        at(follow, "completed", after=draft["waitId"])
        selected = next(r for r in results(original) if r["result_ref"] == ref)
        assert selected["status"] == "saved" and selected["draft_id"]
        detail = owner.http.get("/api/semantic/assets/" + selected["draft_id"], params=owner.scope)
        detail.raise_for_status()
        asset = detail.json()
        assert (asset["name"], asset["code"], asset["state"]) == (name, code, "draft")
        assert asset["source_id"] and asset["owner_scope_id"] == asset["usage_scope_id"]
        mine = owner.http.get("/api/semantic/assets", params={**owner.scope, "q": code})
        mine.raise_for_status()
        assert [m["asset_id"] for m in mine.json()["items"]] == [selected["draft_id"]]

    try:
        if args.prior_runs:
            first, second = ({"runId": str(r), "sessionId": session} for r in args.prior_runs)
        else:
            first = send(FIRST)
            at(first, "completed")
            if args.single:
                scalar = results(first)
                aggregates = [item for item in scalar if item["reusable"]]
                assert aggregates, "the prior answer must have retained an actual aggregate"
                target = next(
                    (
                        item
                        for item in aggregates
                        if "distinct" in item["proposal"]["fields"]["formula"].lower()
                    ),
                    aggregates[-1],
                )
                # A model can inspect tables before calculating. Retain the real scalar
                # receipt as the sole unexpired item for this independent test scenario.
                expire(first, [item["result_ref"] for item in scalar if item != target])
                scalar = results(first)
                assert len(scalar) == 1 and scalar[0]["status"] == "active"
                follow = send("请把刚才那项支付方式临时统计存成我的指标，先让我编辑草稿说明。")
                draft = at(follow, "waiting")
                save_preview(follow, draft, first, scalar[0]["result_ref"], "成功支付方式统计")
                print("唯一的原结果直接打开草稿；重复确认仅保存一项。")
                return
            second = send(SECOND)
            at(second, "completed")
        scalar = results(first)
        assert scalar, "original scalar result is missing"
        table = results(second)
        assert table, "original grouped result is missing"
        wanted = next(r for r in table if "method" in r["columns"])
        denied = peer.http.get(path(second) + "/semantic-results", params=peer.scope)
        assert denied.status_code in (403, 404), denied.status_code
        first_row = next(
            r for r in scalar if r["reusable"] or (args.prior_runs and r["status"] == "dismissed")
        )
        first_ref = first_row["result_ref"]
        first_path = path(first) + "/semantic-results"
        for action, state in (("confirm", "confirmed"), ("dismiss", "dismissed")):
            if args.prior_runs and first_row["status"] == "dismissed":
                break
            response = owner.http.post(
                first_path, json={**owner.scope, "result_ref": first_ref, "action": action}
            )
            response.raise_for_status()
            assert response.json()["status"] == state
            if action == "confirm":
                assert response.json()["reusable"], "new verified aggregate should invite its owner"
        assert (
            next(r for r in results(first) if r["result_ref"] == first_ref)["status"] == "dismissed"
        )
        assert not any(r["draft_id"] for r in results(first))
        print("two original results retained, other user denied, dismissal persisted", flush=True)
        follow = send(SAVE)
        card = at(follow, "waiting")
        assert card["semanticKind"] == "result", card["semanticKind"]
        option_ids = {o["id"] for o in card["options"]}
        assert {first_ref, wanted["result_ref"]} <= option_ids
        choose(follow, card, choiceId=wanted["result_ref"])
        print("owner selected the intended original result", flush=True)
        draft = at(follow, "waiting", after=card["waitId"])
        save_preview(follow, draft, second, wanted["result_ref"], "支付方式成功笔数")
        expire(first, [first_ref])
        assert first_ref not in {r["result_ref"] for r in results(first)}
        expired = owner.http.post(
            first_path, json={**owner.scope, "result_ref": first_ref, "action": "confirm"}
        )
        assert expired.status_code in (403, 404), expired.status_code
        print(
            "selected result reviewed, edited, and saved privately; expired result unavailable",
            flush=True,
        )
    finally:
        owner.http.close()
        peer.http.close()


if __name__ == "__main__":
    main()
