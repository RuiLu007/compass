"""Whole-batch preflight and bounded scheduling with original-order results."""

import asyncio
from dataclasses import replace

from compass_agent.schemas import ToolCall
from compass_agent.tools.contracts import (
    PhaseCanceled,
    PreparedToolCall,
    ToolBatchPolicy,
    ToolExecutionContext,
    ToolExecutionEvent,
    ToolExecutionResult,
    ToolRejected,
)
from compass_agent.tools.dispatcher import ToolDispatcher

REVOKED = {
    "mcp_configuration_stale",
    "tool_config_changed",
    "mcp_catalog_stale",
    "mcp_scope_denied",
}


async def dispatch_batch(
    dispatcher: ToolDispatcher,
    calls: tuple[ToolCall, ...],
    context: ToolExecutionContext,
    policy: ToolBatchPolicy,
) -> tuple[ToolExecutionResult, ...]:
    if not calls or len(calls) > 16 or len({c.id for c in calls}) != len(calls):
        raise ToolRejected("invalid_batch")
    emitted: set[str] = set()
    publish = context.emit

    def record(event):
        emitted.add(event.tool_call_id)
        publish(event)

    context = replace(context, emit=record)
    results: list[ToolExecutionResult | None] = [None] * len(calls)
    prepared: list[PreparedToolCall | None] = [None] * len(calls)
    try:
        for index, call in enumerate(calls):
            try:
                item = dispatcher.prepare(call, context)
                prepared[index] = item
                await dispatcher.revalidate(item, context)
                results[index] = dispatcher.admission(item, context)
                if (
                    results[index] is None
                    and policy.mode == "parallel"
                    and not item.tool.parallel_safe
                ):
                    results[index] = ToolExecutionResult(
                        call.id, "rejected", reason_code="parallel_not_supported"
                    )
            except ToolRejected as error:
                results[index] = ToolExecutionResult(call.id, "rejected", reason_code=error.code)
    except asyncio.CancelledError:
        raise PhaseCanceled(cleanup_confirmed=context.control.cleanup_confirmed) from None
    waiting = any(result and result.status == "waiting" for result in results)
    rejected = any(result and result.status != "waiting" for result in results)
    revoked = any(result and result.reason_code in REVOKED for result in results)
    if waiting or revoked or rejected and policy.failure_mode == "stop":
        reason = "batch_waiting" if waiting else "batch_preflight_failed"
        results = [
            result or ToolExecutionResult(call.id, "rejected", reason_code=reason)
            for call, result in zip(calls, results, strict=True)
        ]
    for call, item, result in zip(calls, prepared, results, strict=True):
        if result is not None:
            context.emit(
                ToolExecutionEvent(
                    context.run_id,
                    context.step_id,
                    call.id,
                    call.function.name,
                    item.tool.binding.environment if item and item.tool.binding else "none",
                    item.tool.policy.effect if item else "unknown",
                    item.tool.policy.approval if item else "required",
                    result.status,
                    result.cleanup,
                    0,
                    result.reason_code,
                )
            )
    if all(result is not None for result in results):
        return tuple(result for result in results if result is not None)

    stopped = False
    cursor = 0
    workers: list[asyncio.Task] = []

    async def work():
        nonlocal cursor, stopped
        while cursor < len(calls) and not stopped:
            index = cursor
            cursor += 1
            if results[index] is not None:
                continue
            item = prepared[index]
            assert item is not None
            try:
                result = await dispatcher.execute(item, replace(context, call_id=item.call_id))
                results[index] = result
                must_stop = (
                    result.cleanup == "unconfirmed"
                    or result.reason_code in REVOKED
                    or result.status in {"canceled", "timeout"}
                    or (policy.failure_mode == "stop" and result.status not in {"ok", "empty"})
                )
                if must_stop:
                    stopped = True
                    for task in workers:
                        if (
                            task is not asyncio.current_task()
                            and not task.done()
                            and not task.cancelling()
                        ):
                            task.cancel()
            except BaseException:
                stopped = True
                for task in workers:
                    if (
                        task is not asyncio.current_task()
                        and not task.done()
                        and not task.cancelling()
                    ):
                        task.cancel()
                raise

    count = 1 if policy.mode == "serial" else min(policy.max_concurrency, len(calls))
    workers = [asyncio.create_task(work()) for _ in range(count)]
    settled_workers = asyncio.gather(*workers, return_exceptions=True)
    try:
        # Let this scope signal workers once and wait for their cleanup receipts.
        outcomes = await asyncio.shield(settled_workers)
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(
                outcome, asyncio.CancelledError
            ):
                raise outcome
    except BaseException as error:
        for task in workers:
            if not task.done() and not task.cancelling():
                task.cancel()
        await settled_workers
        for call, item, result in zip(calls, prepared, results, strict=True):
            if call.id not in emitted:
                context.emit(
                    ToolExecutionEvent(
                        context.run_id,
                        context.step_id,
                        call.id,
                        call.function.name,
                        item.tool.binding.environment if item and item.tool.binding else "none",
                        item.tool.policy.effect if item else "unknown",
                        item.tool.policy.approval if item else "required",
                        result.status if result else "rejected",
                        result.cleanup if result else "not_started",
                        0,
                        result.reason_code if result else "batch_stopped",
                    )
                )
        if isinstance(error, asyncio.CancelledError):
            raise PhaseCanceled(cleanup_confirmed=context.control.cleanup_confirmed) from None
        raise
    settled = tuple(
        result or ToolExecutionResult(call.id, "rejected", reason_code="batch_stopped")
        for call, result in zip(calls, results, strict=True)
    )
    for call, item, result in zip(calls, prepared, settled, strict=True):
        if call.id not in emitted:
            context.emit(
                ToolExecutionEvent(
                    context.run_id,
                    context.step_id,
                    call.id,
                    call.function.name,
                    item.tool.binding.environment if item and item.tool.binding else "none",
                    item.tool.policy.effect if item else "unknown",
                    item.tool.policy.approval if item else "required",
                    result.status,
                    result.cleanup,
                    0,
                    result.reason_code,
                )
            )
    return settled
