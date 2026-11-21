"""Owner decisions on retained query receipts, shared by Chat and the tool continuation."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select

from compass_agent.context_runtime.source_access import allows
from compass_agent.models.errors import ModelError
from compass_agent.semantic import client
from compass_agent.semantic.draft_fields import confirmed_fields
from compass_agent.semantic.entities import SemanticMetricOffer


def visible(store, trusted, row, data, *, epoch=None):
    return (
        row.owner_id == trusted.actor_user_id
        and row.expires_at > datetime.now(UTC)
        and allows(store, trusted, data["refs"], epoch=epoch)
    )


def invitation_eligibility(results, store, trusted, run_id):
    """A confirmed calculation may invite once per equivalent source and definition."""
    hashes = {data.get("definition_hash") for _, _, data in results} - {None}
    known = set()
    if hashes:
        try:
            keys = sorted(hashes)
            for offset in range(0, len(keys), 100):
                found = client.request(
                    store.source_settings,
                    trusted,
                    run_id,
                    "equivalents",
                    {"hashes": keys[offset : offset + 100]},
                )
                known.update(found["hashes"])
        except ModelError:
            # Explicit save is available while an equivalent definition cannot be checked.
            return set()
    known.update(
        data.get("definition_hash") or ref
        for ref, status, data in results
        if status in {"dismissed", "saved"}
    )
    choices = set()
    for ref, _, data in sorted(results, key=lambda item: item[1] != "confirmed"):
        key = data.get("definition_hash") or ref
        if data.get("reusable") and key not in known:
            choices.add(ref)
            known.add(key)
    return choices


def project(row, data, *, reusable=None):
    return {
        "result_ref": row.offer_id,
        "proposal": data.get("proposal", {}),
        "created_at": datetime.fromisoformat(data["created_at"]),
        "columns": data["columns"],
        "status": row.status,
        "reusable": data.get("reusable", False) if reusable is None else reusable,
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
    results = [(row, data) for row, data in results if visible(store, trusted, row, data)]
    eligible = invitation_eligibility(
        [(str(row.offer_id), row.status, data) for row, data in results], store, trusted, run_id
    )
    return {
        "items": [
            project(row, data, reusable=str(row.offer_id) in eligible) for row, data in results
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
            if not choice.fields:
                raise ModelError("invalid_parameters", 422)
            from compass_agent.runtime.private_state import digest

            fields = choice.fields
            if for_tool:
                # Older approved cards may contain additional provider-specific labels.
                fields = confirmed_fields(
                    data.get("proposal", {}).get("fields", {}), fields, row.offer_id
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
            data.update(saved=saved, fields_digest=fingerprint)
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
        raise ModelError("source_access_denied", 403)
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
