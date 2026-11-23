"""Admit immutable Run observations and publish a claim-bound citation manifest."""

import json
import re
from collections import Counter
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select

from compass_agent.context_runtime.citations import publish_citations as publish_citations
from compass_agent.context_runtime.citations import read_citations as read_citations
from compass_agent.context_runtime.delivery import Delivery
from compass_agent.context_runtime.profiles import search_pack
from compass_agent.context_runtime.search_claims import (
    CORE_OPERATIONS,
    Checked,
    DraftRevision,
    Proposal,
    SearchDraft,
    format_checked,
    leaf,
    render,
    target_gap,
)
from compass_agent.context_runtime.search_sql import display_columns, source_columns
from compass_agent.context_runtime.source_access import (
    allows,
    assistant_message,
    read_private,
    record_sources_locked,
    require_run_sources,
    require_search_sources,
    save_private,
)
from compass_agent.execution_context import execution_path
from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import AgentMessage
from compass_agent.runtime.private_state import digest
from compass_agent.schemas import RUN_STREAM_LIMITS, AnalysisFileNone, RunCompleted
from compass_agent.semantic.entities import SemanticMetricOffer


def search_draft_schema(revising=False, *, pack=None):
    if revising:
        from pydantic import TypeAdapter

        schema = TypeAdapter(SearchDraft | DraftRevision).json_schema(by_alias=True)
    else:
        schema = SearchDraft.model_json_schema(by_alias=True)
    schema["$defs"]["Proposal"]["properties"]["op"]["enum"] = [
        *CORE_OPERATIONS,
        *(pack or {}).get("proofOperations", ()),
    ]
    return schema


def _check_query_receipt(store, tx, run, actor, name, args, payload, pin, refs):
    if not payload.get("resultId") or not (
        name == "query_metric" or (name == "database_query" and args.get("op") == "query")
    ):
        return
    offer = tx.get(SemanticMetricOffer, UUID(payload["resultId"]))
    kind = "result" if name == "query_metric" else "open"
    if (
        not offer
        or offer.kind != kind
        or offer.run_id != run.id
        or offer.session_id != run.session_id
        or offer.owner_id != actor
    ):
        raise ValueError("查询回执不属于当前主体及本次运行")
    saved = store.private.open(run.id, kind, offer.private_payload)
    if kind == "open":
        confirmed = (
            saved["query"]["sql"] == args["sql"]
            and saved["revision"] == pin["connectionRevision"]
            and all(ref in refs for ref in saved["refs"])
            and saved["fingerprint"]
            == digest({k: payload[k] for k in ("columns", "rows", "truncated")})
        )
    else:
        source, plan = saved["source"], saved["plan"]
        confirmed = (
            source in refs
            and source["binding_id"] == payload.get("binding_id")
            and source["revision"] == payload.get("connection_revision")
            and source["version_id"] == payload.get("metric", {}).get("version_id")
            and plan["data_release"] == payload.get("data_release") == pin["dataRelease"]
            and saved["parameters"] == (payload.get("time_range") or [None])[0]
        )
    if not confirmed:
        raise ValueError("签收记录与当前来源、范围或返回内容不一致")


