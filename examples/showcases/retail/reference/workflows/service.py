"""Classroom hybrid workflows and their typed tool boundary."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import Field

from compass_agent.execution_context import uuid4
from compass_agent.harness.prompts import PROFILES
from compass_agent.harness.verification import (
    VerificationContext,
    VerificationHook,
    VerificationRejected,
    verify_result,
)
from compass_agent.models.errors import ModelError
from compass_agent.routing.workflows import checked_periods
from compass_agent.runtime.control import RunControl
from compass_agent.runtime.execution_trace import ExecutionTrace
from compass_agent.schemas import (
    ChannelRunParams,
    ExecutionEnvironment,
    ExecutionSkill,
    ExecutionSubAgent,
    ExecutionWorkflow,
    ProductRunParams,
    RunContractModel,
    RunExtensionPayload,
    RunWorkflowProgress,
    StreamExtension,
    ToolCall,
    ToolDefinition,
    TrustedContext,
    WorkflowAssetView,
    WorkflowDraftView,
    WorkflowPeriods,
)
from compass_agent.tools.builtin.find_workflows.catalog import (
    DIRECT_WORKFLOW_TOOLS,
    RegisteredWorkflow,
    catalog_prompt,
)
from compass_agent.tools.builtin.reuse_workflow import ReuseScript
from compass_agent.tools.builtin.run_dynamic_workflow import EntryContract, NewScript
from compass_agent.tools.business.retail_sales.evidence import VerifiedQuery
from compass_agent.tools.business.retail_sales.handler import retail_observation
from compass_agent.tools.business.retail_sales.session import RetailSession
from compass_agent.tools.catalog_contracts import CatalogContext
from compass_agent.tools.contracts import PhaseCanceled
from compass_agent.workflows.assets import WorkflowAssets, normalize_source
from compass_agent.workflows.script import WorkflowError, execute_script


class ChildInput(WorkflowPeriods):
    channel_code: str = Field(alias="channelCode", min_length=1, max_length=40)
    lead_evidence_ref: str = Field(alias="leadEvidenceRef", min_length=1, max_length=40)


class ScriptResult(RunContractModel):
    status: Literal["ok", "needs_data", "needs_review"]
    evidence_refs: list[str] = Field(alias="evidenceRefs", max_length=3)


def hybrid_specs():
    from compass_agent.tools.catalog import workflow_specs

    return workflow_specs()


HYBRID_TOOLS = tuple(
    value
    for spec in hybrid_specs()
    if (value := spec.definition(CatalogContext(frozenset({"workflow"}), workflow=True)))
    is not None
)


def workflow_event(
    source: str,
    key: str,
    status: str,
    *,
    detail: str | None = None,
    asset: WorkflowAssetView | None = None,
) -> StreamExtension:
    data = RunWorkflowProgress.model_validate(
        {
            "source": source,
            "key": key,
            "status": status,
            "detail": detail,
            "asset": {
                "assetId": str(asset.asset_id),
                "name": asset.name,
                "definitionVersion": asset.definition_version,
            }
            if asset
            else None,
        }
    ).model_dump(mode="json", by_alias=True, exclude_none=True)
    return StreamExtension(
        type="stream.extension",
        payload=RunExtensionPayload(
            name="compass.workflow.node",
            version=1,
            required=False,
            data=data,
        ),
    )


def params_for(profile: str, periods: WorkflowPeriods, channel: str | None = None):
    if profile == "product_breakdown" and channel:
        return ProductRunParams(
            dimension="product",
            period=periods.baseline,
            compareTo=periods.current,
            channelCode=channel,
        )
    return ChannelRunParams(dimension="channel", period=periods.baseline, compareTo=periods.current)


@dataclass(frozen=True)
class WorkflowReceipt:
    source: str
    contract: EntryContract
    periods: WorkflowPeriods
    generated: bool
    result: dict


type WorkflowVerifier = VerificationHook[WorkflowReceipt]


@dataclass
class HybridWorkflows:
    assets: WorkflowAssets
    context: TrustedContext
    control: RunControl
    run: RetailSession
    emit: Callable[[StreamExtension], None]
    child: Callable[[str, ChannelRunParams | ProductRunParams], Awaitable[VerifiedQuery | None]]
    confirm_binding: Callable[[], Awaitable[None]]
    diagnosis_periods: WorkflowPeriods | None = None
    trace: ExecutionTrace | None = None
    trace_root: str | None = None
    trace_nodes: dict[str, str] = field(default_factory=dict)
    save_offer: WorkflowDraftView | None = None
    registered: dict[str, RegisteredWorkflow] = field(default_factory=dict)
    verifiers: Mapping[str | UUID, tuple[WorkflowVerifier, ...]] = field(default_factory=dict)

    def register(self, assets: list[WorkflowAssetView]) -> None:
        discovered = {
            item.tool_name: item
            for asset in assets
            if asset.status == "active"
            for item in [RegisteredWorkflow.from_asset(asset)]
        }
        # A search promotes its matches without losing previously advertised bindings.
        self.registered = discovered | {
            name: item for name, item in self.registered.items() if name not in discovered
        }

    async def load_catalog(self) -> str:
        page = await self.asset_operation(lambda: self.assets.list(self.context, active=True))
        self.register(page.items)
        return catalog_prompt(list(self.registered.values()), page.next_cursor)

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return HYBRID_TOOLS + tuple(
            item.definition() for item in list(self.registered.values())[:DIRECT_WORKFLOW_TOOLS]
        )

    def owns_tool(self, name: str) -> bool:
        return name in {spec.name for spec in hybrid_specs()} or name in self.registered

    def event(
        self,
        source: str,
        key: str,
        status: str,
        detail: str | None = None,
        *,
        asset: WorkflowAssetView | None = None,
    ) -> str | None:
        self.emit(workflow_event(source, key, status, detail=detail, asset=asset))
        trace = self.trace
        if trace is None or key == "save_offer":
            return None
        if (source == "dynamic" and key in {"generated", "reused"}) or (
            source == "fixed" and key == "preflight" and status == "running"
        ):
            if status == "running":
                mode: Literal["generated", "reused", "fixed"] = (
                    "generated"
                    if key == "generated"
                    else "reused"
                    if source == "dynamic"
                    else "fixed"
                )
                title = (
                    f"已复用「{asset.name}」· v{asset.definition_version}"
                    if asset
                    else "运行新生成的工作流"
                    if mode == "generated"
                    else "准备预算调整提案"
                )
                self.trace_nodes = {}
                self.trace_root = trace.current_call("workflow") or trace.start("workflow", title)
                trace.update(
                    self.trace_root,
                    title=title,
                    status="running",
                    workflow=ExecutionWorkflow(
                        mode=mode,
                        assetId=str(asset.asset_id) if asset else None,
                        name=asset.name if asset else None,
                        version=asset.definition_version if asset else 1,
                    ),
                    executor=ExecutionEnvironment(
                        kind="service" if source == "fixed" else "quickjs",
                        runtime="Python" if source == "fixed" else "JavaScript",
                    ),
                )
            elif self.trace_root:
                trace.finish(self.trace_root, status)
            if source == "dynamic":
                return self.trace_root
        if source == "skill":
            identifier = trace.current_call("skill") or self.trace_nodes.get(key)
            if identifier is None:
                identifier = trace.start(
                    "skill",
                    "执行每周经营复盘脚本",
                    skill=ExecutionSkill(name="weekly-retail-review", version="1", stage="script"),
                )
            self.trace_nodes[key] = identifier
            if status != "running":
                trace.finish(identifier, status)
            return identifier
        labels = {
            "channel_compare": "渠道分析",
            "product_breakdown": "商品贡献",
            "scope_review": "口径复核",
            "preflight": "并行准备分析范围",
            "binding": "确认授权数据范围",
            "periods": "核对分析时段",
            "evidence": "核对预算提案依据",
            "budget_decision": "等待人工决策",
        }
        role = key.split(":")[0]
        identifier = self.trace_nodes.get(key)
        if identifier is None:
            parent = (
                trace.parent
                if source == "dynamic" and trace.parent != trace.steps.get(trace.active_step or "")
                else self.trace_root
            )
            if source == "fixed" and key in {"binding", "periods"}:
                parent = self.trace_nodes.get("preflight", parent)
            fields: dict = {"parentId": parent}
            kind = (
                "subagent"
                if source == "dynamic"
                else "group"
                if key == "preflight"
                else "narration"
            )
            if kind == "subagent":
                fields["subagent"] = ExecutionSubAgent(name=role)
            if kind == "group":
                fields["scheduling"] = "parallel"
            identifier = trace.start(kind, labels.get(role, "执行流程步骤"), **fields)
            self.trace_nodes[key] = identifier
        if status != "running":
            trace.finish(
                identifier, status, summary="该步骤需要补充数据" if detail == "needs_data" else None
            )
        if source == "fixed" and status == "waiting" and self.trace_root:
            trace.finish(self.trace_root, "waiting", summary="等待人工决策")
        return identifier

    async def asset_operation[T](self, operation: Callable[[], T]) -> T:
        async def settled() -> T:
            task = asyncio.create_task(asyncio.to_thread(operation))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                done, _ = await asyncio.wait({task}, timeout=0.5)
                if done and not task.cancelled():
                    task.exception()
                elif not done:
                    task.add_done_callback(
                        lambda item: None if item.cancelled() else item.exception()
                    )
                raise PhaseCanceled(cleanup_confirmed=bool(done)) from None

        return await self.control.perform(settled, timeout_s=5)

    async def dynamic(self, request: NewScript | ReuseScript) -> dict:
        if self.run.require_plan or self.run.plan.version:
            raise WorkflowError("plan_conflict")
        periods = checked_periods(request.args)
        if self.diagnosis_periods is not None and periods != self.diagnosis_periods:
            raise WorkflowError("workflow_scope_mismatch")
        asset = None
        if isinstance(request, NewScript):
            source = normalize_source(request.script_source)
            fenced = re.fullmatch(
                r"```(?:javascript|js)?[ \t]*\n(.*)\n```", source.strip(), re.I | re.S
            )
            if fenced:
                source = normalize_source(fenced.group(1))
            contract = request.entry_contract
            mode = "generated"
        else:
            asset = await self.asset_operation(
                lambda: self.assets.get(self.context, request.asset_id, active=True)
            )
            assert asset.script_source is not None
            if asset.definition_version != request.definition_version:
                raise WorkflowError("workflow_version_mismatch")
            source, contract = asset.script_source, asset.entry_contract
            mode = "reused"
        self.event("dynamic", mode, "running", asset=asset)
        evidence: dict[str, tuple[str, VerifiedQuery]] = {}
        evidence_nodes: dict[str, str] = {}

        async def dispatch(profile: str, raw: object) -> dict:
            self.control.require_running()
            if profile not in PROFILES or (
                contract == "channel_overview@1" and profile != "channel_compare"
            ):
                raise WorkflowError("workflow_profile_unavailable")
            dependency = None
            if profile == "channel_compare":
                candidate = WorkflowPeriods.model_validate(raw)
                channel = None
            else:
                inputs = ChildInput.model_validate(raw)
                dependency = evidence_nodes.get(inputs.lead_evidence_ref)
                candidate = WorkflowPeriods(baseline=inputs.baseline, current=inputs.current)
                receipt = evidence.get(inputs.lead_evidence_ref)
                if not receipt or receipt[0] != "channel_compare":
                    raise WorkflowError("workflow_evidence_invalid")
                channel = inputs.channel_code
            if candidate != periods:
                raise WorkflowError("workflow_scope_mismatch")
            key = f"{profile}:{uuid4()}"
            node_id = self.event("dynamic", key, "running")
            if self.trace and node_id and dependency:
                self.trace.update(node_id, dependsOn=[dependency])
            try:
                with self.trace.scope(node_id) if self.trace and node_id else nullcontext():
                    query = await self.child(profile, params_for(profile, periods, channel))
                if query is None or not query.result.rows:
                    self.event("dynamic", key, "failed", "needs_data")
                    return {"status": "needs_data", "evidenceRefs": []}
                evidence[query.id] = (profile, query)
                if node_id:
                    evidence_nodes[query.id] = node_id
                self.event("dynamic", key, "completed")
                return {
                    "status": "ok",
                    "evidenceRef": query.id,
                    "channelCode": query.ranked[0].code,
                }
            except (WorkflowError, ModelError, ValueError):
                self.event("dynamic", key, "failed")
                raise
            except asyncio.CancelledError:
                self.event("dynamic", key, "canceled")
                raise

        groups: dict[int, str] = {}

        def group_event(identifier: int, status: str) -> None:
            if self.trace is None:
                return
            if status == "running":
                groups[identifier] = self.trace.start(
                    "group", "并行分析", parentId=self.trace_root, scheduling="parallel"
                )
            elif identifier in groups:
                self.trace.finish(groups[identifier], status)

        async def grouped_dispatch(profile: str, raw: object, group_id: int | None) -> dict:
            parent_id = groups.get(group_id) if group_id is not None else self.trace_root
            with self.trace.scope(parent_id) if self.trace and parent_id else nullcontext():
                return await dispatch(profile, raw)

        async def execute() -> WorkflowReceipt:
            await self.confirm_binding()
            output = await execute_script(
                source,
                periods.model_dump(),
                dispatch,
                remaining=self.control.remaining,
                group_event=group_event,
                grouped_dispatch=grouped_dispatch,
            )
            result = ScriptResult.model_validate(output)
            if len(set(result.evidence_refs)) != len(result.evidence_refs) or any(
                ref not in evidence for ref in result.evidence_refs
            ):
                raise WorkflowError("workflow_evidence_invalid")
            receipt = WorkflowReceipt(
                source,
                contract,
                periods,
                mode == "generated",
                {
                    "status": result.status,
                    "evidenceRefs": result.evidence_refs,
                    "observations": [
                        {
                            "task": profile,
                            **json.loads(retail_observation(query.result, query_id=query.id)),
                        }
                        for profile, query in (evidence[ref] for ref in result.evidence_refs)
                    ],
                },
            )
            hooks = self.verifiers.get(contract, ())
            if asset is not None:
                hooks += self.verifiers.get(asset.asset_id, ())
            try:
                await verify_result(
                    receipt, VerificationContext(self.control.id, self.context, contract), hooks
                )
            except VerificationRejected as error:
                raise WorkflowError("workflow_verification_rejected") from error
            return receipt

        try:
            receipt = await self.control.perform(execute)
        except (WorkflowError, ValueError, ModelError):
            self.event("dynamic", mode, "failed", asset=asset)
            raise
        identifier = uuid4()
        if receipt.generated and receipt.result["status"] == "ok":
            self.save_offer = WorkflowDraftView(
                name=(
                    "渠道异动归因"
                    if receipt.contract == "channel_attribution@1"
                    else "渠道异动概览"
                ),
                entryContract=receipt.contract,
                scriptSource=receipt.source,
                args=receipt.periods,
                summary="",
            )
        self.event(
            "dynamic",
            mode,
            "completed" if receipt.result["status"] == "ok" else "failed",
            asset=asset,
        )
        return {"executionId": str(identifier), **receipt.result}

    async def tool(self, call: ToolCall) -> dict:
        name = call.function.name
        if registered := self.registered.get(name):
            periods = WorkflowPeriods.model_validate_json(call.function.arguments)
            observation = await self.dynamic(
                ReuseScript(
                    assetId=registered.asset_id,
                    definitionVersion=registered.definition_version,
                    args=periods,
                )
            )
            return {**observation, "workflow": registered.summary()}
        from compass_agent.tools.catalog import workflow_execute

        return await workflow_execute(name, self, call.function.arguments)
