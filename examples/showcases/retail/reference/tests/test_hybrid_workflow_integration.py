"""Feature 987 through the real JS runner, Agent and isolated PostgreSQL."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from compass_agent.models.errors import ModelError
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.schemas import (
    AnalysisRunInput,
    MainModelSelection,
    RunPeriod,
    StreamExtension,
    TenantScope,
    TrustedContext,
    WorkflowAssetPatch,
    WorkflowAssetRef,
)
from compass_agent.tools.builtin.find_workflows.catalog import RegisteredWorkflow
from compass_agent.workflows.assets import WorkflowAssets
from compass_agent.workflows.drafts import WorkflowDrafts
from psycopg import sql
from sqlalchemy import create_engine

from .retail_fixtures import DEFAULT_PERIODS
from .test_agent_run import PROVIDER_ID, FakeRouting
from .workflow_fixture import SOURCE, make_executor, new_retail, setup_retail


@pytest.fixture(scope="module")
def stores():
    admin = os.environ["AGENT_TEST_ADMIN_URL"]
    target = os.environ["AGENT_TEST_DATABASE_URL"]
    assert urlsplit(target).path.startswith("/compass_agent_test_")
    engine = create_engine(target)
    dsn = new_retail(admin)
    setup_retail(admin, dsn)
    try:
        yield WorkflowAssets(engine), dsn
    finally:
        engine.dispose()
        retail = urlsplit(dsn)
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(retail.path[1:]))
            )
            connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(retail.username or ""))
            )


def subject():
    return TrustedContext(
        actor_user_id=uuid4(),
        purpose="chat",
        scope=TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
    )


async def execute(
    assets, dsn, context, question, *, drafts=None, exercise_script_retry=False, **fields
):
    if "2025-" not in question:
        question += "，对比 2025-11-17 至 11-23 和 11-24 至 11-30。"
    executor, models = make_executor(
        assets,
        dsn,
        context.scope,
        workflow_drafts=drafts,
        exercise_script_retry=exercise_script_retry,
    )
    control = RunRegistry().register(OwnerKey.from_context(context))
    events = []
    command = AnalysisRunInput(
        requestId=uuid4(),
        question=question,
        selection=MainModelSelection(mode="fixed", provider_id=PROVIDER_ID),
        **fields,
    )
    result = await executor.run(command, context, control, events.append)
    return (
        result,
        models,
        [
            event.payload.data
            for event in events
            if isinstance(event, StreamExtension) and event.payload.name == "compass.workflow.node"
        ],
    )


def test_dynamic_showcase_repairs_failed_script_and_uses_real_retail(stores):
    assets, dsn = stores
    context = subject()
    drafts = WorkflowDrafts()
    result, models, steps = asyncio.run(
        execute(
            assets,
            dsn,
            context,
            "请生成并执行一个可按日期复用的动态工作流",
            drafts=drafts,
            showcase="dynamic_workflow",
            exercise_script_retry=True,
        )
    )
    assert result.status == "completed" and "独立复核" in result.final_answer
    assert models.generated == 2
    assert len(models.child_requests) == 3
    assert any(step["key"] == "generated" and step["status"] == "failed" for step in steps)
    offer = next(step["detail"] for step in steps if step["key"] == "save_offer")
    assert isinstance(offer, str)
    assert drafts.get(context, UUID(offer)).script_source == SOURCE
    assert not assets.list(context).items


@pytest.mark.parametrize(
    "instruction", ["再并行拆解前三项商品贡献并独立复核", "拆解前三项商品贡献，再独立复核"]
)
def test_plain_channel_diagnosis_selects_retail_and_keeps_requested_periods(
    stores, monkeypatch, instruction
):
    assets, dsn = stores
    choose = FakeRouting.select_route
    routes = []

    async def record_route(routing, *args, **kwargs):
        routes.append(kwargs["intent_hint"].scene_id == "SC-03")
        return await choose(routing, *args, **kwargs)

    monkeypatch.setattr(FakeRouting, "select_route", record_route)
    question = (
        "对比 2025-11-17 至 2025-11-23 和 2025-11-24 至 2025-11-30，"
        f"找出实付额变化最大的渠道，{instruction}。"
    )
    drafts = WorkflowDrafts()
    context = subject()
    result, models, _ = asyncio.run(execute(assets, dsn, context, question, drafts=drafts))

    assert routes == [True]
    assert result.status == "completed" and "独立复核" in result.final_answer
    assert len(models.child_requests) == 3
    assert all(
        request["period"] == DEFAULT_PERIODS.baseline.model_dump()
        and request["compareTo"] == DEFAULT_PERIODS.current.model_dump()
        for request in models.child_requests
    )
    assert not assets.list(context).items
    assert "retail_sales" in {tool.function.name for tool in models.parent_requests[0].tools}


def test_generated_asset_reused_under_same_owner_with_new_period(stores):
    assets, dsn = stores
    context = subject()
    drafts = WorkflowDrafts()

    async def scenario():
        first, models, steps = await execute(
            assets, dsn, context, "每周渠道诊断并独立复核", drafts=drafts
        )
        assert first.status == "completed", first
        assert "320.00" in first.final_answer and "SPU1" in first.final_answer
        assert models.generated == 1 and len(models.child_requests) == 3
        detail_id = next(step["detail"] for step in steps if step["key"] == "save_offer")
        assert isinstance(detail_id, str)
        draft_id = UUID(detail_id)
        assert assets.list(context).items == []
        with pytest.raises(ModelError) as forbidden:
            drafts.get(context.model_copy(update={"actor_user_id": uuid4()}), draft_id)
        assert forbidden.value.status == 404
        detail = drafts.get(context, draft_id)
        assert detail.args == DEFAULT_PERIODS and detail.summary == first.final_answer
        assert detail.script_source == SOURCE
        item = assets.save(context, detail.name, detail.script_source, detail.entry_contract)
        assert assets.get(context, item.asset_id).script_source == SOURCE

        reopened = WorkflowAssets(assets.engine)
        reused, models, steps = await execute(
            reopened,
            dsn,
            context,
            "使用已保存的工作流",
            drafts=drafts,
            workflowAssetRef=WorkflowAssetRef(
                assetId=item.asset_id,
                definitionVersion=1,
                baseline=RunPeriod(start="2025-11-10", end="2025-11-17"),
                current=DEFAULT_PERIODS.baseline,
            ),
        )
        assert reused.status == "completed", reused
        assert "150.00" in reused.final_answer
        assert models.generated == 0 and len(models.child_requests) == 3
        assert any(step["key"] == "reused" for step in steps)
        assert all(step["key"] != "save_offer" for step in steps)
        assert reopened.list(context).items[0].asset_id == item.asset_id

        # A fresh executor/connection has no conversation or selected workflow reference.
        # It must advertise the saved capability before the first parent model call.
        automatic, models, nodes = await execute(
            WorkflowAssets(assets.engine),
            dsn,
            context,
            "对比 2025-11-10 至 2025-11-16 和 2025-11-17 至 2025-11-23，"
            "找出实付额变化最大的渠道，再并行拆解前三项商品贡献并独立复核。",
            drafts=drafts,
        )
        assert automatic.status == "completed", automatic
        assert "150.00" in automatic.final_answer
        assert "2025-11-10" in automatic.final_answer and "2025-11-24" in automatic.final_answer
        binding = RegisteredWorkflow.from_asset(item)
        assert models.asset_calls == [binding.tool_name]
        assert models.generated == 0 and len(models.child_requests) == 3
        first_request = models.parent_requests[0]
        assert binding.tool_name in {tool.function.name for tool in first_request.tools}
        assert item.name in (first_request.messages[0].content or "")
        assert SOURCE not in (first_request.messages[0].content or "")
        assert len([message for message in first_request.messages if message.role == "user"]) == 1
        reused_nodes = [node for node in nodes if node["key"] == "reused"]
        assert reused_nodes[-1]["asset"] == {
            "assetId": str(item.asset_id),
            "definitionVersion": 1,
            "name": item.name,
        }
        assert all(node["key"] not in {"generated", "save_offer"} for node in nodes)
        assert len(reopened.list(context).items) == 1

        # The same catalog is not visible to another user in the same workspace.
        other = context.model_copy(update={"actor_user_id": uuid4()})
        _, foreign_models, _ = await execute(assets, dsn, other, "每周渠道诊断并独立复核")
        assert all(
            binding.tool_name not in {tool.function.name for tool in request.tools}
            for request in foreign_models.parent_requests
        )

        assets.patch(
            context,
            item.asset_id,
            WorkflowAssetPatch(metadataRevision=1, name=item.name, status="disabled"),
        )
        _, disabled_models, _ = await execute(assets, dsn, context, "每周渠道诊断并独立复核")
        assert not disabled_models.asset_calls
        assert all(
            binding.tool_name not in {tool.function.name for tool in request.tools}
            for request in disabled_models.parent_requests
        )

    asyncio.run(scenario())


def test_asset_scope_and_concurrent_deduplication(stores):
    assets, _dsn = stores
    context = subject()
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = list(
            pool.map(
                lambda name: assets.save(context, name, SOURCE, "channel_attribution@1"),
                ("A", "B"),
            )
        )
    assert one.asset_id == two.asset_id
    assert len(assets.list(context).items) == 1
    with pytest.raises(ModelError):
        assets.get(subject(), one.asset_id)
    changed = assets.patch(
        context,
        one.asset_id,
        WorkflowAssetPatch(
            metadataRevision=1,
            name="每周渠道",
            status="disabled",
        ),
    )
    assert changed.metadata_revision == 2
    with pytest.raises(ModelError):
        assets.get(context, one.asset_id, active=True)
    with pytest.raises(ModelError):
        assets.patch(
            context,
            one.asset_id,
            WorkflowAssetPatch(
                metadataRevision=1,
                name="过期",
                status="active",
            ),
        )
    reenabled = assets.patch(
        context,
        one.asset_id,
        WorkflowAssetPatch(
            metadataRevision=2,
            name="每周渠道",
            status="active",
        ),
    )
    assert reenabled.asset_id == one.asset_id and reenabled.definition_version == 1


def test_budget_question_uses_model_and_weekly_skill_returns_data(stores):
    assets, dsn = stores
    context = subject()

    async def scenario():
        budget, _, events = await execute(assets, dsn, context, "帮我准备广告预算方案")
        assert budget.status == "completed"
        assert budget.final_answer == "请提供预算目标与数据范围。"
        assert not events
        weekly, _, steps = await execute(assets, dsn, context, "请按每周经营复盘生成周报")
        assert weekly.status == "completed", weekly
        assert weekly.final_answer == "本周概览：数据已汇总。下周跟踪渠道变化。"
        assert any(node["key"] == "weekly-retail-review@1" for node in steps)

    asyncio.run(scenario())


def test_wrong_owner_and_weekly_gap_use_safe_results(stores):
    assets, dsn = stores
    owner = subject()
    outsider = owner.model_copy(update={"actor_user_id": uuid4()})
    asset = assets.save(owner, "当前用户脚本", SOURCE, "channel_attribution@1")

    async def scenario():
        result, models, _ = await execute(
            assets,
            dsn,
            outsider,
            "运行其他用户的工作流",
            workflowAssetRef=WorkflowAssetRef(
                assetId=asset.asset_id,
                definitionVersion=1,
                baseline=DEFAULT_PERIODS.baseline,
                current=DEFAULT_PERIODS.current,
            ),
        )
        assert result.status == "failed"
        assert not models.child_requests
        report, _, _ = await execute(
            assets,
            dsn,
            owner,
            "每周经营复盘 2025-11-03 至 11-09 对比 11-17 至 11-23",
        )
        assert report.status == "completed", report
        assert "周报未完成" in report.final_answer and "2025-11-03" in report.final_answer
        assert "611.00" in report.final_answer

    asyncio.run(scenario())
