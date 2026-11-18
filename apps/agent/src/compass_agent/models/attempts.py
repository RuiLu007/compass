"""Persistence adapter for the LLM executor's per-physical-attempt recorder port."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from compass_agent.execution_context import current_execution
from compass_agent.llm.continuation import canonical
from compass_agent.models.entities import ModelCall
from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import AgentAttempt, ModelAttempt


class AttemptRecorder:
    def __init__(self, repository, budget=None):
        self.repository = repository
        self.budget = budget
        self.reservations = {}
        self.base_requests = {}
        self.journal = current_execution.get()

    def content(self, call_id, purpose, value):
        if self.journal:
            return self.journal.store.private.seal(self.journal.run_id, purpose, value)
        return self.repository.secrets.seal(
            purpose, {"callId": str(call_id), "value": value}
        ).encode()

    def _prepare(self, identifier, number, request):
        spec, call_id = request.access.spec, request.context.call_id
        raw = request.input.model_dump(mode="json", exclude={"continuation_token"})
        current_request = canonical(raw)
        baseline = self.base_requests.setdefault(call_id, current_request)
        with Session(self.repository.engine) as tx, tx.begin():
            if self.journal:
                _, run = self.journal.store.lock(tx, self.journal.run_id)
                self.journal.store.require_epoch(run, self.journal.epoch)
            call = tx.get(ModelCall, call_id)
            if call is None:
                raise RuntimeError("model call missing")
            if call.request_content is None:
                call.request_content = self.content(call_id, "model-request", raw)
                call.content_status = "captured"
            if self.journal:
                call.run_id, call.agent_attempt_id = (
                    self.journal.run_id,
                    self.journal.active_attempt.get(),
                )
            if self.journal and call.agent_attempt_id:
                logical = tx.get(AgentAttempt, call.agent_attempt_id)
                if logical:
                    call.input_message_refs = logical.receipt.get("inputMessageRefs", [])
            reservation = getattr(request.input, "max_tokens", None) or 4096
            input_tokens = max(1, len(canonical(raw)))
            reserved = (
                self.budget.reservation[1] if self.budget and self.budget.reservation else None
            )
            self.reservations[identifier] = reserved
            token_bound = (
                reserved.profile.input_tokens + reserved.output_limit
                if reserved
                else input_tokens + reservation
            )
            cost_bound = (
                reserved.cost(reserved.profile.input_tokens, reserved.output_limit)
                if reserved
                else 0
            )
            tx.add(
                ModelAttempt(
                    id=identifier,
                    call_id=call_id,
                    number=number,
                    provider_id=spec.provider_id,
                    config_version=spec.config_version,
                    binding_version=spec.binding_version,
                    requested_model=spec.api_model,
                    status="prepared",
                    origin="live",
                    request_content=self.content(call_id, "model-attempt-request", raw)
                    if current_request != baseline
                    else None,
                    sent=False,
                    outcome_unknown=False,
                    cleanup_confirmed=False,
                    usage={},
                    reserved_tokens=token_bound,
                    reserved_cost_microusd=cost_bound,
                    created_at=datetime.now(UTC),
                )
            )

    async def prepare(self, identifier, number, request):
        await asyncio.to_thread(self._prepare, identifier, number, request)

    async def dispatched(self, identifier):
        def write():
            with Session(self.repository.engine) as tx, tx.begin():
                if self.journal:
                    _, run = self.journal.store.lock(tx, self.journal.run_id)
                    self.journal.store.require_epoch(run, self.journal.epoch)
                attempt = tx.get(ModelAttempt, identifier)
                if attempt is None:
                    raise RuntimeError("model attempt missing")
                attempt.status, attempt.sent, attempt.outcome_unknown = "dispatched", True, True
                attempt.dispatched_at = datetime.now(UTC)

        dispatch = asyncio.create_task(asyncio.to_thread(write))
        try:
            await asyncio.shield(dispatch)
        except asyncio.CancelledError:
            # The caller cannot enter the provider after this await was canceled.
            # Let the dispatch write settle before recording that fact.
            try:
                await dispatch
            except ModelError as error:
                if error.code not in {"execution_fenced", "lease_lost"}:
                    raise
            else:
                await asyncio.to_thread(self._cancel_before_send, identifier)
            raise

    def _cancel_before_send(self, identifier):
        with Session(self.repository.engine) as tx, tx.begin():
            attempt = tx.get(ModelAttempt, identifier)
            assert attempt is not None
            attempt.status, attempt.finished_at = "canceled", datetime.now(UTC)
            attempt.sent, attempt.outcome_unknown = False, False
            attempt.cleanup_confirmed = True
            attempt.error_code = "user_canceled"

    async def finish(self, identifier, result):
        def write():
            from pydantic import TypeAdapter

            from compass_agent.llm.contracts import RuntimeResult
            from compass_agent.runtime.private_state import digest

            with Session(self.repository.engine) as tx, tx.begin():
                attempt = tx.get(ModelAttempt, identifier)
                if attempt is None:
                    raise RuntimeError("model attempt missing")
                if result is None:
                    attempt.status, attempt.outcome_unknown = "unknown", True
                    return
                value = TypeAdapter(RuntimeResult).dump_python(result, mode="json")
                # The runtime owns private continuation authority.
                if isinstance(value.get("output"), dict):
                    value["output"].pop("continuation_token", None)
                attempt.output_content = self.content(
                    attempt.call_id, "model-attempt-output", value
                )
                attempt.output_sha256 = digest(value)
                attempt.status = (
                    "completed"
                    if result.status == "completed"
                    else "canceled"
                    if result.status == "canceled"
                    else "incomplete"
                    if result.upstream_outcome_unknown
                    else "failed"
                )
                attempt.sent = result.upstream_sent
                attempt.outcome_unknown, attempt.cleanup_confirmed = (
                    result.upstream_outcome_unknown,
                    result.cleanup_confirmed,
                )
                attempt.usage = result.usage.model_dump(mode="json")
                reservation = self.reservations.get(identifier)
                if (
                    reservation
                    and result.usage.input_tokens is not None
                    and result.usage.output_tokens is not None
                ):
                    attempt.actual_cost_microusd = reservation.cost(
                        result.usage.input_tokens, result.usage.output_tokens
                    )
                call = tx.get(ModelCall, attempt.call_id)
                if call is not None and result.status == "completed":
                    call.output_content = attempt.output_content
                attempt.upstream_request_id = result.upstream_request_id
                attempt.error_code = result.error.code if result.error else None
                attempt.finished_at, attempt.duration_ms = datetime.now(UTC), result.duration_ms

        await asyncio.to_thread(write)
