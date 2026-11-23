"""Auxiliary model calls reuse provider authorization, accounting and durable attempts."""

import asyncio
from uuid import uuid5

from compass_agent.llm.policy import LogicalPolicy
from compass_agent.models import RoutingGrant
from compass_agent.schemas import CallResponse


class OptionalBudget:
    """A refused optional reservation must leave the main task's stop reason unchanged."""

    def __init__(self, budget):
        self.budget = budget
        self.reason: str | None = budget.reason

    def reserve(self, request, attempt_id):
        old_reason = self.budget.reason
        accepted = self.budget.reserve(request, attempt_id)
        self.reason = self.budget.reason
        if not accepted:
            self.budget.reason = old_reason
        return accepted

    def settle(self, attempt_id, result):
        self.budget.settle(attempt_id, result)

    @property
    def reservation(self):
        return self.budget.reservation


async def auxiliary_call(
    journal,
    models,
    trusted,
    control,
    budget,
    *,
    kind,
    key,
    provider_id,
    binding_version,
    request,
    optional=False,
    deadline=None,
    source_refs=(),
):
    call_id = uuid5(journal.run_id, key)
    wire = request.model_dump(mode="json", exclude_none=True)

    async def revalidate():
        from compass_agent.models.errors import ModelError

        grants = await journal.capabilities() if journal.capabilities else {}
        if not grants.get("analysis") or not grants.get("model"):
            raise ModelError("forbidden", 403)
        from compass_agent.context_runtime.source_access import allows

        if not await asyncio.to_thread(
            allows, journal.store, trusted, source_refs, epoch=journal.epoch, raise_unavailable=True
        ):
            raise ModelError("context_access_changed", 403)
        control.require_running()

    async def invoke():
        models.stage = "context_selection" if kind == "ctx_select" else "context_compaction"
        from compass_agent.models.errors import ModelError

        try:
            prepared = await models.prepare(
                call_id,
                provider_id,
                request,
                trusted,
                deadline=min(
                    control.deadline_at, deadline or (asyncio.get_running_loop().time() + 25)
                ),
                grant=RoutingGrant(
                    trusted.actor_user_id, trusted.scope, call_id, provider_id, request.kind
                ),
                expected_binding_version=binding_version,
            )
        except ModelError as error:
            # A pre-dispatch failure is a completed optional decision, with no
            # provider side effect to leave in the unknown-execution state.
            return CallResponse(
                call_id=call_id,
                provider_id=provider_id,
                config_version=0,
                status="failed",
                duration_ms=0,
                error_code=error.code,
                requested_model="",
                kind=request.kind,
            )
        result = await models.run(
            prepared,
            request,
            attempt_budget=OptionalBudget(budget) if optional and budget else budget,
            logical_policy=LogicalPolicy(max_attempts=1),
        )
        if result.error_code == "resource_cleanup_failed":
            from compass_agent.models.service import ModelInvocationCanceled

            raise ModelInvocationCanceled(cleanup_confirmed=False)
        if result.error_code == "record_write_failed":
            raise ModelError("record_write_failed", 503)
        return result

    return await journal.call(
        kind,
        key,
        {"provider": str(provider_id), "binding": binding_version, "request": wire},
        invoke,
        CallResponse,
        budget=budget,
        revalidate=revalidate,
        yield_boundary=False,
    )


def restore_budget(journal, budget, refs):
    """A frozen main frame skips auxiliary code; restore its accepted costs once."""
    if budget is None:
        return
    from uuid import UUID

    from sqlalchemy.orm import Session

    from compass_agent.models.errors import ModelError
    from compass_agent.runtime.entities import AgentAttempt, AgentStep
    from compass_agent.runtime.private_state import digest

    with Session(journal.store.engine) as tx:
        for ref in dict.fromkeys(refs):
            identifier = UUID(ref)
            if identifier in journal.context_accounted_attempts:
                continue
            attempt = tx.get(AgentAttempt, identifier)
            step = tx.get(AgentStep, attempt.step_id) if attempt else None
            if (
                not attempt
                or not step
                or attempt.run_id != journal.run_id
                or step.kind not in {"ctx_select", "ctx_compact"}
                or step.accepted_attempt_id != identifier
            ):
                raise ModelError("checkpoint_corrupt", 409)
            value = journal.store.private.open(
                journal.run_id, "attempt-output", attempt.output_content
            )
            if digest(value) != attempt.output_sha256:
                raise ModelError("checkpoint_corrupt", 409)
            for key, amount in value.get("budget", {}).items():
                setattr(budget, key, getattr(budget, key) + amount)
            journal.context_accounted_attempts.add(identifier)
