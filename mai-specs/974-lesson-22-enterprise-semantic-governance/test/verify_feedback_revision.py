"""Follow a reader's feedback through an AI-assisted revision, trial, review and release."""

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from publish_effect_fixture import get, post
from run_chat_cohort import Actor
from score_sql_bindings import PAID, expected
from verify_retail_content import FINAL_K


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Local Web origin for a disposable release history")
    parser.add_argument("personas", type=Path)
    args = parser.parse_args()
    if urlsplit(args.origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("a local acceptance server is required")
    if not urlsplit(os.environ["WEB_TEST_DATABASE_URL"]).path.startswith("/compass_web_test_"):
        parser.error("use a separate Web test database with published sales metrics")
    roster = json.loads(args.personas.read_text())
    with ExitStack() as stack:
        actors = []
        for name in ("101.sales", "operator", "expert", "steward"):
            person = Actor(args.origin, roster[name])
            stack.callback(person.http.close)
            actors.append(person)
        reader, author, expert, steward = actors
        previous = next(m for m in get(reader, "assets")["items"] if m["code"] == PAID)
        path = "assets/" + previous["asset_id"]
        old = get(author, path, binding_id=previous["binding_id"])
        assert previous["version_id"] == old["version_id"] and old["editable"]
        window = {
            "start": "2025-11-17",
            "end": "2025-11-24",
            "as_of": FINAL_K,
            "filters": {},
            "group_by": "total",
        }
        retail = stack.enter_context(psycopg.connect(os.environ["SEMANTIC_RETAIL_101_URL"]))
        retail.execute("SET TRANSACTION READ ONLY")
        retail.execute("SET LOCAL search_path=pg_catalog,retail_course,pg_temp")
        cents, status = expected(retail, PAID, window)["total"]

        def check_sample(view):
            result = post(
                author,
                "trial",
                version_id=view["version_id"],
                binding_id=view["binding_id"],
                parameters=window,
                expected=[{"group_key": "total", "value": str(cents), "status": status}],
            )
            assert result["matched"] and result["rows"] == 1

        check_sample(old)
        note = "周报把支付日期理解为下单日期，请在业务说明中标明两者的区别和成功支付状态。"
        feedback = post(
            reader, "feedback", version_id=old["version_id"], category="定义误解", note=note
        )
        recorded = next(row for row in get(author, path)["feedback"] if row["id"] == feedback["id"])
        assert (recorded["version_id"], recorded["category"], recorded["note"]) == (
            old["version_id"],
            "定义误解",
            note,
        )
        assert recorded["revision_id"] is None
        material = post(
            author,
            "sources",
            owner_scope_id=old["owner_scope_id"],
            kind="document",
            locator="sales/weekly-payment-clock/" + uuid4().hex[:10],
            version="1",
            excerpt=note + " 按成功支付的 attempted_at 划分期间，以分存储金额，按支付流水汇总。",
            priority="P1",
            responsible="销售经营组",
        )
        suggestion = author.http.post(
            "/api/semantic/suggestions",
            params=author.scope,
            json={"source_refs": [material["source_id"]], "owner_scope_id": old["owner_scope_id"]},
            timeout=55,
        )
        suggestion.raise_for_status()
        idea = next(
            i for i in suggestion.json()["candidates"] if i["source_ref"] == material["source_id"]
        )
        assert idea["summary"] and idea["formula"] and idea["position"]
        fields = {
            k: old[k]
            for k in (
                "asset_id",
                "owner_scope_id",
                "usage_scope_id",
                "code",
                "domain",
                "kind",
                "name",
                "responsible",
                "grain",
                "unit",
                "aliases",
                "dependencies",
            )
        }
        for k in ("event_time", "timezone", "aggregation", "dimensions", "filters"):
            fields[k] = old["definition"][k]
        nxt = post(
            author,
            "drafts",
            **fields,
            summary=idea["summary"],
            formula=idea["formula"],
            expected_digest=old["digest"],
            source_id=idea["source_ref"],
            source_position=idea["position"],
        )
        adopted = post(
            author, "feedback/" + feedback["id"] + "/adopt", revision_id=nxt["version_id"]
        )
        assert adopted["version_id"] == nxt["version_id"] and nxt["version_id"] != old["version_id"]
        details = get(author, path)
        linked = next(row for row in details["feedback"] if row["id"] == feedback["id"])
        assert (linked["version_id"], linked["revision_id"]) == (
            previous["version_id"],
            nxt["version_id"],
        )
        assert get(reader, path)["version_id"] == old["version_id"]
        binding = post(
            author,
            "bindings",
            version_id=nxt["version_id"],
            expected_digest=details["digest"],
            reuse_binding_id=old["binding_id"],
        )["binding_id"]
        review = {
            "expected_digest": details["digest"],
            "binding_id": binding,
            "note": "已重审反馈所涉口径和独立支付样本。",
        }
        revision = "revisions/" + nxt["version_id"] + "/"
        post(author, revision + "submit", **review)
        post(expert, revision + "business", **review)
        check_sample({"version_id": nxt["version_id"], "binding_id": binding})
        post(steward, revision + "data", **review)
        assert get(reader, path)["version_id"] == old["version_id"]
        approved = get(author, path, binding_id=binding)
        post(
            author,
            "releases",
            version_id=nxt["version_id"],
            expected_generation=approved["generation"],
            mode="stable",
            **review,
        )
        final = get(reader, path)
        assert final["version_id"] == nxt["version_id"] and final["binding_id"] == binding
        assert final["summary"] == idea["summary"]
        assert get(author, path)["source_id"] == idea["source_ref"]
        check_sample(final)
        print(f"反馈关联原版，AI 建议通过双审与独立试算后发布；同一问题两版均为 {cents} 分。")


if __name__ == "__main__":
    main()
