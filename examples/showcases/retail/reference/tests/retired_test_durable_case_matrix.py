"""Real Retail/QuickJS cases retain every public object after restart and stream expiry."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from compass_agent.harness.autonomy import AutonomyLimits
from compass_agent.runtime.context import compass_definition
from compass_agent.runtime.entities import AgentArtifact, AgentAttempt, AgentCheckpoint
from compass_agent.runtime.public_projection import reduce_packet
from compass_agent.runtime.resources import RuntimeResources
from compass_agent.runtime.service import RunService
from compass_agent.schemas import AgentResponse, TenantScope
from compass_agent.workflows.assets import WorkflowAssets
from compass_agent.workflows.drafts import WorkflowDrafts
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .durable_matrix_cases import canceled_sql, failed_python, failed_sql
from .query_fixtures import provider_for
from .test_agent_run import CHAT, FakeModels, FakeRouting, model_result, selected_route
from .test_agent_run_integration import AD_HOC_SQL, _required_target
from .test_database_tools_integration import targets as targets
from .test_durable_runtime_integration import (
    DYNAMIC,
    QUESTION,
    command,
    decision,
    execute,
    prepare,
    service,
)
from .test_run_autonomy import (
    ScriptedModels,
    channel,
    complete,
    definition,
    output,
    product,
    tool,
)
from .test_run_autonomy import (
    select as pick,
)
from .workflow_fixture import make_executor


def observation(request, call_id):
    return next(
        json.loads(message.content)
        for message in request.messages
        if message.role == "tool" and message.tool_call_id == call_id
    )


def retail_answer(request):
    channel_row = observation(request, "c")["rows"][0]
    product_rows = observation(request, "p")["rows"]
    products = "、".join(row["name"] for row in product_rows)
    return model_result(text=f"{channel_row['code']} 渠道的主要商品贡献：{products}")


def retail_case(name):
    binding = _required_target().binding
    question = "比较2025-11-17到2025-11-23与2025-11-24到2025-11-30的渠道和商品"
    limits = None
    autonomous = True
    if name == "legacy-diagnosis":
        autonomous = False
        models = FakeModels(
            [
                model_result(tool_calls=(channel(),), token="fixture", finish="tool_calls"),
                model_result(tool_calls=(product(),), token="fixture", finish="tool_calls"),
                model_result(text="POS 渠道实付下降，商品贡献已完成查询。"),
            ]
        )
    elif name == "ad-hoc-sql":
        question = "列出数据表并执行只读 JOIN 查询"
        models = ScriptedModels(
            [
                output(tool("database_query", "tables", op="list_tables")),
                output(
                    tool(
                        "database_query",
                        "describe",
                        op="describe_table",
                        schema="core",
                        table="spu",
                    )
                ),
                output(tool("database_query", "query", op="query", sql=AD_HOC_SQL)),
                lambda request: model_result(
                    text="查询商品：" + observation(request, "query")["rows"][0][0]
                ),
            ]
        )
    elif name == "planned-diagnosis":
        question = "请先列计划：" + question
        models = ScriptedModels(
            [
                output(definition(), pick("t1"), channel()),
                lambda request: output(
                    complete("t1", observation(request, "c")["queryId"]), pick("t2"), product()
                ),
                lambda request: output(complete("t2", observation(request, "p")["queryId"])),
                retail_answer,
            ]
        )
    elif name == "model-synthesis":
        models = ScriptedModels(
            [
                output(channel()),
                output(product()),
                retail_answer,
            ]
        )
    elif name == "tool-budget":
        limits = AutonomyLimits(tools=1)
        models = ScriptedModels([output(channel()), output(product())])
    elif name == "missing-period":
        models = ScriptedModels(
            [
                output(
                    tool(
                        "retail_sales",
                        "future",
                        dimension="channel",
                        period={"start": "2035-01-01", "end": "2035-01-08"},
                        compareTo={"start": "2035-01-08", "end": "2035-01-15"},
                    )
                ),
                model_result(text="所选期间缺少数据。"),
            ]
        )
    else:
        raise AssertionError(name)
    runner = RunService(
        cast(Any, models),
        cast(Any, FakeRouting(selected_route())),
        tools_provider=provider_for(lambda _: binding),
        autonomy_profiles=() if autonomous else None,
        autonomy_limits=limits,
        definition=compass_definition(),
    )
    return runner, models, command(question=question)


def public_state(snapshot):
    view, messages = {"schemaVersion": 1, "run": None, "objects": {}}, {}
    for packet in snapshot["packets"]:
        reduce_packet(view, messages, packet)
    return view, messages


def test_previous_lessons_durable_case_matrix(targets):
    engine = prepare(targets)
    cases = []
    identifiers = []

    async def capture(runtime, name, run):
        assert run.status in {"completed", "failed", "canceled"}, (
            name,
            run.status,
            run.reason_code,
        )
        await runtime.publisher.flush(run.id)
        snapshot = runtime.reader.messages(run.session_id, CHAT).model_dump(
            mode="json", by_alias=True
        )
        rows = await runtime.streams.rows(run.id)
        sse = [
            AgentResponse.model_validate(
                {
                    "protocol": "compass.run",
                    "version": 2,
                    "sessionId": str(run.session_id),
                    "mode": "delta",
                    "packets": [{**row["packet"], "seq": seq} for seq, row in batch],
                    "cursors": [{"runId": str(run.id), "seq": batch[-1][0]}],
                    "page": None,
                }
            ).model_dump(mode="json", by_alias=True)
            for offset in range(0, len(rows), 16)
            for batch in [rows[offset : offset + 16]]
        ]
        live = {"packets": [packet for frame in sse for packet in frame["packets"]]}
        assert public_state(live) == public_state(snapshot), name
        with Session(engine) as tx:
            count = tx.scalar(
                select(func.count()).select_from(AgentAttempt).where(AgentAttempt.run_id == run.id)
            )
            files = tx.scalars(select(AgentArtifact).where(AgentArtifact.run_id == run.id)).all()
            original_files = {
                str(file.id): (file.name, file.sha256, file.content) for file in files
            }
        cases.append({"name": name, "snapshot": snapshot, "sse": sse})
        identifiers.append((run.id, run.session_id, snapshot, count, original_files))

    async def scenario():
        async with service(engine) as runtime:
            for name, exercise in [
                ("sql-failure", failed_sql),
                ("sql-canceled", canceled_sql),
                ("python-error", failed_python),
            ]:
                await capture(runtime, name, await exercise(runtime))
            await capture(
                runtime,
                "nas-collection-failure",
                await failed_python(runtime, missing_nas_file=True),
            )
            for name in [
                "legacy-diagnosis",
                "ad-hoc-sql",
                "planned-diagnosis",
                "model-synthesis",
                "tool-budget",
                "missing-period",
            ]:
                worker, models, value = retail_case(name)
                run = await execute(runtime, value, worker)
                await capture(runtime, name, run)
                if name in {"planned-diagnosis", "model-synthesis"}:
                    assert run.status == "completed"
                    with Session(engine) as tx:
                        checkpoint = tx.get(AgentCheckpoint, (run.id, run.checkpoint_no))
                        assert checkpoint and checkpoint.plan_state["states"]
                        private = runtime.store.private.open(
                            run.id, "verifier", checkpoint.verifier_state
                        )
                        assert private["states"] and checkpoint.budget_state["states"]
            assert isinstance(CHAT.scope, TenantScope)
            assets = WorkflowAssets(engine)
            for name, question, showcase, repair in [
                ("dynamic-workflow", DYNAMIC, "dynamic_workflow", False),
                ("dynamic-script-repair", DYNAMIC, "dynamic_workflow", True),
                (
                    "weekly-skill",
                    "请按每周经营复盘，对比2025-11-17至11-23和11-24至11-30完成周报",
                    "weekly_review",
                    False,
                ),
                ("budget-approve", QUESTION, "budget_proposal", False),
                ("budget-reject", QUESTION, "budget_proposal", False),
            ]:
                worker, models = make_executor(
                    assets,
                    os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"],
                    CHAT.scope,
                    exercise_script_retry=repair,
                    workflow_drafts=WorkflowDrafts(RuntimeResources(runtime.store)),
                    legacy_approval=name.startswith("budget"),
                )
                run = await execute(runtime, command(question=question, showcase=showcase), worker)
                if name.startswith("budget"):
                    assert run.status == "waiting"
                    approved = name == "budget-approve"
                    runtime.hitl.decide(
                        run.id,
                        decision(runtime, run, "approve" if approved else "reject"),
                        CHAT,
                        may_approve=approved,
                        boot_id=runtime.boot_id,
                    )
                    if approved:
                        runtime.schedule(run.id, worker, CHAT)
                        await runtime.tasks[run.id]
                    run = runtime.store.read_run(run.id)
                if name == "dynamic-workflow":
                    snapshot = runtime.reader.messages(run.session_id, CHAT)
                    draft = next(
                        p.event.payload.value
                        for p in snapshot.packets
                        if p.event.type == "view.upsert" and p.event.payload.kind == "workflow"
                    )
                    resources = RuntimeResources(runtime.store)
                    saved = resources.confirm(CHAT, UUID(draft.draft_id), "持久矩阵工作流")
                    assert saved is not None
                    reused_worker, reused_models = make_executor(
                        assets,
                        os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"],
                        CHAT.scope,
                        workflow_drafts=WorkflowDrafts(resources),
                    )
                    reused = await execute(
                        runtime,
                        command(
                            question="使用已保存的工作流",
                            workflowAssetRef={
                                "assetId": str(saved.asset_id),
                                "definitionVersion": 1,
                                "baseline": {"start": "2025-11-10", "end": "2025-11-17"},
                                "current": {"start": "2025-11-17", "end": "2025-11-24"},
                            },
                        ),
                        reused_worker,
                    )
                    assert reused.status == "completed" and reused_models.generated == 0
                    assert len(reused_models.child_requests) == 3
                    await capture(runtime, "saved-workflow-reuse", reused)
                await capture(runtime, name, run)
        async with service(engine) as restarted:
            for identifier, session_id, before, count, files in identifiers:
                await restarted.streams.writer.delete(restarted.streams.key(identifier))
                after = restarted.reader.messages(session_id, CHAT).model_dump(
                    mode="json", by_alias=True
                )
                assert public_state(after) == public_state(before)
                with Session(engine) as tx:
                    assert (
                        tx.scalar(
                            select(func.count())
                            .select_from(AgentAttempt)
                            .where(AgentAttempt.run_id == identifier)
                        )
                        == count
                    )
                for key, original in files.items():
                    value = restarted.store.artifact(identifier, UUID(key), CHAT)
                    assert (value.name, value.sha256, value.content) == original
        output_dir = os.environ.get("COMPASS_DURABLE_MATRIX_OUTPUT_DIR")
        if output_dir:
            target = Path(output_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / "durable-matrix.json").write_text(json.dumps(cases, ensure_ascii=False))

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()
