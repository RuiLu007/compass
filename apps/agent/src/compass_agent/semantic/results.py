"""Owner decisions on retained query receipts, shared by Chat and the tool continuation."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select

from compass_agent.context_runtime.source_access import allows
from compass_agent.models.errors import ModelError
from compass_agent.semantic import client
from compass_agent.semantic.draft_fields import confirmed_fields
from compass_agent.semantic.entities import SemanticMetricOffer
from compass_agent.semantic.extraction import receipt_proposal


def visible(store, trusted, row, data, *, epoch=None):
    return (
        row.owner_id == trusted.actor_user_id
        and row.expires_at > datetime.now(UTC)
        and allows(store, trusted, data["refs"], epoch=epoch)
    )


MAX_CARD_ITEMS = 3


def invitation_eligibility(results, store, trusted, run_id, *, surface_errors=False):
    """Recheck only model-nominated definitions against the owner's current catalog."""
    candidates = [
        (ref, data["definition_hash"], data["recommendation"]["fields"])
        for ref, status, data in results
        if data.get("recommendation") and status in {"active", "confirmed"}
    ][:MAX_CARD_ITEMS]
    if not candidates:
        return set()
    try:
        found = client.request(
            store.source_settings,
            trusted,
            run_id,
            "equivalents",
            {
                "hashes": [key for _, key, _ in candidates],
                "candidates": [
                    {
                        "hash": key,
                        "name": f["name"],
                        "formula": f["formula"],
                        "summary": f["summary"],
                    }
                    for _, key, f in candidates
                ],
            },
        )
    except ModelError:
        if surface_errors:
            raise
        return set()
    return {ref for ref, key, _ in candidates if key not in found["hashes"]}


def nominate(service, args):
    fields = args.get("fields") or {}
    reason = (args.get("reuse_reason") or "").strip()
    if (
        not args.get("result_ref")
        or not reason
        or not all(fields.get(k) for k in ("name", "domain", "summary", "formula", "unit"))
    ):
        return {"status": "incomplete_definition"}
    ref, data = service.open_result(args["result_ref"])
    run_id = service.journal.run_id
    if not data.get("complete", data.get("reusable")) or not data.get("definition_hash"):
        return {"status": "incomplete_result"}
    if not allows(service.store, service.trusted, data["refs"], epoch=service.journal.epoch):
        raise ModelError("source_access_denied", 403)
    from compass_agent.semantic.draft_fields import result_preview

    recommendation = {"fields": result_preview({}, fields, ref), "reuse_reason": reason}
    candidate = {**data, "recommendation": recommendation}
    if ref not in invitation_eligibility(
        [(ref, "active", candidate)], service.store, service.trusted, run_id, surface_errors=True
    ):
        return {"status": "existing_definition"}
    with service.store.transaction() as tx:
        _, run = service.store.lock(tx, run_id, service.trusted)
        service.store.require_epoch(run, service.journal.epoch)
        rows = tx.scalars(
            select(SemanticMetricOffer).where(
                SemanticMetricOffer.run_id == run_id,
                SemanticMetricOffer.owner_id == service.trusted.actor_user_id,
                SemanticMetricOffer.kind == "open",
            )
        ).all()
        target = None
        count = 0
        for row in rows:
            saved = service.store.private.open(run_id, "open", row.private_payload)
            existing = saved.get("recommendation")
            if str(row.offer_id) == ref and row.status == "active":
                target = row
                if existing == recommendation:
                    return {"status": "recommended"}
            if existing:
                count += 1
                if (
                    saved.get("definition_hash") == data["definition_hash"]
                    or existing["fields"]["name"].strip().casefold()
                    == fields["name"].strip().casefold()
                    or all(
                        existing["fields"][key] == fields[key]
                        for key in ("summary", "formula", "unit")
                    )
                ):
                    return {"status": "already_considered"}
        if target is None or count >= MAX_CARD_ITEMS:
            return {"status": "not_recommended"}
        target.private_payload = service.store.private.seal(run_id, "open", candidate)
    return {"status": "recommended"}


def project(row, data, *, reusable=None):
    return {
        "result_ref": row.offer_id,
        "proposal": {
            **receipt_proposal(data),
            **({"saved_name": data["saved_name"]} if data.get("saved_name") else {}),
        },
        "created_at": datetime.fromisoformat(data["created_at"]),
        "columns": data["columns"],
        "status": row.status,
        "reusable": (bool(data.get("recommendation")) and row.status in {"active", "confirmed"})
        if reusable is None
        else reusable,
        "draft_id": UUID(data["saved"]["asset_id"]) if data.get("saved") else None,
    }


