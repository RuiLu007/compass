"""Rebuild private choice/form cards for the owner at read and decision time."""

from datetime import UTC, datetime
from uuid import UUID

from compass_agent.models.errors import ModelError
from compass_agent.semantic.client import request
from compass_agent.semantic.draft_fields import result_preview
from compass_agent.semantic.extraction import receipt_proposal
from compass_agent.semantic.results import MAX_CARD_ITEMS


def card(store, run, context, private) -> dict:
    kind = private.get("kind")
    pins = run.config_snapshot.get("contextRuntime", {}).get("registry", {}).get("pins")
    if pins is None or kind not in {
        "semantic_choice",
        "semantic_result_choice",
        "semantic_draft",
        "semantic_revision",
        "semantic_submit",
    }:
        return {}

    def api(op, args):
        return request(store.source_settings, context, run.id, op, args, pins)

    if kind == "semantic_revision":
        revision = private["revision"]
        try:
            view = api(
                "revision-preview",
                {
                    "metric": revision["asset_id"],
                    "expected_digest": revision["expected_digest"],
                    "changes": revision["changes"],
                },
            )
        except ModelError as exc:
            if exc.status not in {403, 409}:
                raise
            # Keep the owner's reject action available when a preview becomes stale.
            return {"semanticKind": "revision"}
        return {
            "semanticKind": "revision",
            "revisionPreview": {key: view[key] for key in ("name", "code", "changes")},
        }
    if kind == "semantic_result_choice":
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from compass_agent.semantic.entities import SemanticMetricOffer
        from compass_agent.semantic.results import visible

        ids = [UUID(o["id"]) for o in private["options"]]
        with Session(store.engine) as tx:
            rows = tx.scalars(
                select(SemanticMetricOffer).where(
                    SemanticMetricOffer.offer_id.in_(ids),
                    SemanticMetricOffer.session_id == run.session_id,
                    SemanticMetricOffer.kind == "open",
                )
            ).all()
            allowed = {
                str(r.offer_id)
                for r in rows
                if r.status != "saved"
                and visible(
                    store,
                    context,
                    r,
                    store.private.open(r.run_id, r.kind, r.private_payload),
                    epoch=run.execution_epoch,
                )
            }
        return {
            "semanticKind": "result",
            "options": [o for o in private["options"] if o["id"] in allowed][:MAX_CARD_ITEMS],
        }
    if kind == "semantic_choice":
        current = api(
            "resolve",
            {
                k: v
                for k, v in private["args"].items()
                if k not in {"parameters", "historical_as_of"}
            },
        )
        candidates = current.get("options", [])
        if current.get("metric"):
            candidates = [current["metric"]]
        keys = {(v["asset_id"], v["version_id"]) for v in candidates}
        return {
            "semanticKind": "choice",
            "options": [v for v in private["options"] if (v["id"], v["versionId"]) in keys][
                :MAX_CARD_ITEMS
            ],
        }
    if kind == "semantic_draft":
        from sqlalchemy.orm import Session

        from compass_agent.context_runtime.source_access import allows
        from compass_agent.semantic.entities import SemanticMetricOffer

        with Session(store.engine) as tx:
            offer = tx.get(
                SemanticMetricOffer,
                UUID(private.get("result_ref") or private["args"]["result_ref"]),
            )
            if (
                not offer
                or offer.expires_at <= datetime.now(UTC)
                or offer.kind != "open"
                or offer.status == "saved"
                or offer.session_id != run.session_id
                or offer.owner_id != context.actor_user_id
            ):
                raise ModelError("forbidden", 403)
            original = store.private.open(offer.run_id, offer.kind, offer.private_payload)
        if not allows(store, context, original["refs"], epoch=run.execution_epoch):
            raise ModelError("forbidden", 403)
        scopes = api("scopes", {})
        if not scopes["can_create_personal"] and not any(s["manage"] for s in scopes["items"]):
            raise ModelError("forbidden", 403)
        fields = result_preview(
            receipt_proposal(original).get("fields", {}), private["fields"], offer.offer_id
        )
        return {"semanticKind": "draft", "fields": fields, "scopes": scopes["items"]}
    state = api("validate", {"version_id": private["args"]["version_id"]})
    if state["digest"] != private["args"]["expected_digest"]:
        raise ModelError("proposal_conflict", 409)
    return {"semanticKind": "submit"}


def apply(store, run, context, private, command):
    if command.custom_definition is not None:
        if private.get("kind") != "semantic_choice":
            raise ModelError("invalid_parameters", 422)
        private["customDefinition"] = command.custom_definition.strip()
        return
    view = card(store, run, context, private)
    if private.get("kind") == "semantic_draft":
        private["fields"] = view["fields"]
    if private.get("kind") in {"semantic_choice", "semantic_result_choice"}:
        if str(command.choice_id) not in {v["id"] for v in view.get("options", [])}:
            raise ModelError("proposal_conflict", 409)
        private["choiceId"] = str(command.choice_id)
    elif command.choice_id:
        raise ModelError("proposal_conflict", 409)
    if private.get("kind") == "semantic_revision":
        preview = view.get("revisionPreview")
        if not preview:
            raise ModelError("proposal_conflict", 409)
        changes = private["revision"]["changes"]
        edits = command.draft_edits or {}
        if set(edits) - changes.keys() or any(
            not isinstance(v, str)
            or not v.strip()
            or len(v) > (2400 if k in {"summary", "formula"} else 160)
            for k, v in edits.items()
        ):
            raise ModelError("invalid_parameters", 422)
        changes = {**changes, **{k: v.strip() for k, v in edits.items()}}
        if all(changes[item["field"]] == item["before"] for item in preview["changes"]):
            raise ModelError("invalid_parameters", 422)
        private["revision"]["changes"] = changes
    elif private.get("kind") == "semantic_draft" and command.draft_edits:
        # User-editable attributes exclude source receipts, baseline digest and identity.
        allowed = {
            "code",
            "domain",
            "timezone",
            "aggregation",
            "name",
            "summary",
            "responsible",
            "usage_scope_id",
            "owner_scope_id",
            "unit",
            "grain",
            "event_time",
            "formula",
        }
        if set(command.draft_edits) - allowed:
            raise ModelError("proposal_conflict", 409)
        fields = {**private["fields"], **command.draft_edits}
        for key in ("owner_scope_id", "usage_scope_id"):
            if not fields.get(key):
                fields.pop(key, None)
        private["fields"] = fields
    elif command.draft_edits:
        raise ModelError("proposal_conflict", 409)
    if private.get("kind") == "semantic_draft":
        import re

        fields = private["fields"]
        if not all(
            isinstance(fields.get(k), str) and fields[k].strip() for k in ("code", "name", "domain")
        ) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,119}", fields["code"]):
            raise ModelError("invalid_parameters", 422)
