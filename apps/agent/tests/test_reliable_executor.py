"""Logical recovery, capacity ownership and deadlines as observable behaviors."""

import asyncio

import pytest
from compass_agent.llm.admission import ExecutionGate
from compass_agent.llm.contracts import RuntimeCancelled
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.executor import ReliableExecutor
from compass_agent.llm.policy import LogicalPolicy
from compass_agent.llm.runtime import Runtime
from compass_agent.schemas import ModelUsage

from .test_runtime import Adapter, request, success


def executor(action, gate=None, **options):
    return ReliableExecutor(
        Runtime({"litellm_chat": Adapter(action)}), gate or ExecutionGate(), **options
    )


def test_retries_once_using_same_snapshot_and_aggregates_each_usage_category():
    async def run():
        captured = []

        async def action(req, session):
            captured.append((req.context.call_id, req.access.spec, req.input.model_copy(deep=True)))
            session.resources.sent = True
            session.progress.usage = ModelUsage(input_tokens=2, output_tokens=1)
            if len(captured) == 1:
                req.input.messages[0].content = "adapter mutation"
                raise RuntimeFailure("model_rate_limited", recovery="rejected_transient")
            result = await success(req, session)
            session.progress.usage = ModelUsage(input_tokens=3, output_tokens=4)
            return result

        result = await executor(action, jitter=lambda *args: 0).execute(
            request(5), LogicalPolicy(max_attempts=2)
        )
        assert result.execution is not None
        assert result.status == "completed"
        assert captured[0] == captured[1]
        assert [a.number for a in result.execution.attempts] == [1, 2]
        assert len({a.attempt_id for a in result.execution.attempts}) == 2
        assert result.usage.input_tokens == 5 and result.usage.output_tokens == 5
        assert result.usage.reasoning_tokens is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "code,recovery",
    [
        ("model_authentication_failed", "never"),
        ("schema_validation_failed", "never"),
        ("model_unavailable_upstream", "never"),
    ],
)
def test_non_retryable_failure_stops(code, recovery):
    async def run():
        count = 0

        async def action(req, session):
            nonlocal count
            count += 1
            session.resources.sent = True
            raise RuntimeFailure(code, recovery=recovery)

        result = await executor(action).execute(request(5), LogicalPolicy(max_attempts=2))
        assert result.execution is not None
        assert count == 1 and result.status == "failed"
        assert result.execution.stop_reason == "non_retryable"
        assert result.usage.input_tokens is None

    asyncio.run(run())


def test_shared_capacity_rejects_excess_without_dispatch_and_returns_all_slots():
    async def run():
        gate = ExecutionGate(2, 5)
        released = asyncio.Event()

        async def action(req, session):
            await released.wait()
            return await success(req, session)

        tasks = [
            asyncio.create_task(executor(action, gate).execute(request(), LogicalPolicy()))
            for _ in range(20)
        ]
        await asyncio.sleep(0.03)
        assert gate.active == 2 and gate.inflight == 5
        released.set()
        results = await asyncio.gather(*tasks)
        assert (
            sum(r.error and r.error.code == "runtime_overloaded" for r in results if r.error) == 15
        )
        assert sum(r.status == "completed" for r in results) == 5
        assert gate.snapshot()["available"] == 2 and gate.inflight == 0

    asyncio.run(run())


def test_cancel_queued_call_does_not_release_another_calls_slot():
    async def run():
        gate = ExecutionGate(1, 2)
        released, entered = asyncio.Event(), asyncio.Event()

        async def action(req, session):
            entered.set()
            await released.wait()
            return await success(req, session)

        owner = asyncio.create_task(executor(action, gate).execute(request(), LogicalPolicy()))
        await entered.wait()
        waiting = asyncio.create_task(executor(action, gate).execute(request(), LogicalPolicy()))
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(RuntimeCancelled) as failure:
            await waiting
        assert failure.value.result.execution is not None
        assert failure.value.result.execution.attempts == []
        assert gate.active == 1 and gate.inflight == 1 and gate.slots.locked()
        released.set()
        await owner
        assert gate.snapshot()["available"] == 1

    asyncio.run(run())


