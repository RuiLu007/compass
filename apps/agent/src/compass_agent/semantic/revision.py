"""Commit an owner-approved edit through the Web-owned metric version ledger."""

from datetime import UTC, datetime
from uuid import UUID

from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import AgentAction
from compass_agent.semantic.client import request


def save_approved_revision(service, grant):
    journal = service.journal
    with service.store.transaction() as tx:
        _, run = service.store.lock(tx, journal.run_id, service.trusted)
        service.store.require_epoch(run, journal.epoch)
        action = tx.get(AgentAction, UUID(grant["actionId"]), with_for_update=True)
        if not action or action.run_id != run.id or action.state not in {"pending", "applied"}:
            raise ModelError("proposal_conflict", 409)
        # A retry after a lost response recovers the Web receipt under the same action key.
        saved = request(
            service.store.source_settings,
            service.trusted,
            run.id,
            "revision-save",
            {**grant["revision"], "request_key": str(action.id)},
            service.pins,
        )
        action.receipt = {"asset_id": saved["asset_id"], "version_id": saved["version_id"]}
        action.state = "applied"
        action.updated_at = datetime.now(UTC)
