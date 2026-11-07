"""Bounded logical execution over the single-attempt Runtime."""

import asyncio
import logging
import random
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from compass_agent.llm.admission import ExecutionGate, RuntimeOverloaded
from compass_agent.llm.contracts import (
    DecisionOutput,
    GenerationOutput,
    RuntimeCancelled,
    RuntimeResult,
)
from compass_agent.llm.errors import RuntimeErrorInfo
from compass_agent.llm.policy import LogicalPolicy
from compass_agent.schemas import AttemptSummary, ExecutionSummary, ModelUsage, OutputValidation

logger = logging.getLogger(__name__)


class LogicalRun:
    def __init__(self, request, policy):
        self.request, self.policy = request, policy
        self.attempts = []
        self.last = None
        self.queue_wait_ms = 0
        self.content_exposed = False

    def observe(self, attempt_id, result, started_at):
        self.last = result
        if not result.adapter_started:
            return
        usage = (
            result.usage
            if result.upstream_sent
            else ModelUsage(input_tokens=0, output_tokens=0, reasoning_tokens=0)
        )
        self.attempts.append(
            AttemptSummary(
                attempt_id=attempt_id,
                number=len(self.attempts) + 1,
                provider_id=result.provider_id,
                config_version=result.config_version,
                binding_version=result.binding_version,
                started_at=started_at,
                duration_ms=result.duration_ms,
                status=result.status,
                error_code=result.error.code if result.error else None,
                upstream_request_id=result.upstream_request_id,
                upstream_outcome_unknown=result.upstream_outcome_unknown,
                sent=result.upstream_sent,
                cleanup_confirmed=result.cleanup_confirmed,
                usage=usage,
            )
        )

    def finish(self, reason, error=None, *, canceled=False):
        request, spec = self.request, self.request.access.spec
        result = self.last or RuntimeResult(
            call_id=request.context.call_id,
            provider_id=spec.provider_id,
            config_version=spec.config_version,
            binding_version=spec.binding_version,
            requested_model=spec.api_model,
            status="failed",
            duration_ms=0,
            output=GenerationOutput() if request.input.kind == "generate" else DecisionOutput(),
            usage=ModelUsage(),
            validation=OutputValidation(),
        )
        values = {}
        for name in ModelUsage.model_fields:
            parts = [getattr(a.usage, name) for a in self.attempts]
            values[name] = None if any(x is None for x in parts) else sum(parts)
        status = (
            "canceled"
            if canceled
            else (
                "incomplete" if result.status == "incomplete" or self.content_exposed else "failed"
            )
            if error
            else result.status
        )
        output = result.output
        if status != "completed" and isinstance(output, GenerationOutput):
            output = replace(output, continuation_token=None)
        summary = ExecutionSummary(
            max_attempts=self.policy.max_attempts,
            queue_wait_ms=self.queue_wait_ms,
            stop_reason=reason,
            attempts=self.attempts,
        )
        if len(summary.model_dump_json().encode()) > 16384:
            raise ValueError("execution summary exceeds limit")
        return replace(
            result,
            status=status,
            output=output,
            usage=ModelUsage(**values),
            duration_ms=max(
                0,
                int((asyncio.get_running_loop().time() - request.policy.started_monotonic) * 1000),
            ),
            error=None if canceled else RuntimeErrorInfo(error) if error else result.error,
            upstream_sent=any(a.sent for a in self.attempts),
            upstream_outcome_unknown=any(a.upstream_outcome_unknown for a in self.attempts)
            or (canceled and any(a.sent for a in self.attempts)),
            execution=summary,
        )


def can_retry(result, content_exposed):
    return (
        result.status == "failed"
        and result.error is not None
        and result.error.code in {"model_rate_limited", "model_unavailable_upstream"}
        and result.error.recovery in {"not_sent_transient", "rejected_transient"}
        and not result.upstream_outcome_unknown
        and not content_exposed
        and result.cleanup_confirmed
    )