def list_results(store, trusted, run_id):
    run = store.read_run(run_id, trusted)
    if run.status != "completed" or not run.config_snapshot.get("contextRuntime", {}).get(
        "registry"
    ):
        return {"items": []}
    with store.transaction() as tx:
        rows = tx.scalars(
            select(SemanticMetricOffer)
            .where(
                SemanticMetricOffer.run_id == run_id,
                SemanticMetricOffer.kind == "open",
                SemanticMetricOffer.owner_id == trusted.actor_user_id,
                SemanticMetricOffer.expires_at > datetime.now(UTC),
            )
            .order_by(SemanticMetricOffer.expires_at)
        ).all()
        results = [
            (row, store.private.open(row.run_id, "open", row.private_payload)) for row in rows
        ]
    results = [
        (row, data)
        for row, data in results
        if (data.get("recommendation") or row.status == "saved")
        and row.status != "dismissed"
        and visible(store, trusted, row, data)
    ]
    eligible = invitation_eligibility(
        [(str(row.offer_id), row.status, data) for row, data in results], store, trusted, run_id
    )
    return {
        "items": [
            project(row, data, reusable=str(row.offer_id) in eligible)
            for row, data in results
            if row.status == "saved" or str(row.offer_id) in eligible
        ]
    }


def decide_result(store, trusted, run_id, choice, *, for_tool=False, action_id=None, epoch=None):
    with store.transaction() as tx:
        _, run = store.lock(tx, run_id, trusted)
        if for_tool:
            store.require_epoch(run, epoch)
        if not run.config_snapshot.get("contextRuntime", {}).get("registry") or (
            not for_tool and run.status != "completed"
        ):
            raise ModelError("not_found", 404)
        row = tx.scalar(
            select(SemanticMetricOffer)
            .where(
                SemanticMetricOffer.offer_id == choice.result_ref,
                SemanticMetricOffer.kind == "open",
                SemanticMetricOffer.session_id == run.session_id,
                SemanticMetricOffer.owner_id == trusted.actor_user_id,
            )
            .with_for_update()
        )
        if not row or (not for_tool and row.run_id != run_id):
            raise ModelError("not_found", 404)
        data = store.private.open(row.run_id, "open", row.private_payload)
        if not visible(store, trusted, row, data, epoch=run.execution_epoch):
            raise ModelError("source_access_denied", 403)
        if choice.action == "save":
            if row.status == "dismissed" and not for_tool:
                raise ModelError("proposal_conflict", 409)
            from compass_agent.runtime.private_state import digest

            fields = choice.fields
            if not fields:
                fields = (data.get("recommendation") or {}).get("fields")
                if not fields or for_tool:
                    raise ModelError("invalid_parameters", 422)
                if row.status != "saved" and str(row.offer_id) not in invitation_eligibility(
                    [(str(row.offer_id), row.status, data)],
                    store,
                    trusted,
                    run_id,
                    surface_errors=True,
                ):
                    raise ModelError("proposal_conflict", 409)
            if for_tool:
                # Older approved cards may contain additional provider-specific labels.
                fields = confirmed_fields(
                    receipt_proposal(data).get("fields", {}), fields, row.offer_id
                )
            fingerprint = digest(fields)
            if row.status == "saved":
                if data.get("fields_digest") != fingerprint:
                    raise ModelError("proposal_conflict", 409)
            payload = {
                "result_ref": str(row.offer_id),
                "columns": data["columns"],
                "captured_at": data["created_at"],
                "fingerprint": data["fingerprint"],
                **(
                    {"definition_hash": data["definition_hash"]}
                    if data.get("definition_hash")
                    else {}
                ),
                "fields": {**fields, "request_key": str(uuid5(row.offer_id, "save"))},
            }
            action = None
            if action_id:
                from compass_agent.runtime.entities import AgentAction

                action = tx.get(AgentAction, action_id, with_for_update=True)
                if not action or action.run_id != run.id:
                    raise ModelError("proposal_conflict", 409)
            saved = client.request(store.source_settings, trusted, run_id, "result-draft", payload)
            data.update(saved=saved, saved_name=fields.get("name"), fields_digest=fingerprint)
            row.private_payload = store.private.seal(row.run_id, "open", data)
            row.status = "saved"
            if action:
                action.state = "applied"
                action.receipt = {"asset_id": saved["asset_id"], "version_id": saved["version_id"]}
                action.updated_at = datetime.now(UTC)
            return saved if for_tool else project(row, data)
        if choice.fields is not None:
            raise ModelError("invalid_parameters", 422)
        if row.status == "saved" or (row.status == "dismissed" and choice.action == "confirm"):
            raise ModelError("proposal_conflict", 409)
        row.status = "confirmed" if choice.action == "confirm" else "dismissed"
        result = project(row, data)
    if choice.action == "confirm":
        for item in list_results(store, trusted, run_id)["items"]:
            if item["result_ref"] == choice.result_ref:
                return item
        result["reusable"] = False
    return result


def dismiss_proposal(tx, store, run, private):
    if private.get("kind") != "semantic_draft":
        return
    ref = private.get("result_ref") or private.get("args", {}).get("result_ref")
    if ref:
        row = tx.get(SemanticMetricOffer, UUID(ref), with_for_update=True)
        if (
            row
            and row.session_id == run.session_id
            and row.kind == "open"
            and row.status != "saved"
        ):
            row.status = "dismissed"
