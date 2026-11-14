"""Durable artifact bytes and owner-scoped, expiring workflow save offers."""

from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import (
    AgentArtifact,
    AgentAttempt,
    AgentStep,
    AgentWorkflowDraft,
)
from compass_agent.runtime.store import now
from compass_agent.schemas import WorkflowDraftView
from compass_agent.workflows.assets import (
    WorkflowAsset,
    save_asset,
    view,
)


def draft_event(row):
    return {
        "type": "view.upsert",
        "payload": {
            "objectId": str(row.id),
            "revision": 2 if row.status == "saved" else 1,
            "kind": "workflow",
            "value": {
                "draftId": str(row.id),
                "draft": row.validation_summary,
                "status": row.status,
                "assetId": str(row.asset_id) if row.asset_id else None,
                "expiresAt": row.expires_at.isoformat(),
            },
        },
    }


class RuntimeResources:
    def __init__(self, store):
        self.store = store

    def artifact(self, journal, ref, content):
        if (
            ref.run_id != journal.run_id
            or ref.byte_size != len(content)
            or ref.sha256 != sha256(content).hexdigest()
        ):
            raise ModelError("artifact_invalid", 409)
        with self.store.transaction() as tx:
            session, run = self.store.lock(tx, journal.run_id)
            self.store.require_epoch(run, journal.epoch)
            previous = tx.get(AgentArtifact, UUID(ref.id))
            if previous:
                if previous.run_id != run.id or previous.sha256 != ref.sha256:
                    raise ModelError("artifact_conflict", 409)
                return
            used = (
                tx.scalar(
                    select(func.sum(AgentArtifact.size_bytes)).where(AgentArtifact.run_id == run.id)
                )
                or 0
            )
            if used + len(content) > 2097152:
                raise ModelError("artifact_limit", 409)
            attempt_id = journal.active_attempt.get() or journal.last_attempt
            if attempt_id is None:
                attempt_id = tx.scalar(
                    select(AgentAttempt.id)
                    .where(AgentAttempt.run_id == run.id, AgentAttempt.status == "completed")
                    .order_by(AgentAttempt.delivery_no.desc())
                    .limit(1)
                )
            if attempt_id is None:
                raise ModelError("artifact_attempt_missing", 409)
            tx.add(
                AgentArtifact(
                    id=UUID(ref.id),
                    run_id=run.id,
                    attempt_id=attempt_id,
                    tool_call_id=ref.tool_call_id,
                    name=ref.name,
                    media_type=ref.media_type,
                    size_bytes=len(content),
                    sha256=ref.sha256,
                    content=content,
                    scope={
                        "tenant": str(session.tenant_id),
                        "workspace": str(session.workspace_id),
                        "owner": str(session.owner_user_id),
                    },
                    created_at=now(),
                )
            )
            value = {
                "id": ref.id,
                "name": ref.name,
                "mediaType": ref.media_type,
                "byteSize": len(content),
                "sha256": ref.sha256,
                "stepId": str(ref.step_id),
                "toolCallId": ref.tool_call_id,
            }
            self.store.outbox(
                tx,
                session,
                run,
                [
                    {
                        "type": "view.upsert",
                        "payload": {
                            "objectId": ref.id,
                            "revision": 1,
                            "kind": "artifact",
                            "value": value,
                        },
                    }
                ],
            )

    def offer(self, journal, detail, *, step_id=None):
        with self.store.transaction() as tx:
            session, run = self.store.lock(tx, journal.run_id)
            self.store.require_epoch(run, journal.epoch)
            query = select(AgentStep).where(
                AgentStep.run_id == run.id, AgentStep.kind == "workflow"
            )
            if step_id is not None:
                query = query.where(AgentStep.id == step_id)
            step = tx.scalar(query.order_by(AgentStep.ordinal.desc()).limit(1))
            if step is None:
                raise ModelError("checkpoint_missing", 409)
            identifier = uuid5(run.id, "draft:" + str(step.id))
            if tx.get(AgentWorkflowDraft, identifier) or any(
                draft.id == identifier for draft in journal.pending_drafts
            ):
                return identifier
            row = AgentWorkflowDraft(
                id=identifier,
                run_id=run.id,
                step_id=step.id,
                suggested_name=detail.name,
                script_source=detail.script_source,
                script_sha256=sha256(detail.script_source.encode()).hexdigest(),
                definition_version=1,
                runner_api_version=1,
                validation_summary=detail.model_dump(mode="json", by_alias=True),
                status="offered",
                created_at=now(),
                expires_at=now() + timedelta(minutes=30),
            )
            journal.pending_drafts.append(row)
            return identifier

    def get(self, context, identifier):
        with Session(self.store.engine) as tx:
            row = tx.get(AgentWorkflowDraft, identifier)
            if row is None:
                return None
            self.store.read_run(row.run_id, context)
            if row.status != "saved" and row.expires_at <= now():
                raise ModelError("draft_expired", 410)
            if sha256(row.script_source.encode()).hexdigest() != row.script_sha256:
                raise ModelError("draft_corrupt", 409)
            return WorkflowDraftView.model_validate(row.validation_summary)

    def confirm(self, context, identifier, name):
        with self.store.transaction() as tx:
            draft = tx.get(AgentWorkflowDraft, identifier)
            if draft is None:
                return None
            session, run = self.store.lock(tx, draft.run_id, context)
            tx.refresh(draft)
            if run.status != "completed":
                raise ModelError("draft_not_ready", 409)
            if draft.status == "saved":
                asset = tx.get(WorkflowAsset, draft.asset_id)
                assert asset is not None
                return view(asset)
            if draft.expires_at <= tx.scalar(select(func.clock_timestamp())):
                raise ModelError("draft_expired", 410)
            if sha256(draft.script_source.encode()).hexdigest() != draft.script_sha256:
                raise ModelError("draft_corrupt", 409)
            detail = WorkflowDraftView.model_validate(draft.validation_summary)
            asset = save_asset(
                tx,
                context,
                name,
                draft.script_source,
                detail.entry_contract,
                detail.description,
                detail.parameters,
            )
            draft.asset_id, draft.saved_at, draft.status = asset.asset_id, now(), "saved"
            draft.validation_summary = {**draft.validation_summary, "name": asset.name}
            self.store.outbox(tx, session, run, [draft_event(draft)])
            return view(asset)