def _observations(name, args, payload, pin, run_id, refs):
    if name in {"search_documents", "read_document"}:
        for section in payload.get("sections", []):
            ref = section.get("sourceRef")
            text = section.get("text")
            if (
                not ref
                or ref.get("runId") != str(run_id)
                or ref not in refs
                or ref.get("collectionDigest") != pin.get("collectionDigest")
            ):
                raise ValueError("段落的资料包或登记来源不匹配")
            kind = section["kind"] if name == "read_document" else "candidate"
            if kind != "candidate" and (
                not isinstance(text, str)
                or sha256(text.encode()).hexdigest() != section["sectionHash"]
            ):
                raise ValueError("原段修订与摘要不匹配")
            tz = pin.get("timezone") or pin.get("timeZone") or ""
            cutoff = datetime.fromisoformat(pin["knownAt"])
            day = (
                cutoff.astimezone(ZoneInfo(tz)).date().isoformat()
                if tz
                else cutoff.date().isoformat()
            )
            yield {
                "kind": kind,
                "record": section,
                "title": section["title"],
                "version": str(section["version"]),
                "locator": " / ".join(
                    (section["docId"], section["sectionId"], section["sectionHash"])
                ),
                "scope": (
                    f"{section.get('issuer', '')} · {section.get('validFrom') or ''} 至 "
                    f"{section.get('validTo') or ''} · 截至 {pin['knownAt']}"
                ),
                "current": (not section.get("validFrom") or section["validFrom"][:10] <= day)
                and (not section.get("validTo") or day < section["validTo"][:10])
                and (not ref.get("businessDate") or ref["businessDate"][:10] == day),
                "timezone": tz,
                "knownAt": pin["knownAt"],
                "available": {},
            }
        return
    if name in {"search_semantics", "get_metric_definition", "resolve_semantics"} or (
        name == "database_query" and args.get("op") != "query"
    ):
        # Keep discovered IDs, schema and future query_ref in the next decision frame.
        # These are lookup aids. A published definition can only support its own text.
        definition = (
            name == "get_metric_definition"
            and payload.get("version_id")
            and any(
                r.get("kind") == "semantic" and r.get("version_id") == payload["version_id"]
                for r in refs
            )
        )
        if definition:
            lines = [
                payload[key]
                for key in ("summary", "formula", "event_time", "grain", "exceptions")
                if isinstance(payload.get(key), str) and payload[key]
            ]
            yield {
                "kind": "definition",
                "record": {**payload, "text": "\n".join(lines)},
                "title": payload["name"],
                "version": str(payload["version"]),
                "locator": payload["version_id"],
                "scope": pin["workspaceId"],
                "current": True,
                "available": {},
            }
        else:
            relation = f"{args.get('schema')}.{args.get('table')}"
            describes = (
                name == "database_query"
                and args.get("op") == "describe_table"
                and any(
                    r.get("kind") == "metadata" and relation in (r.get("columns") or {})
                    for r in refs
                )
            )
            yield {
                "kind": "hint",
                "record": payload,
                "title": "授权发现结果",
                "version": pin["collectionDigest"],
                "locator": str(
                    payload.get("query_ref") or payload.get("resultId") or digest([name, payload])
                ),
                "scope": pin["workspaceId"],
                "available": {},
                **({"describedRelation": relation} if describes else {}),
            }
            if name == "search_semantics":
                for entry in payload.get("items", []):
                    if entry.get("kind") == "entity" and any(
                        r.get("kind") == "semantic"
                        and r.get("version_id") == entry.get("version_id")
                        for r in refs
                    ):
                        yield {
                            "kind": "definition",
                            "record": {**entry, "text": entry["summary"]},
                            "title": entry["name"],
                            "version": str(entry["version"]),
                            "locator": entry["version_id"],
                            "scope": pin["workspaceId"],
                            "current": True,
                            "available": {},
                        }
        return
    if name not in {"database_query", "query_metric"} or not payload.get("resultId"):
        return
    columns, rows = payload.get("columns"), payload.get("rows")
    if (
        not isinstance(columns, list)
        or not isinstance(rows, list)
        or not all(len(row) == len(columns) for row in rows)
    ):
        raise ValueError("查询回执行列不完整")
    if name == "query_metric":
        metric = payload.get("metric") or {}
        if not (
            all(
                payload.get(k)
                for k in (
                    "binding_hash",
                    "manifest_hash",
                    "binding_id",
                    "source_scope_hash",
                    "time_range",
                    "as_of",
                )
            )
            and any(
                r.get("kind") == "semantic_result" and r.get("binding_id") == payload["binding_id"]
                for r in refs
            )
            and datetime.fromisoformat(payload["as_of"]) <= datetime.fromisoformat(pin["knownAt"])
        ):
            raise ValueError("正式指标缺少本轮有效执行回执")
        available = {c: c for c in columns}
        yield {
            "kind": "metric",
            "record": payload,
            "title": metric["name"],
            "version": str(metric["version"]),
            "scope": json.dumps(
                {
                    "workspace": payload["authorized_scope"],
                    "window": payload["time_range"],
                    "data": payload.get("data_scope"),
                    "asOf": payload["as_of"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "locator": payload["resultId"],
            "available": available,
        }
    elif args.get("op") == "query" and any(
        r.get("kind") in {"semantic_open", "retail", "metadata", "database_result"} for r in refs
    ):
        available, units = source_columns(args.get("sql", ""), columns)
        yield {
            "kind": "detail" if available else "hint",
            "sql": args.get("sql", ""),
            "knownAt": pin["knownAt"],
            "timezone": pin["timezone"],
            "record": payload,
            "title": "授权明细查询",
            "version": str(pin.get("connectionRevision", "")),
            "scope": f"截至 {pin['knownAt']} · {args.get('sql', '')}",
            "locator": payload["resultId"],
            "available": available,
            "units": units,
        }


def verify_batch(journal, step, results, trusted):
    """Called after genuine dispatch and before appending any tool message to the model."""
    pin = journal.context_binding["search"]
    if [c.id for c in step.tool_calls] != [r.call_id for r in results]:
        raise ValueError("工具批次的执行回执未逐项对应")
    with journal.store.transaction() as tx:
        _, run = journal.store.lock(tx, journal.run_id, trusted)
        journal.store.require_epoch(run, journal.epoch)
        key = f"search/batch/{step.index}"
        receipt_key = f"search/batch-receipt/{step.index}"
        receipt = digest(
            [
                str(step.id),
                pin,
                [
                    [c.id, c.function.name, c.function.arguments, r.status, r.cleanup]
                    for c, r in zip(step.tool_calls, results, strict=True)
                ],
            ]
        )
        previous = read_private(journal.store, tx, run.id, key)
        if previous is not None:
            signed = read_private(journal.store, tx, run.id, receipt_key)
            if signed is not None and signed != receipt:
                raise ModelError("delivery_conflict", 409)
            if signed is None:
                # Older accepted batches use the step, call and locator to derive each ID.
                calls = {c.id: c.function.name for c in step.tool_calls}
                if any(
                    calls.get(item["callId"]) != item["tool"]
                    or item["evidenceId"]
                    != str(uuid5(run.id, f"evidence:{step.id}:{item['callId']}:{item['locator']}"))
                    for item in previous
                ):
                    raise ModelError("delivery_conflict", 409)
            refs = list(assistant_message(tx, run.id).source_refs or [])
            require_search_sources(journal, trusted, refs=refs, epoch=journal.epoch)
            return previous
        incoming = [
            section["sourceRef"]
            for call, result in zip(step.tool_calls, results, strict=True)
            if call.function.name in {"search_documents", "read_document"}
            and result.status == "ok"
            and isinstance(result.payload, dict)
            for section in result.payload.get("sections", [])
            if isinstance(section, dict) and "sourceRef" in section
        ]
        record_sources_locked(journal.store, tx, run, incoming, journal.epoch)
        message = assistant_message(tx, run.id)
        refs = list(message.source_refs or [])
        if not allows(journal.store, trusted, refs, epoch=journal.epoch):
            raise ModelError("source_access_denied", 403)
        choices = []
        for call, result in zip(step.tool_calls, results, strict=True):
            if (
                result.status not in {"ok", "empty"}
                or result.cleanup == "unconfirmed"
                or not isinstance(result.payload, dict)
            ):
                continue
            name = call.function.name
            args = json.loads(call.function.arguments)
            _check_query_receipt(
                journal.store, tx, run, trusted.actor_user_id, name, args, result.payload, pin, refs
            )
            for evidence in _observations(name, args, result.payload, pin, run.id, refs):
                identifier = str(
                    uuid5(run.id, f"evidence:{step.id}:{call.id}:{evidence['locator']}")
                )
                evidence.update(
                    stepId=str(step.id),
                    callId=call.id,
                    inputDigest=digest([name, args, pin]),
                    snapshotDigest=digest(pin),
                    contentDigest=digest(evidence["record"]),
                )
                summary = {
                    "evidenceId": identifier,
                    "sourceKey": f"e{step.index}_{len(choices) + 1}",
                    "tool": name,
                    "callId": call.id,
                    "kind": evidence["kind"],
                    "locator": evidence["locator"],
                }
                rec = evidence["record"]
                if "rows" in rec:
                    summary.update(
                        columns=list(evidence["available"]),
                        rowCount=len(rec["rows"]),
                        truncated=bool(rec.get("truncated")),
                    )
                    if evidence.get("units"):
                        summary["columnUnits"] = evidence["units"]
                    if evidence["kind"] == "metric":
                        summary["columnUnits"] = {
                            c: "count" if c in {"numerator", "denominator"} else rec["unit"]
                            for c in (
                                "value",
                                "current_value",
                                "comparison_value",
                                "current_minus_comparison",
                                "numerator",
                                "denominator",
                            )
                            if c in evidence["available"]
                        }
                if "sectionHash" in rec:
                    summary.update(
                        sectionHash=rec["sectionHash"],
                        documentRef=rec["ref"],
                        title=evidence["title"],
                    )
                if evidence["kind"] == "definition":
                    summary["title"] = evidence["title"]
                if evidence.get("describedRelation"):
                    summary["describedRelation"] = evidence["describedRelation"]
                save_private(
                    journal.store, tx, run, "search/evidence/" + identifier, evidence, refs
                )
                choices.append(summary)
        save_private(journal.store, tx, run, key, choices, refs)
        save_private(journal.store, tx, run, receipt_key, receipt, refs)
        return choices


def comparison_delivery(journal, step, text, trusted, deliverable):
    from compass_agent.context_runtime.comparison_brief import ComparisonBrief, render_brief

    draft = ComparisonBrief.model_validate_json(text or "")
    require_run_sources(journal.store, journal.run_id, trusted, epoch=journal.epoch)
    keys = {
        item.get("sourceKey", identifier): identifier
        for identifier, item in journal.search_progress.evidence.items()
    }
    with journal.store.transaction() as tx:
        _, run = journal.store.lock(tx, journal.run_id, trusted)
        journal.store.require_epoch(run, journal.epoch)

        def lookup(ref):
            identifier = keys.get(ref, ref)
            if identifier not in journal.search_progress.evidence:
                raise ValueError(f"本轮没有签收来源 {ref}")
            item = read_private(journal.store, tx, run.id, "search/evidence/" + identifier)
            if not item or item["kind"] in {"candidate", "hint"}:
                raise ValueError("请引用实际读过的段落或查询回执")
            return item

        body, manifest = render_brief(run.id, draft, lookup)
        refs = list(assistant_message(tx, run.id).source_refs or [])
        save_private(
            journal.store,
            tx,
            run,
            "search/draft/" + str(step.id),
            {"comparisonBrief": draft.model_dump(), "manifest": manifest},
            refs,
        )
    result = RunCompleted(
        status="completed", finalAnswer=body, file=AnalysisFileNone(status="none")
    )
    result._citations = manifest
    if deliverable == "business_report":
        result._delivery = Delivery(
            f"{execution_path.get()}/model:{step.id}",
            "report",
            "report.md",
            "text/markdown",
            body.encode(),
        )
    return result


def verify_draft(journal, step, text, trusted, deliverable, *, checkpoint=False, scene=None):
    pack = search_pack((journal.context_binding or {}).get("search"), scene)
    if (
        not checkpoint
        and (journal.context_binding.get("search") or {}).get("reportFormat") == "comparison_brief"
    ):
        return comparison_delivery(journal, step, text, trusted, deliverable)
    raw = (text or "").strip()
    # Some providers emit their accepted plan object immediately before the draft.
    # Consume only a complete, typed plan. All published data still comes from the draft.
    if raw.startswith("{"):
        try:
            leading, end = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(leading, dict) and set(leading) == {"targets"} and raw[end:].strip():
                from compass_agent.context_runtime.search_control import parse_targets

                if parse_targets(raw[:end]):
                    raw = raw[end:].lstrip()
    progress = getattr(journal, "search_progress", None)
    try:
        proposal = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"草稿 JSON 在第 {exc.lineno} 行第 {exc.colno} 列不完整") from exc
    if isinstance(proposal, dict) and proposal.get("revise") is True:
        if not progress or progress.previous_draft is None:
            raise ValueError("请先提交本次运行的完整草稿")
        draft = DraftRevision.model_validate(proposal).apply(progress.previous_draft)
    else:
        draft = SearchDraft.model_validate(proposal)
    if progress:
        # Retain model proposals only inside this Run. Each publication below reopens
        # the original receipts and evaluates all claims under the live permissions.
        progress.previous_draft = draft
    aliases = {
        entry["sourceKey"]: identifier
        for identifier, entry in (progress.evidence.items() if progress else ())
        if entry.get("sourceKey")
    }
    if aliases:
        draft = draft.model_copy(
            update={
                "claim_proposals": [
                    claim.model_copy(update={"evidence_id": aliases[claim.evidence_id]})
                    if claim.evidence_id and claim.evidence_id in aliases
                    else claim
                    for claim in draft.claim_proposals
                ]
            }
        )
    require_run_sources(journal.store, journal.run_id, trusted, epoch=journal.epoch)
    with journal.store.transaction() as tx:
        _, run = journal.store.lock(tx, journal.run_id, trusted)
        journal.store.require_epoch(run, journal.epoch)
        records = {}
        for claim in draft.claim_proposals:
            if claim.evidence_id:
                key = claim.evidence_id
                if key not in records:
                    records[key] = read_private(journal.store, tx, run.id, "search/evidence/" + key)
                if records[key] is None:
                    raise ValueError(f"断言 {claim.key} 的来源键不在本次运行已签收的观察中")
        dictionaries = []
        described = {}
        if progress:
            for key, entry in progress.evidence.items():
                if entry.get("kind") == "definition":
                    definition = records.get(key) or read_private(
                        journal.store, tx, run.id, "search/evidence/" + key
                    )
                    if definition is not None:
                        dictionaries.append(definition)
                if entry.get("describedRelation"):
                    schema = read_private(journal.store, tx, run.id, "search/evidence/" + key)
                    if schema:
                        relation = schema["describedRelation"]
                        rows = schema["record"]
                        for row in rows.get("rows", []):
                            col = dict(zip(rows["columns"], row, strict=True))
                            if col.get("column_name") and col.get("description"):
                                field = relation + "." + col["column_name"]
                                described[field] = {
                                    "description": col["description"],
                                    "source": {
                                        "kind": "detail",
                                        "title": "授权数据字典",
                                        "version": schema["version"],
                                        "scope": schema["scope"],
                                        "locator": schema["locator"] + " · " + field,
                                        "excerpt": field + "：" + col["description"],
                                    },
                                }
        for evidence in records.values():
            if evidence["kind"] == "metric":
                evidence["dictionaries"] = dictionaries
            elif evidence["kind"] == "detail":
                evidence["columnDictionary"] = described
                evidence["dictionaries"] = dictionaries
                evidence["displayColumns"] = display_columns(
                    evidence.get("sql"), evidence["available"], described
                )
        body, manifest = render(
            run.id,
            draft,
            records,
            pack=pack,
            progress=None if checkpoint else progress,
            requested=[]
            if checkpoint
            else (getattr(journal, "registry_initial_context", None) or {}).get(
                "requested_metrics", []
            ),
        )
        if len(body.encode()) > RUN_STREAM_LIMITS["answerBytes"]:
            raise ValueError("正文超过交付长度，请缩短引文")
        refs = list(assistant_message(tx, run.id).source_refs or [])
        save_private(
            journal.store,
            tx,
            run,
            ("search/claims/" if checkpoint else "search/draft/") + str(step.id),
            {"proposals": draft.model_dump(mode="json", by_alias=True), "manifest": manifest},
            refs,
        )
    if checkpoint and progress:
        previous = set(progress.claim_feedback.get("verifiedClaims", {}))
        verified = {d["claimId"]: d["status"] for d in manifest["details"].values()}
        progress.claim_feedback = {
            "checkedBlocks": draft.answer_blocks,
            "verifiedClaims": verified,
            "changedClaims": [key for key in verified if key not in previous],
        }
        # A terminal partial may reuse factual work from this checkpoint only while
        # its observation set is still current. Action proposals require final review.
        progress.verified_partial = None
        if not any(p.op in pack.get("actionOperations", ()) for p in draft.claim_proposals):
            progress.verified_partial = (
                len(progress.evidence),
                [
                    Checked(
                        d["statement"],
                        tuple(d["sources"]),
                        d["status"],
                        calculation=d["calculation"],
                    )
                    for d in manifest["details"].values()
                ],
            )
    response = RunCompleted(
        status="completed", finalAnswer=body, file=AnalysisFileNone(status="none")
    )
    response._citations = manifest
    if deliverable == "business_report" and not checkpoint:
        response._delivery = Delivery(
            f"{execution_path.get()}/model:{step.id}",
            "report",
            "report.md",
            "text/markdown",
            body.encode(),
        )
    return response


def _partial_rows(evidence):
    kind, rec = evidence["kind"], evidence["record"]
    if kind in {"history", "knowledge", "external"}:
        if rec.get("heading") == "发布说明":
            return
        for match in re.finditer(r"[^。！？\n]+[。！？]?", rec["text"]):
            sentence = match.group().strip()
            if (
                not 4 <= len(sentence) <= 200
                or sentence.startswith("|")
                or rec["text"].count(sentence) != 1
            ):
                continue
            item = leaf(Proposal(key="source", op="quote", quote=sentence), evidence)
            yield replace(
                item, text=f"《{evidence['title']}》{rec.get('heading', '')}记载“{item.text}”"
            )
            return
        return
    if kind not in {"metric", "detail"}:
        return
    columns = evidence["available"]
    if kind == "metric":
        selected = (
            ["value"]
            if "value" in columns
            else [c for c in ("current_value", "comparison_value") if c in columns]
        )
    else:
        numeric = [col for col in columns if evidence.get("units", {}).get(col)]
        selected = numeric[:3] or list(columns)[:2]
    for i in range(min(8, len(rec.get("rows", [])))):
        for col in selected:
            item = leaf(Proposal(key="source", op="cell", row=i, column=col), evidence)
            if item.status in {"supported", "insufficient"} and len(item.text) <= 500:
                yield item


_MISSING_SOURCE = {
    "semantic": "正式指标回执仍待补充，经营口径与指标判断待核。",
    "detail": "业务明细仍待查，相关事实及其发生时间待核。",
    "knowledge": "适用制度仍待补充，资格与执行条件待核。",
    "history": "历史记录仍待补充，先前约定与负责人待核。",
    "external": "外部原件仍待补充，对方给出的条件及适用时限待核。",
}


def partial_delivery(journal, trusted, step=None, deliverable=None):
    """Publish bounded source facts already opened by the model under current access."""
    require_run_sources(journal.store, journal.run_id, trusted, epoch=journal.epoch)
    with journal.store.transaction() as tx:
        _, run = journal.store.lock(tx, journal.run_id, trusted)
        journal.store.require_epoch(run, journal.epoch)
        progress = getattr(journal, "search_progress", None)
        partial = getattr(progress, "verified_partial", None)
        ready = partial[1] if progress and partial and partial[0] == len(progress.evidence) else []
        counts = Counter()
        facts = {v.text: v for v in ready[:12]}
        purposes = [f"search/batch/{i}" for i in range(1, RUN_STREAM_LIMITS["steps"] + 1)]
        keys = {uuid5(run.id, "context:" + purpose): purpose for purpose in purposes}
        batches = tx.scalars(
            select(AgentMessage)
            .where(AgentMessage.id.in_(keys.keys()))
            .order_by(AgentMessage.ordinal.desc())
        )
        for batch in batches:
            for item in journal.store.private.open(run.id, keys[batch.id], batch.private_content):
                kind = item["kind"]
                quota = 8 if kind in {"metric", "detail"} else 2
                if (
                    kind not in {"metric", "detail", "history", "knowledge", "external"}
                    or counts[kind] >= quota
                ):
                    continue
                rec = read_private(
                    journal.store, tx, run.id, "search/evidence/" + item["evidenceId"]
                )
                if rec is None:
                    continue
                for checked in _partial_rows(rec):
                    if checked.text in facts:
                        continue
                    facts[checked.text] = checked
                    counts[kind] += 1
                    if counts[kind] >= quota:
                        break
        open_kinds = (
            {t.source for t in progress.targets.values() if t.required} if progress else set()
        )
        missing = [
            text
            for kind, text in _MISSING_SOURCE.items()
            if kind in open_kinds and not counts["metric" if kind == "semantic" else kind]
        ]
        items = [
            *facts.values(),
            *(target_gap(t) for t in (progress.targets.values() if progress else ()) if t.required),
            *(Checked(text, (), "insufficient") for text in missing),
            Checked("当前取证未完成，其他条件待核。", (), "insufficient"),
        ]
        body, manifest = format_checked(run.id, items, byte_limit=RUN_STREAM_LIMITS["answerBytes"])
    result = RunCompleted(
        status="completed", finalAnswer=body, file=AnalysisFileNone(status="none")
    )
    result._citations = manifest
    if deliverable == "business_report" and step is not None:
        result._delivery = Delivery(
            f"{execution_path.get()}/model:{step.id}",
            "report",
            "report.md",
            "text/markdown",
            body.encode(),
        )
    return result
