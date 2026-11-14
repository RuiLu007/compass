"""Short-lived save offers for successfully executed workflow scripts."""

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from uuid import UUID, uuid4

from compass_agent.execution_context import current_execution
from compass_agent.models.errors import ModelError
from compass_agent.schemas import TrustedContext, WorkflowDraftView
from compass_agent.workflows.assets import scope_of

DRAFT_TTL_SECONDS = 1800
MAX_DRAFTS = 256


@dataclass(frozen=True)
class WorkflowDraft:
    owner: tuple[UUID, UUID, UUID]
    expires_at: float
    detail: WorkflowDraftView


class WorkflowDrafts:
    def __init__(self, durable=None):
        self.durable = durable
        self._drafts: OrderedDict[UUID, WorkflowDraft] = OrderedDict()
        self._lock = Lock()

    def offer(self, context: TrustedContext, detail: WorkflowDraftView) -> UUID:
        if journal := current_execution.get():
            return journal.draft(detail)
        draft_id = uuid4()
        with self._lock:
            now = monotonic()
            while self._drafts and (
                next(iter(self._drafts.values())).expires_at <= now
                or len(self._drafts) >= MAX_DRAFTS
            ):
                self._drafts.popitem(last=False)
            self._drafts[draft_id] = WorkflowDraft(
                scope_of(context), now + DRAFT_TTL_SECONDS, detail
            )
        return draft_id

    def get(self, context: TrustedContext, draft_id: UUID) -> WorkflowDraftView:
        if self.durable is not None:
            value = self.durable.get(context, draft_id)
            if value is not None:
                return value
        owner = scope_of(context)
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft and draft.expires_at <= monotonic():
                del self._drafts[draft_id]
                draft = None
            if draft is None or draft.owner != owner:
                raise ModelError("not_found", 404)
            return draft.detail

    def confirm(self, context, draft_id, name, assets):
        if self.durable is not None:
            value = self.durable.confirm(context, draft_id, name)
            if value is not None:
                return value
        detail = self.get(context, draft_id)
        return assets.save(context, name, detail.script_source, detail.entry_contract)
