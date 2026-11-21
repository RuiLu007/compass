"""Exercise configured scopes, source, names, units and defaults in a new tenant."""

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
from compass_agent.context_runtime.pipeline import exact
from compass_agent.context_runtime.source_access import allows
from compass_agent.semantic.receipts import scope_lines
from compass_agent.settings import get_settings
from extension_source import prepare_source
from run_chat_cohort import Actor
from running_registry import invoke, running_registry


def identifier():
    return str(uuid4())


def read(actor, path):
    reply = actor.http.get("/api/semantic/" + path, params=actor.scope)
    reply.raise_for_status()
    return reply.json()


def write(actor, path, body, *, semantic=True, scoped=False):
    if semantic:
        body = {"request_key": identifier(), **body}
    reply = actor.http.post(
        ("/api/semantic/" if semantic else "/api/web/v1/") + path,
        params=actor.scope if semantic or scoped else None,
        json=body,
    )
    if not reply.is_success:
        raise AssertionError((path, reply.status_code, reply.text[:240]))
    return reply.json()


def new_tenant(origin, roster):
    platform = Actor(origin, roster["platform_operator"])
    organizer = Actor(origin, roster["operator"])
    target = {"tenantId": identifier(), "workspaceId": identifier()}
    tid, wid = target.values()
    write(
        platform,
        "platform/tenants",
        {
            "id": tid,
            "name": "窑区运营 " + tid[:8],
            "timezone": "UTC",
            "initialAdminLoginName": roster["operator"]["handle"],
        },
        semantic=False,
    )
    write(organizer, f"tenants/{tid}/workspaces", {"id": wid, "name": "周转调度"}, semantic=False)
    people = {
        k: {**roster[k], **target}
        for k in (
            "operator",
            "expert",
            "steward",
            "101.sales",
            "101.sales_finance",
            "platform_operator",
        )
    }
    perms = [
        "module.analysis",
        "module.admin.data",
        "models.invoke",
        "semantics.read",
        "semantics.query",
    ]
    for key in ("expert", "steward", "101.sales", "101.sales_finance", "platform_operator"):
        mid = identifier()
        admin = key in {"expert", "steward"}
        write(
            organizer,
            f"tenants/{tid}/members",
            {
                "id": mid,
                "loginName": people[key]["handle"],
                "roleKey": "admin" if admin else "member",
                "permissionKeys": [] if admin else perms,
            },
            semantic=False,
        )
        if not admin:
            write(
                organizer,
                f"tenants/{tid}/workspaces/{wid}/members",
                {
                    "id": identifier(),
                    "tenantMemberId": mid,
                    "roleKey": "analyst",
                    "permissionKeys": perms,
                },
                semantic=False,
            )
    actors = {key: Actor(origin, person) for key, person in people.items()}
    return people, actors


