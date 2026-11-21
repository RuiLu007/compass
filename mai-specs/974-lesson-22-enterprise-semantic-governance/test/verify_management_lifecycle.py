"""Exercise source discovery, AI suggestions, independent trials, approval and release recovery."""

import argparse
import asyncio
import json
import os
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from compass_agent.models.errors import ModelError
from run_chat_cohort import Actor
from running_registry import invoke, running_registry
from score_sql_bindings import ORDER, expected
from verify_retail_content import FINAL_K


def main(active_runs):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Isolated Web instance with a disposable release history")
    parser.add_argument("roster", type=Path)
    parser.add_argument(
        "--verify-live-pin", action="store_true", help="Claim two isolated Agent Runs"
    )
    args = parser.parse_args()
    if urlsplit(args.origin).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error(
            "Use a dedicated local acceptance instance for release rollback and revocation."
        )
    roster = json.loads(args.roster.read_text())
    author, expert, data, reader = (
        Actor(args.origin, roster[k]) for k in ("operator", "expert", "steward", "101.sales")
    )
    outside = Actor(args.origin, roster["101.finance"])

    def api(actor, path, body=None, *, failure=None, **query):
        if body is None:
            response = actor.http.get("/api/semantic/" + path, params={**actor.scope, **query})
        else:
            response = actor.http.post("/api/semantic/" + path, params=actor.scope, json=body)
        if failure:
            assert response.status_code == failure, (
                path,
                response.status_code,
                response.text[:300],
            )
        else:
            assert response.is_success, (path, response.status_code, response.text[:300])
        return response.json()

    def post(actor, path, **fields):
        return api(actor, path, {"request_key": str(uuid4()), **fields})

    scopes = api(author, "scopes")["items"]
    own = next(s["id"] for s in scopes if s["kind"] == "tenant" and s["manage"])
    key = uuid4().hex[:10]
    catalog = api(author, "assets")["items"]
    original = next(m for m in catalog if m["code"] == ORDER and m["binding_id"])
    source = post(
        author,
        "sources",
        owner_scope_id=own,
        kind="document",
        locator="fulfillment/" + key,
        version="1",
        priority="P1",
        responsible="仓配经营组",
        excerpt="履约下单量以订单为粒度，按创建时间计数，每个订单统计一次，使用下单业务时区。",
    )
    suggestions = api(
        author, "suggestions", {"source_refs": [source["source_id"]], "owner_scope_id": own}
    )
    idea = next(
        item for item in suggestions["candidates"] if item["source_ref"] == source["source_id"]
    )
    assert idea["name"] and idea["formula"] and 0 <= idea["confidence"] <= 1
    draft = dict(
        owner_scope_id=own,
        usage_scope_id=own,
        code="acceptance.orders." + key,
        domain="履约",
        kind="metric",
        responsible="履约负责人",
        grain=original["grain"],
        unit=original["unit"],
        name=idea["name"],
        summary=idea["summary"],
        formula=idea["formula"],
        source_id=source["source_id"],
        source_position=idea["position"],
        event_time="created_at",
        timezone="Asia/Shanghai",
        aggregation="count",
    )
    v = post(author, "drafts", **draft)
    print("AI 建议已采用，候选保留原来源与位置。", flush=True)
    p = {
        "start": "2025-01-01",
        "end": "2025-01-08",
        "as_of": FINAL_K,
        "filters": {},
        "group_by": "total",
    }
    with psycopg.connect(os.environ["SEMANTIC_RETAIL_101_URL"]) as db:
        db.execute("SET TRANSACTION READ ONLY")
        db.execute("SET LOCAL search_path=pg_catalog,retail_course,pg_temp")
        value, status = expected(db, ORDER, p)["total"]

    def approve_and_release(v, gray_for=None):
        v = api(author, "assets/" + v["asset_id"])
        b = post(
            author,
            "bindings",
            version_id=v["version_id"],
            expected_digest=v["digest"],
            reuse_binding_id=original["binding_id"],
        )["binding_id"]
        fields = {
            "binding_id": b,
            "expected_digest": v["digest"],
            "note": "已核对定义及独立数据源。",
        }
        path = "revisions/" + v["version_id"] + "/"
        post(author, path + "submit", **fields)
        post(expert, path + "business", **fields)
        api(data, path + "data", {"request_key": str(uuid4()), **fields}, failure=422)
        trial = {
            "request_key": str(uuid4()),
            "version_id": v["version_id"],
            "binding_id": b,
            "parameters": p,
        }
        api(
            data,
            "trial",
            {
                **trial,
                "expected": [{"group_key": "total", "value": str(value + 1), "status": status}],
            },
            failure=422,
        )
        result = api(
            data,
            "trial",
            {**trial, "expected": [{"group_key": "total", "value": str(value), "status": status}]},
        )
        assert result["matched"] and result["rows"] == 1
        post(data, path + "data", **fields)
        focus = api(author, "assets/" + v["asset_id"], binding_id=b)
        generation = focus["generation"]
        if gray_for:
            gray = post(
                author,
                "releases",
                version_id=v["version_id"],
                expected_generation=generation,
                mode="gray",
                audience_id=gray_for,
                **fields,
            )
            assert api(reader, "assets/" + v["asset_id"])["version_id"] == v["version_id"]
            assert api(outside, "assets/" + v["asset_id"])["version_id"] == stable["version_id"]
            generation = gray["generation"]
        return post(
            author,
            "releases",
            version_id=v["version_id"],
            expected_generation=generation,
            mode="stable",
            **fields,
        )

    stable = approve_and_release(v)

    def pin():
        return active_runs.enter_context(
            running_registry(roster["101.sales"], "查本期内的" + draft["name"])
        )

    def resolve(service):
        return asyncio.run(
            invoke(
                service,
                "resolve",
                {
                    "mention": draft["code"],
                    "parameters": p,
                    "historical_as_of": FINAL_K,
                },
            )
        )

    def query(service, plan):
        return asyncio.run(invoke(service, "query", {"query_ref": plan["query_ref"]}))

    if args.verify_live_pin:
        ongoing = pin()
        old_plan = resolve(ongoing)
        assert old_plan["metric"]["version_id"] == stable["version_id"]
    detail = api(author, "assets/" + v["asset_id"])
    updated = post(
        author,
        "drafts",
        **{
            **draft,
            "asset_id": v["asset_id"],
            "expected_digest": detail["digest"],
            "summary": "更新负责人的使用说明",
        },
    )
    sales_scope = next(
        s["id"] for s in scopes if s["kind"] == "department" and s["label"] == "sales"
    )
    second = approve_and_release(updated, gray_for=sales_scope)
    assert second["generation"] > stable["generation"]
    if args.verify_live_pin:
        new_run = pin()
        new_plan = resolve(new_run)
        assert new_plan["metric"]["version_id"] == second["version_id"]
        assert resolve(ongoing)["metric"]["version_id"] == stable["version_id"]
        for service, plan in ((ongoing, old_plan), (new_run, new_plan)):
            result = query(service, plan)
            assert result["metric"]["version_id"] == plan["metric"]["version_id"]
            assert Decimal(result["rows"][0][result["columns"].index("value")]) == value
    rolled = post(
        author,
        "releases/" + stable["release_id"] + "/rollback",
        expected_generation=second["generation"],
        note="业务回切第一版",
    )
    for actor in (author, reader):
        catalog = api(actor, "assets", state="published")["items"]
        restored = next(m for m in catalog if m["asset_id"] == v["asset_id"])
        assert restored["version_id"] == v["version_id"]
        assert api(actor, "assets/" + v["asset_id"])["version_id"] == v["version_id"]
    head_generation = rolled["generation"]
    if args.verify_live_pin:
        prior_revoke = post(
            author,
            "releases/" + stable["release_id"] + "/revoke",
            expected_generation=head_generation,
            note="撤销在途旧版本",
        )
        head_generation = prior_revoke["generation"]
        try:
            query(ongoing, old_plan)
        except ModelError as error:
            assert error.status == 403, error.code
        else:
            raise AssertionError("a revoked release still allowed its earlier query plan")
        assert query(new_run, new_plan)["metric"]["version_id"] == second["version_id"]
        print("发布切换后在途 Run 仍以原版取数，新 Run 使用新版；撤销原发布后旧计划返回 403。")
    revoked = post(
        author,
        "releases/" + rolled["release_id"] + "/revoke",
        expected_generation=head_generation,
        note="验收撤销本次回切清单",
    )
    assert revoked["state"] == "revoked"
    record = next(
        r for r in api(author, "releases")["items"] if r["release_id"] == rolled["release_id"]
    )
    assert record["publisher"] and record["note"] == "业务回切第一版"
    assert record["restored_from"] == stable["release_id"]
    assert record["previous_release_id"] == second["release_id"]
    assert record["revoked_reason"] == "验收撤销本次回切清单"
    for actor in (author, reader):
        assert all(
            m["asset_id"] != v["asset_id"] for m in api(actor, "assets", state="published")["items"]
        )
    api(reader, "assets/" + v["asset_id"], failure=404)
    print("两版均完成试算、双审和发布。灰度受众、稳定版、发布审计、回切和撤销后的可见性通过。")


if __name__ == "__main__":
    with ExitStack() as runs:
        main(runs)
