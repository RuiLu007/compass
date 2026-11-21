"""Owner clarification for a single registered term in a numeric request."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid5

from compass_agent.runtime.private_state import digest
from compass_agent.schemas import AnalysisFileNone, RunCompleted, RunWaiting, ToolApprovalProposal
from compass_agent.semantic.draft_fields import result_preview
from compass_agent.semantic.entities import SemanticMetricOffer


def options(metrics):
    return [
        {
            "id": m["asset_id"],
            "versionId": m["version_id"],
            "name": m["name"],
            "description": m["summary"][:300],
            "unit": m["unit"],
            "version": m["version"],
        }
        for m in metrics
    ]


def pending_queries(journal, steps):
    """Compare explicitly requested metrics with the observations of this Run."""
    requested = (getattr(journal, "registry_initial_context", {}) or {}).get(
        "requested_metrics", []
    )
    if selected := getattr(journal, "registry_selected", None):
        requested = [selected]
    actual, plans = set(), {}
    unavailable = False
    for step in steps:
        for call, output in zip(step.tool_calls, step.tool_results, strict=False):
            name = call.function.name
            if output.status != "ok" or name not in {"resolve_semantics", "query_metric"}:
                continue
            data = json.loads(output.observation_json)
            metric = (data.get("metric") or {}).get("asset_id")
            if name == "query_metric" and data.get("resultId"):
                actual.add(metric)
            if name == "resolve_semantics" and metric and data.get("query_ref"):
                plans[metric] = data["query_ref"]
            if data.get("status") in {"unavailable_binding", "invalid_dimension", "no_match"}:
                unavailable = True
    missing = [
        {"name": m["name"], "code": m.get("code"), "query_ref": plans.get(m["asset_id"])}
        for m in requested
        if m["asset_id"] not in actual
    ]
    if not missing and (actual or unavailable or requested):
        return None
    return {
        "missing": missing,
        "instruction": (
            "数值结论还缺少正式查询回执。对照原问题的日期、分组和筛选，"
            "用 resolve_semantics 解析所需口径并填入 parameters；"
            "已有合适的 query_ref 时调用 query_metric。"
        ),
    }


async def check(service, question):
    journal = service.journal
    data = await service.api("context", {"question": question[:2400]})
    journal.registry_initial_context = data
    resolution = data.get("resolution") or {}
    mention = resolution.get("mention")
    if not mention:
        return None
    if resolution["status"] == "unavailable_binding":
        return RunCompleted(
            status="completed",
            finalAnswer="当前账户没有这个指标的可执行绑定或查询权限，无法取得该期间的数值。",
            file=AnalysisFileNone(status="none"),
        )
    if resolution["status"] == "resolved":
        journal.registry_selected = resolution["metric"]
        journal.registry_family = resolution.get("family", [])
        return None
    if resolution["status"] != "needs_clarification":
        return None
    args = {"mention": mention}
    result = await service.api("resolve", args)
    if result["status"] != "needs_clarification":
        return None
    authorized = options(result["options"])
    if len(authorized) > 32:
        return RunCompleted(
            status="completed",
            finalAnswer="可选口径较多，请补充指标所属部门、业务域或具体名称，再选择要查询的版本。",
            file=AnalysisFileNone(status="none"),
        )
    previous = await asyncio.to_thread(service.choice, mention)
    selected = next(
        (
            m
            for m in result["options"]
            if previous
            and m["asset_id"] == previous["asset_id"]
            and m["version_id"] == previous["version_id"]
        ),
        None,
    )
    if selected:
        journal.registry_selected = selected
        journal.registry_family = [m["id"] for m in authorized]
        return None
    call_id = str(uuid5(journal.run_id, "registry-clarification:" + mention))
    proposal_id = uuid5(journal.run_id, digest(authorized) + call_id)
    proposal = ToolApprovalProposal(
        proposalId=str(proposal_id),
        toolName="resolve_semantics",
        toolCallId=call_id,
        argumentsDigest=digest(args),
        summary="请选择本次查询使用的指标口径。",
    )
    journal.pending_proposal = {
        "kind": "semantic_choice",
        "origin": "registry",
        "args": args,
        "options": authorized,
        "toolName": "resolve_semantics",
        "callId": call_id,
        "stepId": call_id,
        "proposal": proposal.model_dump(mode="json", by_alias=True),
    }
    return RunWaiting(
        status="waiting",
        runId=str(journal.run_id),
        waitId=str(uuid5(proposal_id, "wait")),
        proposal=proposal,
    )


async def continue_selected_result(service):
    """The owner's confirmed selection determines the next form and its eventual save."""
    from uuid import UUID

    from compass_agent.context_runtime.source_access import allows
    from compass_agent.models.errors import ModelError
    from compass_agent.schemas import SemanticResultDecision
    from compass_agent.semantic.results import decide_result

    journal = service.journal
    approvals = journal.approved_proposals
    save = next((a for a in reversed(approvals) if a.get("kind") == "semantic_draft"), None)
    if save:
        ref = save.get("result_ref") or save["args"]["result_ref"]
        await asyncio.to_thread(
            decide_result,
            service.store,
            service.trusted,
            journal.run_id,
            SemanticResultDecision(result_ref=UUID(ref), action="save", fields=save["fields"]),
            for_tool=True,
            action_id=UUID(save["actionId"]),
            epoch=journal.epoch,
        )
        return RunCompleted(
            status="completed",
            finalAnswer="已将「" + save["fields"]["name"] + "」保存为指标草稿。",
            file=AnalysisFileNone(status="none"),
        )
    choice = next(
        (a for a in reversed(approvals) if a.get("kind") == "semantic_result_choice"), None
    )
    if not choice:
        return None
    ref = choice["choiceId"]
    _, data = await asyncio.to_thread(service.open_result, ref)
    if not await asyncio.to_thread(
        allows, service.store, service.trusted, data["refs"], epoch=journal.epoch
    ):
        raise ModelError("source_access_denied", 403)
    scopes = await service.api("scopes")
    if not scopes["can_create_personal"] and not any(s["manage"] for s in scopes["items"]):
        raise ModelError("forbidden", 403)
    fields = result_preview(data.get("proposal", {}).get("fields", {}), {}, ref)
    call_id = str(uuid5(journal.run_id, "selected-result:" + ref))
    args = {"result_ref": ref}
    proposal = ToolApprovalProposal(
        proposalId=call_id,
        toolName="discover_semantic_candidates",
        toolCallId=call_id,
        argumentsDigest=digest(args),
        summary="请核对所选查询的口径、归属和缺项，确认后保存草稿。",
    )
    journal.pending_proposal = {
        "kind": "semantic_draft",
        "origin": "selected-result",
        "fields": fields,
        "result_ref": ref,
        "args": args,
        "toolName": "discover_semantic_candidates",
        "callId": call_id,
        "stepId": call_id,
        "proposal": proposal.model_dump(mode="json", by_alias=True),
    }
    return RunWaiting(
        status="waiting",
        runId=str(journal.run_id),
        waitId=str(uuid5(journal.run_id, call_id)),
        proposal=proposal,
    )


def retain_selection(tx, store, session, run, private):
    if private.get("kind") == "semantic_result_choice":
        kind, key = "selection", "open"
        payload = {"result_ref": private["choiceId"]}
    elif private.get("origin") == "registry" and private.get("kind") == "semantic_choice":
        kind, key = "choice", private["args"]["mention"].strip().casefold()
        choice = next(o for o in private["options"] if o["id"] == private["choiceId"])
        payload = {"asset_id": choice["id"], "version_id": choice["versionId"]}
    else:
        return None
    ref = uuid5(run.id, kind + ":" + key)
    if not tx.get(SemanticMetricOffer, ref):
        tx.add(
            SemanticMetricOffer(
                offer_id=ref,
                session_id=session.id,
                run_id=run.id,
                owner_id=session.owner_user_id,
                kind=kind,
                status="active",
                expires_at=datetime.now(UTC) + timedelta(days=1),
                private_payload=store.private.seal(run.id, kind, payload),
            )
        )
    return {"selection": str(ref)}
