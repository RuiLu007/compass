"""Exercise pinned search, drilldown and period comparison on a dedicated Agent test store."""

import argparse
import asyncio
import json
import os
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from compass_agent.tools.contracts import ToolRejected
from running_registry import invoke, running_registry
from verify_retail_content import FINAL_K


async def main(runs):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("personas", type=Path)
    args = parser.parse_args()
    people = json.loads(args.personas.read_text())
    who = people["101.sales"]
    question = "比较两期各渠道订单，并从地区下钻到渠道。"
    first = runs.enter_context(running_registry(who, question))
    other = runs.enter_context(running_registry(who, question))

    async def refused(awaitable, reason):
        try:
            await awaitable
        except ToolRejected as error:
            assert error.code == reason, error.code
        else:
            raise AssertionError("expected rejection: " + reason)

    search = {"query": "", "kinds": ["metric"], "limit": 1}
    invocation = (uuid4(), str(uuid4()))
    page = await invoke(first, "search", search, invocation=invocation)
    replay = await invoke(first, "search", search, invocation=invocation)
    assert json.dumps(page, ensure_ascii=False) == json.dumps(replay, ensure_ascii=False)
    assert (await invoke(first, "search", search))["status"] == "budget_exhausted"
    next_page = {**search, "cursor": page["cursor"]}
    assert UUID(next_page["cursor"]) and len(page["items"]) == 1
    second_page = await invoke(first, "search", next_page)
    assert second_page["items"][0]["asset_id"] != page["items"][0]["asset_id"]
    await refused(invoke(first, "search", {**next_page, "query": "库存"}), "invalid_parameters")
    await refused(invoke(other, "search", next_page), "semantic_reference_expired")
    current = {
        "start": "2025-10-08",
        "end": "2025-10-15",
        "group_by": "channel",
        "filters": {"region": 1},
    }
    previous = {**current, "start": "2025-10-01", "end": "2025-10-08"}
    plan = await invoke(first, "resolve", {"mention": "sales.order_count", "parameters": current})
    assert plan["previous_equal_window"] == {**previous, "as_of": plan["parameters"]["as_of"]}
    query = {"query_ref": plan["query_ref"]}
    drilled = await invoke(
        first,
        "query",
        {**query, "operation": "drilldown", "parent_group": {"region": 1}},
    )
    await refused(
        invoke(
            first,
            "query",
            {**query, "operation": "drilldown", "parent_group": {"region": 2}},
        ),
        "semantic_rebind_parameters",
    )
    await refused(invoke(other, "query", query), "semantic_reference_expired")
    compare = await invoke(first, "query", {**query, "operation": "compare"})
    assert compare["time_range"] == [plan["parameters"], plan["previous_equal_window"]]
    with psycopg.connect(os.environ["SEMANTIC_RETAIL_101_URL"]) as db:
        db.execute("SET TRANSACTION READ ONLY")
        db.execute("SET LOCAL search_path=pg_catalog,retail_course,pg_temp")

        def expected(p):
            return dict(
                db.execute(
                    """SELECT channel_id::text, count(DISTINCT order_id)
                       FROM order_header
                       WHERE region_id = 1 AND ordered_at >= %s AND ordered_at < %s
                         AND available_at <= %s
                       GROUP BY channel_id""",
                    (
                        p["start"] + " 00:00+08",
                        p["end"] + " 00:00+08",
                        plan["parameters"]["as_of"],
                    ),
                )
            )

        now, before = expected(current), expected(previous)

    def rows(receipt):
        return [dict(zip(receipt["columns"], item, strict=True)) for item in receipt["rows"]]

    assert {str(r["group_key"]): Decimal(r["value"]) for r in rows(drilled)} == now
    values = {
        str(r["group_key"]): Decimal(r["current_minus_comparison"])
        for r in rows(compare)
        if r["current_minus_comparison"] is not None
    }
    assert values == {group: n - before[group] for group, n in now.items() if group in before}
    limited = runs.enter_context(running_registry(people["102.sales"], "按渠道核对区域现金净额"))
    limited_query = await invoke(
        limited,
        "resolve",
        {
            "mention": "sales.net_gmv",
            "parameters": {**previous, "filters": {}},
            "historical_as_of": FINAL_K,
        },
    )
    scoped = await invoke(
        limited, "query", {"query_ref": limited_query["query_ref"], "operation": "compare"}
    )
    assert scoped["data_scope"] == {"tenant_id": 102, "all_regions": False, "region_ids": [1]}
    with psycopg.connect(os.environ["SEMANTIC_RETAIL_102_URL"]) as db:
        db.execute("SET TRANSACTION READ ONLY")
        db.execute("SET LOCAL search_path=pg_catalog,retail_course,pg_temp")

        def cash(p):
            return dict(
                db.execute(
                    """WITH entries AS (
                     SELECT order_id,amount_cent,attempted_at,available_at FROM payment
                     WHERE status='SUCCEEDED'
                     UNION ALL
                     SELECT order_id,-amount_cent,attempted_at,available_at FROM refund
                     WHERE status='SUCCEEDED'
                   ) SELECT h.channel_id::text,sum(e.amount_cent)
                     FROM entries e JOIN order_header h ON h.order_id=e.order_id
                     WHERE e.attempted_at >= %s AND e.attempted_at < %s
                       AND e.available_at <= %s AND h.available_at <= %s GROUP BY h.channel_id""",
                    (p["start"] + " 00:00+08", p["end"] + " 00:00+08", FINAL_K, FINAL_K),
                )
            )

        actual, prior = map(cash, scoped["time_range"])
    deltas = {
        r["group_key"]: Decimal(r["current_minus_comparison"])
        for r in rows(scoped)
        if r["current_minus_comparison"] is not None
    }
    assert prior.keys() - actual.keys()
    assert deltas == {k: actual.get(k, 0) - prior.get(k, 0) for k in actual.keys() | prior.keys()}
    assert [Decimal(t["value"]) for t in scoped["period_totals"]] == [
        sum(actual.values()),
        sum(prior.values()),
    ]
    print(
        json.dumps(
            {
                "pages": 2,
                "invalid_cursors_rejected": 2,
                "channels": len(now),
                "signed_differences": len(values),
                "money_groups": len(deltas),
                "prior_only_groups": len(prior.keys() - actual.keys()),
                "independent_totals_match": True,
                "cross_run_plan_rejected": True,
                "wrong_parent_rejected": True,
            }
        )
    )


if __name__ == "__main__":
    with ExitStack() as stack:
        asyncio.run(main(stack))