def test_cancel_during_backoff_keeps_attempt_metadata_and_stops_dispatch():
    async def run():
        gate = ExecutionGate(1, 2)
        backing = asyncio.Event()

        async def action(req, session):
            session.resources.sent = True
            raise RuntimeFailure("model_rate_limited", recovery="rejected_transient")

        async def sleep(seconds):
            backing.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            executor(action, gate, sleep=sleep).execute(request(5), LogicalPolicy(max_attempts=2))
        )
        await backing.wait()
        task.cancel()
        with pytest.raises(RuntimeCancelled) as failure:
            await task
        result = failure.value.result
        assert result.execution is not None
        assert result.status == "canceled" and len(result.execution.attempts) == 1
        assert gate.inflight == 0 and gate.snapshot()["available"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("acquire_number", [1, 2])
def test_deadline_after_acquire_forms_timeout_and_preserves_prior_attempt(acquire_number):
    async def run():
        gate = ExecutionGate(1, 2)
        acquire = gate.slots.acquire
        count = 0
        # A forwarding clock lets acquire return after the deadline without relying on scheduling.
        from unittest.mock import patch

        loop = asyncio.get_running_loop()
        original_time = loop.time
        offset = [0]

        async def delayed():
            nonlocal count
            await acquire()
            count += 1
            if count == acquire_number:
                offset[0] = 5
            return True

        gate.slots.acquire = delayed

        async def action(req, session):
            session.resources.sent = True
            session.progress.usage = ModelUsage(input_tokens=7)
            raise RuntimeFailure("model_rate_limited", recovery="rejected_transient")

        req = request()
        with patch.object(loop, "time", lambda: original_time() + offset[0]):
            result = await executor(action, gate, jitter=lambda *args: 0).execute(
                req, LogicalPolicy(max_attempts=2)
            )
        assert result.error is not None and result.execution is not None
        assert result.error.code == "model_timeout"
        assert result.execution.stop_reason == "deadline_exceeded"
        assert len(result.execution.attempts) == acquire_number - 1
        assert result.usage.input_tokens == 7 * (acquire_number - 1)
        assert gate.inflight == 0 and gate.snapshot()["available"] == 1

    asyncio.run(run())


def test_logical_policy_validates_limits():
    for kwargs in [
        {"max_attempts": 3},
        {"attempt_timeout_s": 0},
        {"attempt_timeout_s": float("nan")},
    ]:
        with pytest.raises(ValueError):
            LogicalPolicy(**kwargs)


def test_shutdown_rejects_new_roots_and_settles_owned_children():
    from compass_agent.models.errors import ModelError
    from compass_agent.models.lifecycle import ExecutionTasks

    async def run():
        roots = ExecutionTasks()
        entered, closed = asyncio.Event(), asyncio.Event()

        async def work():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

        task = roots.create(work())
        await entered.wait()
        await roots.close()
        assert closed.is_set() and task.cancelled()
        with pytest.raises(ModelError, match="unavailable"):
            roots.create(work())

    asyncio.run(run())


def test_pre_adapter_rejection_has_zero_attempts_and_zero_usage():
    from dataclasses import replace
    from uuid import uuid4

    async def run():
        req = request()
        binding = req.context.continuation_binding.model_copy(update={"provider": uuid4()})
        req = replace(req, context=replace(req.context, continuation_binding=binding))

        async def unexpected(req, session):
            raise AssertionError("invalid binding dispatched")

        result = await executor(unexpected).execute(req, LogicalPolicy(max_attempts=2))
        assert result.execution is not None and result.execution.attempts == []
        assert result.error is not None and result.error.code == "continuation_invalid"
        assert result.usage.input_tokens == 0 and not result.upstream_sent

    asyncio.run(run())


def test_sdk_synthetic_503_after_http_success_cannot_enable_retry():
    from compass_agent.llm.errors import recovery_for

    class SdkReadFailure(Exception):
        status_code = 503

    assert recovery_for(SdkReadFailure(), sent=True, has_partial=False, http_status=200) == "never"
    assert (
        recovery_for(SdkReadFailure(), sent=True, has_partial=False, http_status=503)
        == "rejected_transient"
    )