def main(runs):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument("roster", type=Path)
    args = parser.parse_args()
    if urlsplit(args.origin).hostname not in {"localhost", "127.0.0.1"}:
        parser.error("Use an isolated local application instance.")
    people, actors = new_tenant(args.origin, json.loads(args.roster.read_text()))
    writer, expert, steward = (actors[k] for k in ("operator", "expert", "steward"))
    os.environ["COMPASS_SEMANTIC_WORKSPACES"] = writer.scope["workspaceId"]
    get_settings.cache_clear()
    tenant = write(writer, "scopes", {"kind": "tenant"})["scope_id"]
    groups = [
        write(writer, "scopes", {"kind": kind, "name": name})["scope_id"]
        for kind, name in (("department", "窑务"), ("job", "抽检"))
    ]
    members = {m["name"]: m["id"] for m in read(writer, "organization")["members"]}
    for index, g in enumerate(groups):
        for key, p in people.items():
            if key == "platform_operator" or (index and key == "101.sales"):
                continue
            for cap in ["member", "share"] if key == "operator" else ["member"]:
                write(
                    writer,
                    "grants",
                    {
                        "scope_id": g,
                        "capability": cap,
                        "member_id": members[p["handle"]],
                        "active": True,
                    },
                )
    connection, expr, source_admin = prepare_source(
        os.environ["SEMANTIC_EXTENSION_SOURCE_ADMIN_URL"]
    )
    conn_id = write(
        actors["platform_operator"], "data/connections", connection, semantic=False, scoped=True
    )["id"]
    src = write(
        writer,
        "sources",
        {
            "owner_scope_id": tenant,
            "kind": "schema",
            "locator": "kiln_ops.shard_moves",
            "version": "1",
            "priority": "P1",
            "responsible": "窑务",
            "excerpt": "记录收坯及抽检托当量，按烧成时间和入账可见时间核对。",
        },
    )["source_id"]
    period = {
        "start": "2026-05-04",
        "end": "2026-05-07",
        "as_of": "2026-06-01T00:00:00Z",
        "group_by": "total",
        "filters": {},
    }

    def publish(code, name, usage, measure=None, expected=None):
        v = write(
            writer,
            "drafts",
            {
                "owner_scope_id": tenant,
                "usage_scope_id": usage,
                "code": code,
                "name": name,
                "kind": "metric" if measure else "term",
                "domain": "窑务",
                "summary": name + "的已登记统计含义",
                "source_id": src,
                "source_position": "第2段",
                "responsible": "窑务负责人",
                "grain": "一次过秤",
                "unit": "托当量",
                "formula": "sum(" + measure + ")" if measure else "术语",
                "event_time": "fired_at",
                "timezone": "UTC",
                "aggregation": "sum",
                "dimensions": ["line"],
                "filters": ["plant"],
                "aliases": ["周转当量"] if measure else [],
            },
        )
        fields = {"expected_digest": v["digest"], "note": "核对物理列与范围"}
        if measure:
            b = write(
                writer,
                "bindings",
                {
                    "expected_digest": v["digest"],
                    "version_id": v["version_id"],
                    "mapping": {
                        "connection_id": conn_id,
                        "schema": "kiln_ops",
                        "relation": "shard_moves",
                        "measure": measure,
                        "event_time": "fired_at",
                        "available_at": "known_at",
                        "aggregate": "sum",
                        "dimensions": {"line": "lane"},
                        "filters": {"plant": "kiln"},
                        "row_policies": [expr],
                        "source_release": "kiln-revision-1",
                    },
                },
            )
            fields["binding_id"] = b["binding_id"]
        path = "revisions/" + v["version_id"] + "/"
        write(writer, path + "submit", fields)
        write(expert, path + "business", fields)
        if measure:
            write(
                steward,
                "trial",
                {
                    "version_id": v["version_id"],
                    "binding_id": fields["binding_id"],
                    "parameters": period,
                    "expected": [{"group_key": "total", "value": str(expected), "status": "OK"}],
                },
            )
        write(steward, path + "data", fields)
        version = read(writer, "assets/" + v["asset_id"])
        write(
            writer,
            "releases",
            {**fields, "version_id": v["version_id"], "expected_generation": version["generation"]},
        )
        return v

    term = publish("kiln.turnover", "周转当量", tenant)
    received = publish("kiln.received", "收坯当量", groups[0], "received", 16)
    inspected = publish("kiln.inspected", "抽检当量", groups[1], "inspected", 9)

    def start(key):
        return runs.enter_context(running_registry(people[key], "核对窑区周转当量"))

    def tool(service, name, values):
        return asyncio.run(invoke(service, name, values))

    def resolve(service, mention, params=None):
        return tool(
            service,
            "resolve",
            {
                "mention": mention,
                "parameters": params or period,
                "historical_as_of": period["as_of"],
            },
        )

    def policy(defaults):
        generation = read(writer, "policies/" + tenant)["generation"]
        return write(
            writer,
            "policies",
            {
                "scope_id": tenant,
                "source_id": src,
                "expected_generation": generation,
                "note": "按窑区词表设置",
                "policy": {
                    "pinned_asset_ids": [inspected["asset_id"]],
                    "defaults": defaults,
                    "page_size": 1,
                    "context_max_items": 1,
                    "max_search_rounds": 3,
                },
            },
        )

    only = start("101.sales")
    assert resolve(only, "周转当量")["metric"]["asset_id"] == received["asset_id"]
    both = start("101.sales_finance")

    def options(service):
        v = asyncio.run(service.api("resolve", {"mention": "周转当量"}))
        assert v["status"] == "needs_clarification"
        return {o["asset_id"] for o in v["options"]}

    expected = {received["asset_id"], inspected["asset_id"]}
    assert options(both) == expected
    policy([{"term_ref": term["asset_id"], "target_metric_ref": inspected["asset_id"]}])
    fresh = start("101.sales_finance")
    assert resolve(fresh, "周转当量")["metric"]["asset_id"] == inspected["asset_id"]
    assert options(both) == expected
    policy([])
    after = start("101.sales_finance")
    assert options(after) == expected
    assert resolve(fresh, "周转当量")["metric"]["asset_id"] == inspected["asset_id"]
    definition = tool(only, "definition", {"asset_id": received["asset_id"]})
    assert definition["unit"] == "托当量" and definition["definition"]["dimensions"] == ["line"]
    p = {**period, "group_by": "line", "filters": {"plant": "north"}}
    plan = resolve(only, "kiln.received", p)
    run_query = {"query_ref": plan["query_ref"]}
    current = tool(
        only, "query", {**run_query, "operation": "drilldown", "parent_group": {"plant": "north"}}
    )
    compared = tool(only, "query", {**run_query, "operation": "compare"})

    def numbers(observation, column):
        result = {}
        for values in observation["rows"]:
            row = dict(zip(observation["columns"], values, strict=True))
            if row[column] is not None:
                result[row["group_key"]] = Decimal(row[column])
        return result

    assert numbers(current, "value") == {"amber": 8, "blue": 5}
    assert numbers(compared, "current_minus_comparison") == {"amber": 2, "blue": -4}
    assert any("托当量" in line for line in scope_lines({"receipt": current}))

    admin = actors["platform_operator"]
    path = "tasks/business_qa/scenes/metric_lookup/answer.md"

    def read_prompt():
        reply = admin.http.get("/api/web/v1/platform/promptops/file", params={"path": path})
        reply.raise_for_status()
        return reply.json()

    def publish_sop(body):
        active = read_prompt()

        def command(name, args):
            return write(admin, "platform/promptops/" + name, args, semantic=False)

        command(
            "file-draft",
            {
                "path": path,
                "body": body,
                "expected_edit_rev": active["edit_rev"],
                "base_epoch": active["active_epoch"],
                "request_id": identifier(),
            },
        )
        target = {"action": "file", "path": path}
        preview = command("release-preview", target)
        assert not preview["errors"] and [c["path"] for c in preview["changes"]] == [path]
        return command(
            "releases",
            {
                **target,
                "request_id": identifier(),
                "confirmation_hash": preview["confirmation_hash"],
                "note": "窑区指标说明顺序",
            },
        )["generation"]

    def bound_sop(service):
        with service.store.transaction() as tx:
            return exact(tx, service.journal.context_binding, [path])[0].body

    original = read_prompt()["body"]
    assert bound_sop(only) == original
    revised = original + "\n\n汇报时先列窑区筛选和观察日期，再列实收托当量。\n"
    try:
        generation = publish_sop(revised)
        after_sop = start("101.sales")
        assert bound_sop(only) == original and bound_sop(after_sop) == revised
        assert after_sop.journal.context_binding["generation"] == generation
        assert generation > only.journal.context_binding["generation"]
        other = tool(
            after_sop, "query", {"query_ref": resolve(after_sop, "kiln.received", p)["query_ref"]}
        )
        assert numbers(other, "value") == numbers(current, "value")
        assert (
            other["manifest_hash"]
            == tool(after_sop, "definition", {"asset_id": received["asset_id"]})["manifest_hash"]
        )
    finally:
        if read_prompt()["body"] == revised:
            publish_sop(original)

    def allowed():
        payload = only.get(current["resultId"], "result")
        return allows(only.store, only.trusted, [payload["source"]], epoch=only.journal.epoch)

    assert allowed()
    with psycopg.connect(source_admin) as db:
        db.execute("ALTER POLICY owner_read ON kiln_ops.shard_moves USING (false)")
    assert not allowed()
    print("通过 PromptOps 发布新场景 SOP，新旧 Run 读取各自正文，同一授权指标逐组数值保持一致。")
    print("新租户两个交叉范围与两种同名指标通过。调整和清除默认策略后，新旧 Run 分别命中预期。")
    print(
        "陌生表和物理列完成真实试算、双审、释义、下钻与等长对比。"
        "两条线的差额为 +2、−4 托当量，收紧行策略后旧结果停止展示。"
    )


if __name__ == "__main__":
    with ExitStack() as runs:
        main(runs)
