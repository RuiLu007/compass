"""Instructor-only replay of the purchase and follow-up against one authorized search Run."""

import argparse
import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from compass_agent.context_runtime.search_control import SearchDispatch, SearchProgress, Target
from compass_agent.context_runtime.search_verification import (
    read_citations,
    verify_batch,
    verify_draft,
)
from compass_agent.data_connections.connections import DataConnections
from compass_agent.data_connections.tools import provider
from compass_agent.documents.service import DocumentTools
from compass_agent.execution_context import current_execution
from compass_agent.harness.autonomy import AutonomyBudget, AutonomyLimits
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.runtime.journal import ExecutionJournal
from compass_agent.runtime.redis_stream import OutboxPublisher, RedisStream
from compass_agent.runtime.store import RuntimeStore
from compass_agent.schemas import SessionSubmit, ToolCall, ToolCallFunction, TrustedContext
from compass_agent.semantic.service import RegistryTools
from compass_agent.settings import Settings
from compass_agent.tools.artifacts import ArtifactStore
from compass_agent.tools.batch import dispatch_batch
from compass_agent.tools.catalog import registrations
from compass_agent.tools.catalog_contracts import CatalogContext
from compass_agent.tools.contracts import ToolBatchPolicy, ToolExecutionContext
from compass_agent.tools.dispatcher import ExecutionSlots, ToolDispatcher
from compass_agent.tools.registry import ToolRegistry
from full_case import collect_full, verify_full, verify_full_citations
from materials import TENANT, WORKSPACE
from sqlalchemy import create_engine

RECEIPTS = """
SELECT d.document_no, m.event_at AS received_at, m.sellable_delta AS received_units
FROM core.inventory_document d
JOIN core.inventory_document_line l USING (tenant_id, inventory_doc_id)
JOIN core.procurement_revision r USING (tenant_id, inventory_doc_line_id)
JOIN core.inventory_movement m USING (tenant_id, inventory_doc_line_id)
WHERE d.tenant_id=101 AND d.doc_type='PURCHASE' AND d.region_id=1 AND d.to_site_id=7
AND l.sku_id=435 AND m.site_id=7 AND m.movement_type='RECEIPT'
AND d.planned_at >= DATE '2025-09-01' AND d.planned_at < DATE '2025-10-01'
AND greatest(d.available_at,l.available_at,r.available_at,m.available_at)<='{known}'
ORDER BY d.document_no,r.revision_no,m.event_at
"""
SAMPLES = """
SELECT COUNT(DISTINCT b.session_id) FILTER (WHERE c.campaign_id IS NOT NULL) AS tagged
FROM core.behavior_event b
JOIN core.web_session s ON s.tenant_id=b.tenant_id AND s.session_id=b.session_id
 AND s.channel_id=4 AND s.region_id=1 AND s.available_at<='{known}'
LEFT JOIN core.campaign c ON c.tenant_id=s.tenant_id AND c.campaign_id=s.campaign_id
 AND c.target_channel_id=4 AND b.event_at>=c.starts_at AND b.event_at<c.ends_at
 AND c.available_at<='{known}'
WHERE b.tenant_id=101 AND b.region_id=1 AND b.sku_id=435 AND b.event_type='PRODUCT_VIEW'
 AND b.event_at>='{start}T00:00:00+08:00' AND b.event_at<'{end}T00:00:00+08:00'
 AND b.available_at<='{known}'
"""


def checked_report(store, rid, trusted, body):
    objects = store.read_run(rid, trusted).public_view["objects"].values()
    (published,) = [item for item in objects if item["kind"] == "artifact"]
    report = store.artifact(rid, UUID(published["objectId"]), trusted)
    assert (report.name, report.media_type, report.content) == (
        "report.md",
        "text/markdown",
        body.encode(),
    )
    return report.id