class ReliableExecutor:
    def __init__(self, runtime, gate: ExecutionGate, *, jitter=None, sleep=asyncio.sleep):
        self.runtime, self.gate = runtime, gate
        self.jitter = jitter or random.uniform
        self.sleep = sleep

    async def execute(self, request, logical_policy: LogicalPolicy, emit=None, *, observation=None):
        request = deepcopy(request)
        run = LogicalRun(request, logical_policy)
        loop = asyncio.get_running_loop()
        deadline = request.policy.deadline_monotonic
        content_exposed = False
        active_observation = None

        async def preview(event):
            nonlocal content_exposed
            if event.type in {"text_delta", "tool_delta"}:
                content_exposed = True
                run.content_exposed = True
                if active_observation:
                    active_observation.first_visible()
            assert emit is not None
            await emit(event)

        try:
            self.gate.enter()
        except RuntimeOverloaded:
            return run.finish("overloaded", "runtime_overloaded")
        try:
            for number in range(1, logical_policy.max_attempts + 1):
                if loop.time() >= deadline:
                    return run.finish("deadline_exceeded", "model_timeout")
                queued = loop.time()
                wait_observation = observation.child("compass.wait") if observation else None
                try:
                    async with asyncio.timeout_at(deadline):
                        await self.gate.slots.acquire()
                except BaseException as exc:
                    if wait_observation:
                        wait_observation.complete(
                            "canceled" if isinstance(exc, asyncio.CancelledError) else "failed",
                            "model_timeout" if isinstance(exc, TimeoutError) else None,
                        )
                    raise
                finally:
                    run.queue_wait_ms += max(0, int((loop.time() - queued) * 1000))
                    if wait_observation:
                        wait_observation.end()
                self.gate.start_attempt()
                cleanup_confirmed = True
                try:
                    started = loop.time()
                    if started >= deadline:
                        return run.finish("deadline_exceeded", "model_timeout")
                    invocation = replace(
                        request,
                        policy=replace(
                            request.policy,
                            started_monotonic=started,
                            deadline_monotonic=min(
                                deadline, started + logical_policy.attempt_timeout_s
                            ),
                        ),
                    )
                    attempt_id, started_at = uuid4(), datetime.now(UTC)
                    cleanup_confirmed = False
                    active_observation = (
                        observation.child(
                            "compass.attempt",
                            attempt_id=str(attempt_id),
                            call_id=str(request.context.call_id),
                            stage=observation.metadata.get("stage", "test"),
                            provider_id=str(request.access.spec.provider_id),
                            model=request.access.spec.api_model,
                            config_version=request.access.spec.config_version,
                            binding_version=request.access.spec.binding_version,
                            adapter=request.access.spec.adapter,
                        )
                        if observation
                        else None
                    )
                    try:
                        result = await self.runtime.invoke(
                            invocation,
                            preview if emit else None,
                            **({"observation": active_observation} if active_observation else {}),
                        )
                    except RuntimeCancelled as exc:
                        cleanup_confirmed = exc.result.cleanup_confirmed
                        run.observe(attempt_id, exc.result, started_at)
                        if active_observation:
                            active_observation.finish_attempt(exc.result)
                        raise
                    cleanup_confirmed = result.cleanup_confirmed
                    run.observe(attempt_id, result, started_at)
                    if active_observation:
                        active_observation.finish_attempt(result)
                finally:
                    if active_observation and not active_observation.ended:
                        active_observation.complete("incomplete", "internal_error")
                        active_observation.end()
                    active_observation = None
                    self.gate.finish_attempt(cleanup_confirmed=cleanup_confirmed)
                if not cleanup_confirmed:
                    logger.warning(
                        "Model execution slot quarantined; call_id=%s", request.context.call_id
                    )
                    return run.finish("cleanup_unconfirmed", "resource_cleanup_failed")
                if result.status == "completed":
                    return run.finish("completed")
                if (
                    result.error
                    and result.error.code == "model_timeout"
                    and loop.time() >= deadline
                ):
                    return run.finish("deadline_exceeded")
                if not can_retry(result, content_exposed):
                    return run.finish("non_retryable")
                if number == logical_policy.max_attempts:
                    return run.finish("attempts_exhausted")
                delay = self.jitter(0.1, 0.3)
                if loop.time() + delay + 0.010 >= deadline:
                    return run.finish("deadline_exceeded", "model_timeout")
                backoff = observation.child("compass.backoff") if observation else None
                try:
                    async with asyncio.timeout_at(deadline):
                        await self.sleep(delay)
                except BaseException as exc:
                    if backoff:
                        backoff.complete(
                            "canceled" if isinstance(exc, asyncio.CancelledError) else "failed",
                            "model_timeout" if isinstance(exc, TimeoutError) else None,
                        )
                    raise
                finally:
                    if backoff:
                        backoff.end()
            raise AssertionError("invalid logical policy")
        except (RuntimeCancelled, asyncio.CancelledError):
            raise RuntimeCancelled(run.finish("canceled", canceled=True)) from None
        except TimeoutError:
            return run.finish("deadline_exceeded", "model_timeout")
        finally:
            if observation:
                observation.set(queue_wait_ms=run.queue_wait_ms)
            self.gate.leave()
