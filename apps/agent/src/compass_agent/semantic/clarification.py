"""Owner clarification for a single registered term in a numeric request."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid5

from compass_agent.runtime.private_state import digest
from compass_agent.schemas import AnalysisFileNone, RunCompleted, RunWaiting, ToolApprovalProposal
from compass_agent.semantic.draft_fields import authored_preview
from compass_agent.semantic.entities import SemanticMetricOffer


def options(metrics):
    return [
        {
            "id": m["asset_id"],
            "versionId": m["version_id"],
            "name": m["name"],
            "description": (
                m["summary"][:300]
                + (" · 仅定义，暂无可用绑定" if m.get("executable") is False else "")
                + (f" · 归期字段 {m['event_time']}" if m.get("event_time") else "")
            )[:400],
            "unit": m["unit"],
            "version": m["version"],
        }
        for m in metrics
    ]


async def continue_selected_result(service):
    """The owner's confirmed selection determines the next form and its eventual save."""
    from uuid import UUID

    from compass_agent.context_runtime.source_access import allows
    from compass_agent.models.errors import ModelError
    from compass_agent.schemas import SemanticResultDecision
    from compass_agent.semantic.results import decide_result

    journal = service.journal
    approvals = journal.approved_proposals
    revision = next((a for a in reversed(approvals) if a.get("kind") == "semantic_revision"), None)
    if revision:
        from compass_agent.semantic.revision import save_approved_revision

        await asyncio.to_thread(save_approved_revision, service, revision)
        return RunCompleted(
            status="completed",
            finalAnswer="修订已保存到原指标。可在「数据与语义 → 指标口径」查看最新草稿。",
            file=AnalysisFileNone(status="none"),
        )
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
    original_args = choice.get("args", {})
    suggested = original_args.get("fields") if original_args.get("result_ref") == ref else None
    fields = authored_preview(data, suggested, ref)
    if not fields:
        # Return the approved source to the model so it can explain the calculation.
        return None
    scopes = await service.api("scopes")
    if not scopes["can_create_personal"] and not any(s["manage"] for s in scopes["items"]):
        raise ModelError("forbidden", 403)
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
    elif private.get("kind") == "semantic_choice":
        kind, key = "choice", private["args"]["mention"].strip().casefold()
        if private.get("customDefinition"):
            payload = {"customDefinition": private["customDefinition"]}
        else:
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