async def purchase_replay(variant="base", previous_run=None, full_case=False):
    settings = Settings()
    database = os.environ["DATABASE_URL"]
    web_database = os.environ["WEB_TEST_MIGRATION_DATABASE_URL"]
    if not all(
        "/compass_" in urlsplit(v).path and "_test_" in urlsplit(v).path
        for v in (database, web_database)
    ):
        raise ValueError("Use dedicated classroom databases")
    with psycopg.connect(web_database) as db:
        (actor,) = db.execute(
            "SELECT id FROM web.app_user WHERE login_name='teaching.analyst'"
        ).fetchone()
    trusted = TrustedContext.model_validate(
        {
            "purpose": "chat",
            "actor_user_id": actor,
            "scope": {"kind": "tenant", "tenant_id": TENANT, "workspace_id": WORKSPACE},
        }
    )
    engine = create_engine(database)
    store = RuntimeStore(
        engine, settings.compass_runtime_state_key.get_secret_value(), settings=settings
    )
    store.ready = store.distributed = True
    streams = RedisStream(
        settings.compass_redis_url, settings.compass_redis_password.get_secret_value()
    )
    publisher = OutboxPublisher(store, streams)
    submitted = store.submit(
        SessionSubmit.model_validate(
            {
                "requestId": str(uuid4()),
                "question": (
                    "讲师回放，核对华东小程序三个月的销售与退款、两窗顾客下单及仓库恢复、"
                    "当前库存、投放档位、余款与复核安排。"
                    if full_case
                    else "讲师回放，核对采购资格、建议和回函复核安排。"
                ),
                "selection": {"mode": "auto", "provider_id": None},
            }
        ),
        trusted,
    )
    rid = UUID(submitted["runId"])
    await publisher.flush(rid)
    epoch = store.claim(rid, uuid4())
    await publisher.flush(rid)
    journal = ExecutionJournal(store, publisher, rid, epoch)
    token = current_execution.set(journal)
    try:
        pin = journal.search_snapshot
        assert set(pin["kinds"]) == {"semantic", "detail", "knowledge", "history", "external"}
        progress = journal.search_progress = SearchProgress(pin)
        progress.plan(
            [Target(key="arrival", goal="核对实际采购入库的记录", source="detail", required=True)]
        )
        control = RunRegistry().register(OwnerKey.from_context(trusted), run_id=rid, lifetime_s=900)
        budget = AutonomyBudget((), AutonomyLimits(tools=100))
        details = await provider(DataConnections(engine, settings))(
            trusted, control, lambda _: None
        )
        context = CatalogContext(
            frozenset({"semantic_read", "semantic_query", "documents"}),
            documents=DocumentTools(journal, trusted),
            semantics=RegistryTools(journal, trusted),
        )
        registry = ToolRegistry([*registrations(context, lambda _: None), *details])
        dispatcher = ToolDispatcher(registry, ExecutionSlots(), ArtifactStore())
        meter = SearchDispatch(journal, pin, budget)
        source = {}
        evidence = {}
        count = 0

        async def call(name, args, label=None):
            nonlocal count
            count += 1
            step = SimpleNamespace(
                id=uuid4(),
                index=count,
                tool_calls=[
                    ToolCall(
                        id="instructor_" + str(count),
                        function=ToolCallFunction(
                            name=name, arguments=json.dumps(args, ensure_ascii=False)
                        ),
                    )
                ],
            )
            ctx = ToolExecutionContext(
                trusted,
                rid,
                step.id,
                control,
                budget,
                frozenset(registry.entries),
                search_dispatch=meter.invoke,
            )
            results = await dispatch_batch(
                dispatcher, tuple(step.tool_calls), ctx, ToolBatchPolicy()
            )
            result = results[0]
            if result.status not in ("ok", "empty"):
                raise ValueError(
                    f"{count} {name}: {result.status} {result.reason_code} "
                    f"{str(result.payload)[:180]}"
                )
            observations = await asyncio.to_thread(verify_batch, journal, step, results, trusted)
            progress.record(step, results, observations, budget)
            if label:
                evidence[label] = observations[0]["evidenceId"]
                source[label] = result.payload
            return result.payload

        async def read(key, kind, code, heading):
            result = await call("search_documents", {"kind": kind, "query": code + " " + heading})
            selected = next(
                v for v in result["sections"] if v["docId"] == code and heading in v["heading"]
            )
            await call("read_document", {"ref": selected["ref"], "offset": 0}, key)

        for args in (
            ("balance", "history", "H-0912", "待办"),
            ("trial", "knowledge", "K-17", "§1"),
            ("tier", "knowledge", "K-17", "§2"),
            ("review", "knowledge", "K-17", "§3"),
            ("offer", "external", "E-1009", "排期"),
            ("calendar", "external", "E-CAL-2025", "调休"),
            ("window", "knowledge", "K-08", "§2"),
        ):
            await read(*args)
        # Check the policy ceiling as soon as its two original inputs are available.
        # The ordinary final revision later refers back to these private claim keys.
        ceiling = [
            {
                "key": "balance",
                "op": "measure",
                "evidenceId": evidence["balance"],
                "quote": "20,000 元",
            },
            {"key": "fraction", "op": "measure", "evidenceId": evidence["trial"], "quote": "40%"},
            {"key": "cap", "op": "multiply", "parents": ["balance", "fraction"]},
        ]
        checkpoint = {
            "targets": [{"key": "cap"}],
            "claimProposals": ceiling,
            "answerBlocks": ["cap"],
        }
        checkpoint_step = SimpleNamespace(id=uuid4())
        interim = await asyncio.to_thread(
            verify_draft,
            journal,
            checkpoint_step,
            json.dumps(checkpoint),
            trusted,
            "business_report",
            checkpoint=True,
        )
        assert interim._delivery is None and "8,000.00" in interim.final_answer
        assert progress.claim_feedback["checkedBlocks"] == ["cap"]
        assert not (await asyncio.to_thread(read_citations, store, rid, trusted))["anchors"]
        for term, code in (
            ("商品浏览", "behavior_event_type"),
            ("采购单", "inventory_document_type"),
            ("实际收货", "inventory_movement_type"),
        ):
            dictionary = await call("search_semantics", {"query": term, "kinds": ["entity"]})
            selected = next(v for v in dictionary["items"] if v["code"] == "dimension." + code)
            await call(
                "get_metric_definition",
                {"asset_id": selected["asset_id"], "version_id": selected["version_id"]},
            )
        for table in (
            "campaign",
            "behavior_event",
            "web_session",
            "inventory_document",
            "inventory_movement",
        ):
            await call("database_query", {"op": "describe_table", "table": table, "schema": "core"})
        known = pin["knownAt"]
        arrival_sql = RECEIPTS
        if full_case:
            arrival_sql = arrival_sql.replace(
                "SELECT d.document_no,",
                "SELECT d.document_no,l.quantity,d.planned_at,r.notified_at,r.revised_due_at,",
            )
        await call(
            "database_query", {"op": "query", "sql": arrival_sql.format(known=known)}, "arrival"
        )
        columns = source["arrival"]["columns"]
        rows = source["arrival"]["rows"]
        i = next(i for i, r in enumerate(rows) if r[columns.index("document_no")] == "DOC13392")
        start = date.fromisoformat(str(rows[i][columns.index("received_at")])[:10])
        assert "连续" in source["window"]["sections"][0]["text"]
        assert (
            "5" in source["window"]["sections"][0]["text"]
            or "五" in source["window"]["sections"][0]["text"]
        )
        end = start + timedelta(days=5)
        await call(
            "database_query",
            {"op": "query", "sql": SAMPLES.format(known=known, start=start, end=end)},
            "samples",
        )

        async def query(key, code, start, end, filters):
            resolved = await call(
                "resolve_semantics",
                {
                    "mention": code,
                    "parameters": {"start": str(start), "end": str(end), "filters": filters},
                },
            )
            if "query_ref" not in resolved:
                raise ValueError(f"{code}: {resolved.get('status')}")
            await call("query_metric", {"query_ref": resolved["query_ref"]}, key)

        sales = {"region": 1, "channel": 4}
        await query("current", "sales.net_gmv", "2025-09-01", "2025-10-01", sales)
        await query("previous", "sales.net_gmv", "2025-08-01", "2025-09-01", sales)
        await query(
            "rate",
            "fulfillment.allocation_insufficient_rate",
            start,
            end,
            {**sales, "fulfillment_site": 7, "sku": 435},
        )
        await query(
            "stock",
            "inventory.closing_available_qty",
            "2025-10-08",
            "2025-10-09",
            {"site": 7, "sku": 435},
        )
        claims = []

        def claim(key, op, **args):
            claims.append(dict(key=key, op=op, **args))

        for key, doc, quote, op in (
            ("minimum", "trial", "30 次", "measure"),
            ("maximum", "trial", "5%", "measure"),
            ("inventory_min", "trial", "100 件", "measure"),
            ("sample_min", "tier", "20 个", "measure"),
            ("tier_rule", "tier", "最高单档", "quote"),
            ("launch", "offer", "10:00 上线", "quote"),
            ("reply", "offer", "15:00 前书面确认", "quote"),
            ("owner", "balance", "负责人为华东渠道经理", "quote"),
            ("review", "review", "第一个工作日", "quote"),
            ("window_rule", "window", "五", "quote"),
        ):
            if key == "window_rule":
                quote = source["window"]["sections"][0]["text"]
            claim(key, op, evidenceId=evidence[doc], quote=quote)
        for key, src, col, row in (
            ("doc", "arrival", "document_no", i),
            ("arrival_time", "arrival", "received_at", i),
            ("qty", "arrival", "received_units", i),
            ("sample", "samples", "tagged", 0),
            ("current", "current", "value", 0),
            ("previous", "previous", "value", 0),
            ("rate", "rate", "value", 0),
            ("attempts", "rate", "denominator", 0),
            ("stock", "stock", "value", 0),
        ):
            claim(key, "cell", evidenceId=evidence[src], row=row, column=col)
        claim("window", "event_window", parents=["arrival_time", "window_rule"])
        claim("arrival", "series", parents=["doc", "arrival_time", "qty", "window"])
        for key, left, right, operator in (
            ("growth", "current", "previous", "gt"),
            ("volume", "attempts", "minimum", "ge"),
            ("shortage", "rate", "maximum", "le"),
            ("inventory", "stock", "inventory_min", "ge"),
            ("expansion", "sample", "sample_min", "ge"),
        ):
            claim(key, "compare", parents=[left, right], comparator=operator)
        claim(
            "purchase",
            "whole_tier",
            evidenceId=evidence["offer"],
            parents=[
                "cap",
                "tier_rule",
                "growth",
                "volume",
                "shortage",
                "inventory",
                "expansion",
                "window",
            ],
        )
        claim("keep", "subtract", parents=["balance", "purchase"])
        claim(
            "action",
            "next_workday",
            evidenceId=evidence["calendar"],
            parents=["launch", "review", "reply", "owner"],
        )
        keys = [
            "arrival",
            "growth",
            "volume",
            "shortage",
            "inventory",
            "expansion",
            "cap",
            "purchase",
            "keep",
            "action",
        ]
        if full_case:
            full_keys = await collect_full(
                call, query, read, evidence, source, claim, known, start, end
            )
            verify_full(source)
            keys = ["purchase", "keep", "action", *full_keys]
        drafts = {
            "revise": True,
            "targets": [{"key": k, "required": True} for k in keys],
            "claimProposals": claims,
            "answerBlocks": keys,
        }
        sid = uuid4()
        generation, attempt, _ = journal.prepare("model", "root/model:" + str(sid), {}, {})
        journal.dispatched(attempt)
        journal.accept(generation, attempt, {"result": {}, "budget": {}, "continuation": None})
        result = await asyncio.to_thread(
            verify_draft,
            journal,
            SimpleNamespace(id=sid),
            json.dumps(drafts),
            trusted,
            "business_report",
        )
        body = result.final_answer
        price_changed = variant == "price"
        expected = (
            ("暂缓采购", "20,000.00 元 − 0.00 元 = 20,000.00 元")
            if price_changed
            else ("6,000.00", "14,000.00", "待签批")
        )
        assert all(
            x in body
            for x in (
                "DOC13392",
                "400",
                "8,000.00",
                *expected,
                "华东渠道经理",
                "2025-10-09 15:00",
                "2025-10-10 10:00",
                "2025-10-11 10:00",
                "试投支出",
            )
        ), body
        store.finish(
            rid,
            epoch,
            "completed",
            answer=body,
            citations=result._citations,
            delivery=result._delivery,
        )
        await publisher.flush(rid)
        retrieved = await asyncio.to_thread(read_citations, store, rid, trusted)
        citations = [
            await asyncio.to_thread(read_citations, store, rid, trusted, a["citationId"])
            for a in retrieved["anchors"]
        ]
        assert all(
            body[a["start"] : a["end"]] == f"[{a['ordinal']}]" and c["status"] == "supported"
            for a, c in zip(retrieved["anchors"], citations, strict=True)
        )
        if full_case:
            verify_full_citations(dict(zip(keys, citations, strict=True)))
            await asyncio.to_thread(checked_report, store, rid, trusted, body)
        decision = next(
            c for c in citations if ("暂缓采购" if price_changed else "建议订") in c["statement"]
        )
        action = next(c for c in citations if "提请签批" in c["statement"])
        offer = next(s for s in decision["sources"] if s["locator"].startswith("E-1009 /"))
        edition, quoted = ("3", "9,000 元") if price_changed else ("2", "6,000 元")
        assert (
            offer["version"] == edition
            and quoted in offer["excerpt"]
            and "15,000 元" in offer["excerpt"]
        )
        if price_changed:
            assert "建议订" not in body and "08:45" in source["offer"]["sections"][0]["publishedAt"]
        original = {}
        if previous_run:
            older = await asyncio.to_thread(read_citations, store, previous_run, trusted)
            for anchor in older["anchors"]:
                previous = await asyncio.to_thread(
                    read_citations, store, previous_run, trusted, anchor["citationId"]
                )
                if "建议订" in previous["statement"]:
                    break
            else:
                raise AssertionError("The previous report has no approved purchase statement")
            previous_offer = next(
                s for s in previous["sources"] if s["locator"].startswith("E-1009 /")
            )
            assert previous_offer["version"] == "2" and "6,000 元" in previous_offer["excerpt"]
            assert "6,000.00 元" in previous["statement"] and "待签批" in previous["statement"]
            original = {
                "previousRunId": str(previous_run),
                "previousQuoteVersion": previous_offer["version"],
            }
        assert {s["kind"] for s in decision["sources"]} >= {
            "knowledge",
            "history",
            "external",
            "metric",
            "definition",
            "detail",
        }
        assert {s["kind"] for s in action["sources"]} == {"knowledge", "history", "external"}
        return {
            "mode": "instructor_replay",
            "runId": str(rid),
            "sessionId": submitted["sessionId"],
            "snapshot": pin["collectionDigest"],
            "knownAt": known,
            "toolCalls": count,
            "citationCount": len(result._citations["details"]),
            "checkedAnchors": len(citations),
            "checkpointClaims": list(progress.claim_feedback["checkedBlocks"]),
            "revisionClaims": len(claims),
            "quoteVersion": edition,
            "quoteTiers": [quoted, "15,000 元"],
            **original,
            **(
                {
                    "case": "G1–G7",
                    "anchorsByKey": {
                        key: {"ordinal": a["ordinal"], "citationId": a["citationId"]}
                        for key, a in zip(keys, retrieved["anchors"], strict=True)
                    },
                }
                if full_case
                else {}
            ),
            "answer": body,
        }
    except BaseException:
        store.finish(rid, epoch, "failed", reason="instructor_replay_failed")
        await publisher.flush(rid)
        raise
    finally:
        current_execution.reset(token)
        await streams.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--variant", choices=["base", "price"], default="base")
    parser.add_argument("--previous-run", type=UUID, help="An earlier baseline purchase-only Run")
    parser.add_argument("--full-case", action="store_true", help="Include the complete G1–G7 case")
    options = parser.parse_args()
    path = options.result
    result = asyncio.run(purchase_replay(options.variant, options.previous_run, options.full_case))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result["runId"], result["citationCount"])
