"""Bounded Workflow child execution using the common model and tool boundaries."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from compass_agent.harness.autonomy import AutonomyBudget
from compass_agent.harness.prompts import PROFILES, WORKFLOW_CHILD_PROMPT
from compass_agent.runtime.context import (
    RunRoute,
)
from compass_agent.runtime.control import RunControl, RunRegistry
from compass_agent.runtime.execution_trace import ExecutionTrace, preview
from compass_agent.runtime.stream import json_bytes
from compass_agent.runtime.task_state import TaskState
from compass_agent.schemas import (
    ChannelRunParams,
    ChatMessage,
    ExecutionDetail,
    ExecutionDisplay,
    ExecutionEnvironment,
    ExecutionOrigin,
    ExecutionSubAgent,
    GenerateInput,
    ProductRunParams,
    TrustedContext,
)
from compass_agent.tools.business.retail_sales.evidence import (
    RetailVerifier,
    VerificationFailure,
    VerifiedQuery,
)
from compass_agent.tools.business.retail_sales.handler import (
    prepare_retail_call,
    retail_observation,
)
from compass_agent.tools.catalog import definitions as catalog_definitions
from compass_agent.tools.catalog import registrations
from compass_agent.tools.catalog_contracts import CatalogContext
from compass_agent.tools.contracts import (
    ExecutionReceipt,
    PhaseCanceled,
    ResourceUnconfirmed,
    StopRequested,
    ToolExecutionContext,
)
from compass_agent.tools.dispatcher import ToolDispatcher
from compass_agent.tools.registry import ToolRegistry
from compass_agent.workflows.script import WorkflowError


async def run_child(
    executor,
    profile: str,
    params: ChannelRunParams | ProductRunParams,
    route: RunRoute,
    context: TrustedContext,
    parent: RunControl,
    ledger: AutonomyBudget,
    trace: ExecutionTrace | None = None,
) -> VerifiedQuery | None:
    parent.require_running()
    with ledger.child_budget() as budget:
        control = RunRegistry(max_active=1).register(parent.owner, lifetime_s=parent.remaining)
        if trace and trace.parent:
            trace.update(
                trace.parent,
                subagent=ExecutionSubAgent(name=profile, childRunId=str(control.id)),
            )
        trace_nodes: list[str] = []
        expected = params.model_dump(mode="json", by_alias=True)
        state = TaskState(
            [
                ChatMessage(
                    role="system",
                    content=(PROFILES[profile] + WORKFLOW_CHILD_PROMPT),
                ),
                ChatMessage(role="user", content=json_bytes(expected).decode()),
            ]
        )
        verifier = RetailVerifier()
        try:
            step = state.start_step(1)
            model_node = (
                trace.start(
                    "model",
                    "分析子任务",
                    stepId=str(step.id),
                    modelCallId=str(step.model_call_id),
                )
                if trace
                else None
            )
            if model_node:
                trace_nodes.append(model_node)
            budget.generation = step.model_call_id
            request = GenerateInput(
                messages=list(state.messages),
                max_tokens=1024,
                tools=catalog_definitions(
                    CatalogContext(
                        frozenset({"retail"}),
                        retail=True,
                    )
                ),
                continuation_token=state.continuation_token,
            )
            result = await control.perform(
                lambda step=step, request=request: executor._invoke_model(
                    step, request, route, context, control, budget
                ),
                timeout_s=25,
            )
            budget.require_continue()
            executor._require_complete(result)
            if trace and model_node:
                trace.model_content(str(step.id), result, node_id=model_node)
            if len(result.tool_calls) != 1:
                raise WorkflowError("workflow_child_invalid")
            call = result.tool_calls[0]
            prepared = prepare_retail_call(call, route.retail_binding)
            if prepared.safe_error or prepared.params != params:
                raise WorkflowError("workflow_scope_mismatch")
            tool_node = (
                trace.start(
                    "tool",
                    "比较渠道实付金额"
                    if params.dimension == "channel"
                    else f"查询 {params.channel_code} 渠道商品明细",
                    parentId=model_node,
                    stepId=str(step.id),
                    toolCallId=call.id,
                    origin=ExecutionOrigin(kind="builtin", toolName="retail_sales"),
                    executor=ExecutionEnvironment(kind="service"),
                    display=ExecutionDisplay(
                        policy="preview",
                        inputPreview=preview(params.model_dump_json(by_alias=True)),
                        language="json",
                    ),
                    detail=ExecutionDetail(input=params.model_dump_json(by_alias=True)),
                )
                if trace
                else None
            )
            if tool_node:
                trace_nodes.append(tool_node)

            queries = []

            async def invoke(args, ctx, prepared=prepared, queries=queries):
                value = await ctx.control.perform(
                    lambda: executor.retail_query(prepared, ctx.control), timeout_s=8
                )
                queries.append(value)
                return ExecutionReceipt(
                    ctx.call_id,
                    value.status,
                    "confirmed",
                    json.loads(retail_observation(value)),
                )

            registry = ToolRegistry(
                [
                    replace(entry, timeout_s=8)
                    for entry in registrations(
                        CatalogContext(frozenset({"retail"}), retail=True),
                        lambda name: invoke,
                    )
                ]
            )
            dispatcher = ToolDispatcher(
                registry, executor.tool_loader.slots, executor.tool_loader.artifacts
            )
            tool_context = ToolExecutionContext(
                context, parent.id, step.id, control, budget, frozenset({"retail_sales"})
            )
            receipt = await control.perform(
                lambda dispatcher=dispatcher, call=call, tool_context=tool_context: (
                    dispatcher.execute(dispatcher.prepare(call, tool_context), tool_context)
                )
            )
            if receipt.cleanup == "unconfirmed":
                raise ResourceUnconfirmed()
            if not queries:
                raise WorkflowError(receipt.reason_code or "workflow_child_query_failed")
            query = queries[0]
            if trace and tool_node:
                trace.finish(
                    tool_node,
                    "completed"
                    if query.status == "ok"
                    else "empty"
                    if query.status == "empty"
                    else "failed",
                    summary=f"返回 {len(query.rows)} 条记录"
                    if query.status != "error"
                    else "查询未完成",
                    detail=trace.tool_detail(tool_node, output_json=retail_observation(query)),
                )
            try:
                proof, _ = verifier.observe(prepared, query, str(step.id))
            except VerificationFailure as error:
                if error.code in {"empty", "missing_period_data"}:
                    if trace and model_node:
                        trace.finish(model_node, "empty", summary="当前时段缺少所需数据")
                    return None
                raise WorkflowError("workflow_child_query_failed") from None
            if trace and model_node:
                trace.finish(model_node, summary="查询完成")
            return proof
        except (StopRequested, asyncio.CancelledError):
            if trace:
                for identifier in reversed(trace_nodes):
                    trace.finish(identifier, "canceled")
            raise PhaseCanceled(cleanup_confirmed=control.cleanup_confirmed) from None
        except Exception:
            if trace:
                for identifier in reversed(trace_nodes):
                    trace.finish(identifier, "failed")
            if not control.cleanup_confirmed:
                raise ResourceUnconfirmed("workflow child cleanup unconfirmed") from None
            raise
