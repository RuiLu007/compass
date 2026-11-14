"""Deterministic model responses and an isolated Retail PostgreSQL fixture."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, LiteralString, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4, uuid5

import psycopg
from compass_agent.routing.workflows import periods_from_question
from compass_agent.runtime.context import compass_definition
from compass_agent.runtime.service import RunService
from compass_agent.schemas import (
    BudgetProposal,
    GenerateInput,
    RunWaiting,
    TenantScope,
    ToolCall,
    ToolCallFunction,
)
from compass_agent.tools.business.retail_sales.handler import RetailBinding
from compass_agent.workflows.assets import WorkflowAssets
from psycopg import sql
from pydantic import SecretStr

from .retail_fixtures import DEFAULT_PERIODS
from .test_agent_run import FakeModels, FakeRouting, model_result, selected_route
from .test_run_autonomy import profile
from .test_runtime import request

PERIODS = DEFAULT_PERIODS.model_dump()
SOURCE = """
const lead = await agent("channel_compare", args);
if (lead.status !== "ok") return { status: "needs_data", evidenceRefs: [] };
const next = {...args, channelCode: lead.channelCode, leadEvidenceRef: lead.evidenceRef};
const [product, review] = await parallel([
  () => agent("product_breakdown", next),
  () => agent("scope_review", next),
]);
if (product.status !== "ok" || review.status !== "ok") {
  return {status: "needs_review", evidenceRefs: [lead.evidenceRef]};
}
return {status: "ok", evidenceRefs: [lead.evidenceRef, product.evidenceRef, review.evidenceRef]};
"""


def workflow_answer(receipt):
    by_task = {item["task"]: item for item in receipt["observations"]}
    lead = by_task.get("channel_compare")
    if not lead or not lead["rows"]:
        return model_result(text="渠道数据不足，无法完成对比。")
    row = lead["rows"][0]
    delta = int(row["deltaCent"]) / 100
    products = "、".join(
        item["code"] for item in by_task.get("product_breakdown", {}).get("rows", [])
    )
    review = "，已独立复核" if "scope_review" in by_task else ""
    return model_result(
        text=(
            f"{lead['period']['start']} 至 {lead['compareTo']['end']}，"
            f"{row['code']} 渠道变动 {delta:.2f} 元，涉及商品 {products}{review}。"
        )
    )


class WorkflowModels(FakeModels):
    def __init__(self, exercise_script_retry: bool = False):
        super().__init__([])
        self.generated = 0
        self.child_requests = []
        self.parent_requests: list[GenerateInput] = []
        self.asset_calls: list[str] = []
        self.exercise_script_retry = exercise_script_retry

    async def run(self, prepared, value: GenerateInput, **kwargs):
        ledger = kwargs["attempt_budget"]
        req = request()
        ledger.profiles = (profile(req),)
        req = replace(req, input=value, context=replace(req.context, call_id=prepared.call_id))
        assert ledger.reserve(req, prepared.call_id)
        ledger.settle(prepared.call_id, None)
        request_messages = value.messages
        tools = {item.function.name for item in value.tools}
        observations = {
            message.tool_call_id: json.loads(message.content or "{}")
            for message in request_messages
            if message.role == "tool"
        }
        question = next(
            message.content or "" for message in request_messages if message.role == "user"
        )
        is_child = tools == {"retail_sales"} and question.startswith("{")
        if not is_child:
            self.parent_requests.append(value)
        registered = [
            item.function.name
            for item in value.tools
            if item.function.name.startswith("workflow_")
            and "前三项商品贡献" in item.function.description
        ]
        if is_child:
            assert len([message for message in request_messages if message.role == "user"]) == 1
            self.child_requests.append(json.loads(question))
            name, call_id, args = "retail_sales", "query", json.loads(question)
        elif any(
            (message.content or "").startswith("以下是本轮工作流返回的数据。")
            for message in request_messages
            if message.role == "user"
        ):
            handoff = next(
                message.content
                for message in request_messages
                if (message.content or "").startswith("以下是本轮工作流返回的数据。")
            )
            assert handoff
            return workflow_answer(json.loads(handoff.split("\n", 1)[1]))
        elif "复盘" in question or "周报" in question:
            review_periods = periods_from_question(question).model_dump()
            if "skill" not in observations:
                name, call_id, args = "load_weekly_review", "skill", {}
            elif "base" not in observations:
                name, call_id, args = (
                    "retail_sales",
                    "base",
                    {
                        "dimension": "channel",
                        "period": review_periods["baseline"],
                        "compareTo": None,
                    },
                )
            elif "current" not in observations:
                name, call_id, args = (
                    "retail_sales",
                    "current",
                    {
                        "dimension": "channel",
                        "period": review_periods["current"],
                        "compareTo": None,
                    },
                )
            elif "weekly" not in observations:
                name, call_id, args = "build_weekly_review", "weekly", review_periods
            else:
                data = observations["weekly"]
                if data["status"] == "needs_data":
                    missing = "、".join(item["start"] for item in data["missing"])
                    total = sum(item["paidCent"] for item in data["totals"]) / 100
                    return model_result(
                        text=f"周报未完成，缺失时段 {missing}。已有实付 {total:.2f} 元。"
                    )
                return model_result(text="本周概览：数据已汇总。下周跟踪渠道变化。")
        elif "每周" in question or "工作流" in question or "独立复核" in question:
            if registered and "生成并执行" not in question and "executed" not in observations:
                name, call_id, args = (
                    registered[0],
                    "executed",
                    periods_from_question(question).model_dump(),
                )
                self.asset_calls.append(name)
            elif (
                "find_workflows" in tools
                and "found" not in observations
                and "executed" not in observations
            ):
                name, call_id, args = "find_workflows", "found", {}
            elif "executed" not in observations:
                items = observations.get("found", {}).get("items", [])
                if items and "生成并执行" not in question:
                    asset = items[0]
                    name, call_id, args = (
                        "reuse_workflow",
                        "executed",
                        {
                            "assetId": asset["assetId"],
                            "definitionVersion": 1,
                            "args": periods_from_question(question).model_dump(),
                        },
                    )
                else:
                    self.generated += 1
                    name, call_id, args = (
                        "run_dynamic_workflow",
                        "executed",
                        {
                            "scriptSource": "return }" if self.exercise_script_retry else SOURCE,
                            "entryContract": "channel_attribution@1",
                            "args": periods_from_question(question).model_dump(),
                        },
                    )
            elif (
                observations["executed"].get("code") == "script_failed"
                and "repaired" not in observations
            ):
                assert "重新调用 run_dynamic_workflow" in observations["executed"]["nextAction"]
                self.generated += 1
                name, call_id, args = (
                    "run_dynamic_workflow",
                    "repaired",
                    {
                        "scriptSource": f"```javascript\n{SOURCE}\n```",
                        "entryContract": "channel_attribution@1",
                        "args": PERIODS,
                    },
                )
            else:
                return workflow_answer(observations.get("repaired") or observations["executed"])
        else:
            return model_result(text="请提供预算目标与数据范围。")
        assert name in tools, tools
        return model_result(
            tool_calls=[
                ToolCall(
                    id=call_id, function=ToolCallFunction(name=name, arguments=json.dumps(args))
                )
            ],
            token="fixture",
            finish="tool_calls",
        )


class LegacyApprovalFixture(RunService):
    """Recreate a previously supported approval wait for recovery tests."""

    async def _execute_path(
        self, command, state, route, context, control, controlled, emit, **options
    ):
        if command.showcase != "budget_proposal":
            return await super()._execute_path(
                command, state, route, context, control, controlled, emit, **options
            )
        assert controlled and state.hybrid and isinstance(context.scope, TenantScope)
        from compass_agent.workflows.service import params_for

        periods = periods_from_question(command.question)
        state.hybrid.event("fixed", "preflight", "running")
        proof = await control.perform(
            lambda: state.hybrid.child("channel_compare", params_for("channel_compare", periods))
        )
        assert proof
        wait_id = uuid5(control.id, "legacy-budget-test")
        state.hybrid.event("fixed", "budget_decision", "waiting")
        return RunWaiting(
            status="waiting",
            runId=str(control.id),
            waitId=str(wait_id),
            proposal=BudgetProposal(
                proposalId=str(uuid5(wait_id, "proposal")),
                campaignKey="classroom-retail",
                tenantId=str(context.scope.tenant_id),
                workspaceId=str(context.scope.workspace_id),
                deltaCent="-5000",
                summary="历史预算提案",
                evidenceRefs=[proof.id],
            ),
        )


def make_executor(
    assets: WorkflowAssets,
    retail_dsn: str,
    scope: TenantScope,
    *,
    exercise_script_retry=False,
    legacy_approval=False,
    **overrides,
):
    models = WorkflowModels(exercise_script_retry)
    binding = RetailBinding(
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        retail_tenant_id=101,
        role=urlsplit(retail_dsn).username or "compass_workflow_retail",
        dsn=SecretStr(retail_dsn),
    )
    runner = LegacyApprovalFixture if legacy_approval else RunService
    return runner(
        cast(Any, models),
        cast(Any, FakeRouting(selected_route())),
        lambda requested: binding if requested == scope else None,
        autonomy_profiles=(profile(request()),),
        workflow_assets=assets,
        **overrides,
        definition=compass_definition(),
    ), models


def setup_retail(admin_url: str, dsn: str) -> None:
    target = urlsplit(dsn)
    database = target.path.lstrip("/")
    assert database.startswith("compass_retail_test_") and target.username and target.password
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(target.username),
                sql.Literal(target.password),
            )
        )
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    admin_target = urlunsplit(urlsplit(admin_url)._replace(path=target.path))
    with psycopg.connect(admin_target) as connection:
        connection.execute(
            sql.SQL(
                cast(LiteralString, (Path(__file__).parent / "workflow_retail.sql").read_text())
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA core TO {}").format(sql.Identifier(target.username))
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA core TO {}").format(
                sql.Identifier(target.username),
            )
        )


def new_retail(admin_url: str) -> str:
    admin = urlsplit(admin_url)
    suffix = uuid4().hex[:10]
    return urlunsplit(
        admin._replace(
            netloc=f"workflow_{suffix}:test-only@{admin.hostname}:{admin.port}",
            path=f"/compass_retail_test_{suffix}",
        )
    )
