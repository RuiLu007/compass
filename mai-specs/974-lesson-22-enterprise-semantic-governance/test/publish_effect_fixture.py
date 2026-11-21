"""Publish only the retail definitions and local bindings used by the frozen acceptance cohort."""

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from run_chat_cohort import Actor
from score_sql_bindings import FINANCE, FIRST, LATEST, NET, ORDER, PAID, ROAS, STOCK


def get(actor, path, **params):
    response = actor.http.get("/api/semantic/" + path, params={**actor.scope, **params})
    response.raise_for_status()
    return response.json()


def post(actor, path, **fields):
    response = actor.http.post(
        "/api/semantic/" + path,
        params=actor.scope,
        json={**fields, "request_key": str(uuid4())},
    )
    response.raise_for_status()
    return response.json()


def approve(actors, info, binding=None):
    author, expert, steward = actors
    key = info["asset_id"]

    def active():
        matches = get(author, "assets", q=info["code"])["items"]
        return any(
            item["asset_id"] == key
            and item["version_id"] == info["version_id"]
            and item["state"] == "published"
            and (not binding or item["binding_id"] == binding)
            for item in matches
        )

    if active():
        return
    target = {"binding_id": binding} if binding else {}
    details = get(author, f"assets/{key}", **target)
    fields = {
        **target,
        "expected_digest": details["digest"],
        "note": "专用教学验收库已按冻结规范核对口径、数据源及本工作区权限。",
    }
    transitions = (
        ("draft", "submit", author),
        ("business", "business", expert),
        ("data", "data", steward),
    )
    for state, action, actor in transitions:
        if details["state"] == state:
            post(actor, f"revisions/{info['version_id']}/{action}", **fields)
            details = get(author, f"assets/{key}", **target)
    if details["state"] == "approved":
        post(
            author,
            "releases",
            **fields,
            mode="stable",
            version_id=info["version_id"],
            expected_generation=details["generation"],
        )
    if not active():
        raise ValueError(f"{info['code']}: publication stopped at {details['state']}")


def publish_workspace(tenant, people):
    catalog = {m["asset_id"]: m for m in get(people[0], "assets")["items"]}
    codes = {m["code"]: m for m in catalog.values()}
    done = set()

    def publish(key):
        if key in done:
            return
        item = catalog[key]
        for dep in item["dependencies"]:
            publish(dep)
        detail = get(people[0], f"assets/{key}")
        binding = next(
            (
                b["binding_id"]
                for b in detail.get("bindings", [])
                if b["source_ref"] == f"retail:{tenant}"
            ),
            None,
        )
        approve(people, item, binding)
        done.add(key)

    required = [PAID, NET, FIRST, LATEST, STOCK, "entity.order_line", "term.sales_amount"]
    if tenant == 101:
        required = [FINANCE, ROAS, *required]
    for code in required:
        publish(codes[code]["asset_id"])
    print(f"{tenant}: {len(done)} definitions and dependencies published", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument("personas", type=Path)
    args = parser.parse_args()
    if urlsplit(args.origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("a local acceptance server is required")
    if not urlsplit(os.environ["WEB_TEST_DATABASE_URL"]).path.startswith("/compass_web_test_"):
        parser.error("a dedicated Web test database is required")
    roster = json.loads(args.personas.read_text())
    with ExitStack() as stack:

        def actors(prefix="", suffix=""):
            people = []
            for role in ("operator", "expert", "steward"):
                account = Actor(args.origin, roster[prefix + role + suffix])
                stack.callback(account.http.close)
                people.append(account)
            ids = {roster[prefix + r + suffix]["actor"] for r in ("operator", "expert", "steward")}
            assert len(ids) == 3, "independent author and reviews required"
            return people

        local = {101: actors(), 102: actors(suffix="102")}
        platform = actors(prefix="platform_")
        for tenant, people in local.items():
            publish_workspace(tenant, people)
        order = next(x for x in get(platform[0], "assets")["items"] if x["code"] == ORDER)
        approve(platform, order)
        for tenant, people in local.items():
            item = get(people[0], f"assets/{order['asset_id']}")
            local_binding = next(
                b["binding_id"] for b in item["bindings"] if b["source_ref"] == f"retail:{tenant}"
            )
            approve(people, item, local_binding)
            print(f"{tenant}: independently approved public order binding", flush=True)


if __name__ == "__main__":
    main()
