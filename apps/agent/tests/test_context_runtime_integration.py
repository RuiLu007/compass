"""Exercise a published factory catalog through scoped assembly and durable model Steps."""

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from compass_agent.context_runtime.contracts import ContextRequest
from compass_agent.context_runtime.delivery import output_contract
from compass_agent.context_runtime.pipeline import pin, prepare_input
from compass_agent.context_runtime.profiles import PHASES, SCOPED_SCENE_FILES, TASKS, ScopeRule
from compass_agent.context_runtime.semantics import Dictionary, MetricCatalog
from compass_agent.data_connections.entities import DataConnection
from compass_agent.execution_context import current_execution
from compass_agent.harness.context import AgentDefinition
from compass_agent.harness.contracts import ToolResult
from compass_agent.llm.contracts import AgentCallResponse
from compass_agent.models.secrets import SecretStore
from compass_agent.runtime.context import RunRoute
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.runtime.entities import AgentStep
from compass_agent.runtime.journal import ExecutionJournal
from compass_agent.runtime.store import RuntimeStore
from compass_agent.runtime.task_state import TaskState
from compass_agent.runtime.turn import RuntimeTurn
from compass_agent.schemas import (
    ChatMessage,
    GenerateInput,
    ModelTokenLimits,
    SessionSubmit,
    TenantScope,
    ToolCall,
    ToolCallFunction,
    ToolDefinition,
    ToolFunction,
)
from compass_agent.settings import Settings
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from .context_fixture import capability_grants, install_prompts
from .test_agent_run import CHAT, PROVIDER_ID, model_result
from .test_database_tools_integration import db
from .test_database_tools_integration import targets as targets

SCENES = {
    "business_qa": "SC-01",
    "data_analysis": "SC-02",
    "dashboard": "SC-25",
    "business_report": "SC-29",
}
LIMITS = ModelTokenLimits(max_input_tokens=98304, max_output_tokens=4096)
PROVIDER_TOOLS = [
    ToolDefinition(function=ToolFunction(name=n))
    for n in ("todo_write", "database_query", "python_analysis")
]


def test_published_assembly_matrix_and_durable_deliveries(targets):
    url, _ = targets("context_matrix")
    db.migrate()
    engine = install_prompts(create_engine(url))
    try:
        owner = CHAT.model_copy(
            update={"scope": TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4())}
        )
        finance = owner.scope
        assert isinstance(finance, TenantScope)
        peers = [
            owner,
            owner.model_copy(
                update={"scope": finance.model_copy(update={"workspace_id": uuid4()})}
            ),
            owner.model_copy(update={"scope": finance.model_copy(update={"tenant_id": uuid4()})}),
            owner,
        ]
        key = Fernet.generate_key().decode()
        settings = Settings(
            compass_context_scope_profiles=[
                ScopeRule(
                    tenantId=finance.tenant_id, workspaceId=finance.workspace_id, profile="finance"
                ),
                ScopeRule(tenantId=finance.tenant_id, workspaceId=None, profile="operations"),
            ]
        )
        store = RuntimeStore(engine, key, settings=settings)
        store.ready = True
        dictionary = MetricCatalog()
        metric_ids = {}
        with Session(engine) as tx, tx.begin():
            records = []
            for i, account in enumerate(peers[:3]):
                c = account.scope
                assert isinstance(c, TenantScope)
                conn = uuid4()
                tx.add(
                    DataConnection(
                        id=conn,
                        tenant_id=c.tenant_id,
                        workspace_id=c.workspace_id,
                        name=f"matrix{i}",
                        engine="postgresql",
                        host="localhost",
                        port=5432,
                        database_name="fixture",
                        role="reader",
                        sslmode="disable",
                        password_ciphertext="unused",
                        revision=1,
                    )
                )
                metric_ids[str(c.tenant_id), str(c.workspace_id)] = f"orders-{i}"
                records.append(
                    {
                        "metricId": f"orders-{i}",
                        "name": "订单数",
                        "definition": f"部门{i}的已支付订单",
                        "tenantId": str(c.tenant_id),
                        "workspaceId": str(c.workspace_id),
                        "object": "订单",
                        "grain": "天",
                        "unit": "单",
                        "aggregation": "去重计数",
                        "revision": "1",
                        "source": str(conn),
                    }
                )
            catalog = Dictionary.model_validate({"version": "fixture", "metrics": records})
            dictionary.active = catalog.digest()
            dictionary.by_digest[dictionary.active] = catalog
            store.metric_catalog = dictionary

        samples = 0
        with Session(engine) as tx:
            for task in TASKS:
                for phase in PHASES:
                    for i, account in enumerate(peers):
                        binding = pin(tx, account, store)
                        scope = account.scope
                        assert isinstance(scope, TenantScope)
                        assert (
                            binding["scopeProfile"]
                            == ("finance", "operations", "platform", "finance")[i]
                        )
                        grants = {"analysis": True, "model": True, "plugins": i != 3}
                        request = ContextRequest(
                            str(uuid4()),
                            "model:1",
                            str(scope.tenant_id),
                            str(scope.workspace_id),
                            binding["scopeProfile"],
                            task,
                            SCENES[task],
                            phase,
                            binding["generation"],
                            1,
                        )
                        schema = output_contract(task) if phase == "generate" else None
                        compiled = prepare_input(
                            tx,
                            request,
                            binding,
                            GenerateInput(
                                messages=[ChatMessage(role="user", content="请核对订单数")],
                                tools=PROVIDER_TOOLS,
                                thinking=True,
                                reasoning_effort="low",
                                max_tokens=2048,
                            ),
                            LIMITS,
                            account,
                            grants,
                            dictionary,
                            as_of=date(2026, 1, 1),
                            variables={"period": "本周", "unit": "元", "audience": "经营负责人"},
                            output_schema=schema,
                        )
                        assert compiled.request.max_tokens == 2048
                        assert compiled.request.thinking is (schema is None)
                        assert compiled.request.reasoning_effort == (
                            "low" if schema is None else None
                        )
                        r = compiled.receipt
                        paths = {f["path"] for f in r["files"]}
                        assert f"shared/stages/{phase}.md" in paths
                        assert (f"tasks/{task}/system.md" in paths) == (phase != "identify")
                        assert (
                            SCOPED_SCENE_FILES[("business_report", "SC-29", "finance")] in paths
                        ) == (i in {0, 3} and task == "business_report" and phase != "identify")
                        assert r["metrics"] == [
                            metric_ids[str(scope.tenant_id), str(scope.workspace_id)]
                        ]
                        expected = (
                            []
                            if phase in {"identify", "generate"}
                            else ["todo_write"]
                            if phase == "plan"
                            else ["todo_write", "database_query"]
                        )
                        if phase in {"query", "analyze"} and i in {1, 2}:
                            expected.append("python_analysis")
                        assert r["tools"] == expected
                        assert ("metric-check" in r["skills"]) == (
                            i != 3 and "database_query" in expected
                        )
                        assert ("report-outline" in r["skills"]) == (
                            i != 3 and task == "business_report" and phase in {"plan", "generate"}
                        )
                        samples += 1
            # A scene with no dedicated Markdown still receives exactly its published SOP.
            from compass_agent.promptops.reader import PromptReader

            account = peers[0]
            binding = pin(tx, account, store)
            generation = binding["generation"]
            scope = account.scope
            assert isinstance(scope, TenantScope)
            scene_request = ContextRequest(
                str(uuid4()),
                "model:scene",
                str(scope.tenant_id),
                str(scope.workspace_id),
                binding["scopeProfile"],
                "data_analysis",
                "SC-03",
                "query",
                generation,
                1,
            )
            (scene_file,) = PromptReader.load_exact(tx, generation, ["support/routing/scenes.json"])
            scenes = json.loads(scene_file.body)["scenes"]
            assert len(scenes) == 40 and all(row["sop"] for row in scenes.values())
            initial = {
                "items": [
                    {
                        "kind": "term",
                        "name": "返工",
                        "summary": "复验未通过后的修复",
                        "grain": "每件",
                    }
                ],
                "owner_clarifications": [{"mention": "返工量", "definition": "按验收日期去重计件"}],
            }
            for search in ({}, {"search": {"kinds": ["semantic"], "skillEnabled": True}}):
                compiled = prepare_input(
                    tx,
                    scene_request,
                    {**binding, **search, "registry": {"pins": []}},
                    GenerateInput(messages=[ChatMessage(role="user", content="看看近期经营变化")]),
                    LIMITS,
                    account,
                    {"analysis": True, "model": True, "plugins": False},
                    dictionary,
                    variables={},
                    as_of=date(2026, 1, 1),
                    registry_context=initial,
                )
                system, first, *turns = compiled.request.messages
                assert turns[-1].role == "user"
                assert "看看近期经营变化" in (turns[-1].content or "")
                assert "按验收日期去重计件" in (turns[-1].content or "")
                assert "按验收日期去重计件" not in (first.content or "")
                assert system.role == "system" and scenes["SC-03"]["sop"] in system.content
                assert scenes["SC-01"]["sop"] not in system.content
                assert (
                    first.role == "user"
                    and json.loads(first.content or "{}")["items"] == initial["items"]
                )
        assert samples == 80
        asyncio.run(exercise_deliveries(engine, store, key, owner))
        asyncio.run(exercise_classifiers(engine, store, owner))
    finally:
        engine.dispose()


TOPICS = (
    ("已支付订单", "单", 41),
    ("到店客流", "人次", 73),
    ("退款笔数", "笔", 5),
    ("渠道成交额", "元", 6295),
    ("商品库存", "件", 208),
)


class DataTool:
    def __init__(self, state, engine, row):
        self.state, self.engine, self.row = state, engine, row

    def definitions(self):
        return PROVIDER_TOOLS[:2]

    async def dispatch(self, step, tools):
        if step.tool_calls[0].function.name == "todo_write":
            assert [t.function.name for t in tools] == ["todo_write"]
            self.state.record_tool_result(step, ToolResult("plan", "ok", '{"status":"planned"}'))
            return None
        assert "database_query" in [t.function.name for t in tools]
        label, unit, count = self.row
        with self.engine.connect() as conn:
            amount = conn.scalar(text("SELECT CAST(:value AS INTEGER)"), {"value": count})
        payload = {"resultId": "obs-current", "columns": [label], "rows": [[amount]], "unit": unit}
        self.state.record_tool_result(
            step, ToolResult("read", "ok", json.dumps(payload, ensure_ascii=False))
        )
        return None


class FixtureTurn(RuntimeTurn):
    def __init__(self, secrets, topic):
        self.models = cast(Any, SimpleNamespace(root_observation=None))
        self.definition = AgentDefinition()
        self.secrets, self.topic = secrets, topic
        self.invocations = 0

    async def _invoke_model_live(
        self, step, request, route, context, control, budget, emit=None, *, publish_text=True
    ):
        self.invocations += 1
        label, unit, count = self.topic
        answer = f"{label}为 {count} {unit}。"
        if (
            not request.continuation_token
            and request.tools
            and "obs-current" not in (request.messages[-1].content or "")
        ):
            planning = [t.function.name for t in request.tools] == ["todo_write"]
            call = ToolCall(
                id="plan" if planning else "read",
                function=ToolCallFunction(
                    name="todo_write" if planning else "database_query", arguments="{}"
                ),
            )
            token = self.secrets.seal("tool_continuation", {"approved": "fixture"})
            out = model_result(tool_calls=[call], token=token, finish="tool_calls")
        elif request.messages[-1].tool_call_id == "plan":
            out = model_result(text="已列出步骤。")
        elif request.output_format == "schema":
            assert all(t.function.name != "database_query" for t in request.tools)
            assert any(str(count) in (m.content or "") for m in request.messages)
            content = request.messages[-1].content
            assert content is not None and isinstance(route, RunRoute)
            material = json.loads(content)
            assert material["acceptedViews"] == [{"factRef": "obs-current", "columns": [label]}]
            source = material["acceptedViews"][0]
            if route.deliverable == "data_analysis" and "formatCorrection" not in material:
                invalid = model_result(text='{"summary":""}').model_dump()
                return AgentCallResponse.model_validate(
                    {**invalid, "status": "incomplete", "error_code": "schema_validation_failed"}
                )
            if route.deliverable == "business_report" and "formatCorrection" not in material:
                source = {**source, "factRef": "unrecognized-table"}
            draft = {
                "title": label,
                "summary": answer,
                "scope": "本期",
                "body": f"本期结果。{answer}",
                "views": [{"kind": "table", "title": label, **source}],
            }
            out = model_result(text=json.dumps(draft, ensure_ascii=False))
        else:
            assert str(count) in (request.messages[-1].content or "")
            out = model_result(text=answer)
        return AgentCallResponse.model_validate(out.model_dump())


class LocalPublisher:
    def require_owner(self):
        return None

    async def flush(self, _run_id):
        return None


async def exercise_deliveries(engine, store, key, owner):
    for task in TASKS:
        for topic in TOPICS:
            await exercise_delivery(engine, store, key, owner, task, topic)


async def exercise_delivery(engine, store, key, owner, task, topic):
    command = SessionSubmit(
        requestId=uuid4(),
        question=f"请按{task}形式交付本期{topic[0]}。",
        selection=cast(Any, {"mode": "fixed", "provider_id": PROVIDER_ID}),
    )
    environment = {
        "tenantName": "集成租户",
        "workspaceName": "经营团队",
        "timezone": "Asia/Shanghai",
    }
    run_id = UUID(store.submit(command, owner, run_environment=environment)["runId"])
    retry = store.submit(command, owner, run_environment={**environment, "tenantName": "改名"})
    assert retry["runId"] == str(run_id)
    epoch = store.claim(run_id, uuid4())
    secrets = SecretStore(key)
    turn = FixtureTurn(secrets, topic)
    route = RunRoute(
        PROVIDER_ID,
        1,
        "fixture",
        SCENES[task],
        "",
        False,
        True,
        deliverable=task,
        limits=LIMITS,
        requires_plan=task == "business_report",
    )

    async def perform_turn():
        journal = ExecutionJournal(store, LocalPublisher(), run_id, epoch)
        journal.capabilities = lambda: capability_grants(owner)
        journal.model_secrets = secrets
        state = TaskState(messages=[ChatMessage(role="user", content=command.question)])
        state.tools = cast(Any, DataTool(state, engine, topic))
        control = RunRegistry().register(OwnerKey.from_context(owner), run_id=run_id)
        token = current_execution.set(journal)
        try:
            return await turn.execute_turn(state, route, owner, control, None, lambda _event: None)
        finally:
            current_execution.reset(token)

    result = await perform_turn()
    assert result.status == "completed" and f"{topic[2]} {topic[1]}" in result.final_answer
    calls = turn.invocations
    replay = await perform_turn()
    assert replay == result and calls == turn.invocations
    if task == "business_qa":
        assert result._delivery is None
    else:
        data = result._delivery
        assert data is not None
        assert (
            data.media
            == {
                "business_report": "text/markdown",
                "data_analysis": "text/csv",
                "dashboard": "application/json",
            }[task]
        )
        assert str(topic[2]) in data.data.decode()
    with Session(engine) as tx:
        prepared = tx.scalars(
            select(AgentStep.prepared_context)
            .where(AgentStep.run_id == run_id)
            .order_by(AgentStep.ordinal)
        ).all()
        assert len(prepared) == calls and all(prepared)
        from zoneinfo import ZoneInfo

        accepted = store.read_run(run_id).created_at.astimezone(ZoneInfo("Asia/Shanghai"))
        phases = []
        for sealed in prepared:
            frame = store.private.open(run_id, "model-frame", sealed)
            messages = frame["input"]["request"]["messages"]
            assert messages[0]["content"].count("本轮运行环境") == 1
            assert accepted.isoformat(timespec="seconds") in messages[0]["content"]
            assert sum(m.get("content") == messages[1]["content"] for m in messages) == 1
            assert json.loads(messages[1]["content"]) == {
                "source": "runOrganization",
                "tenantName": "集成租户",
                "workspaceName": "经营团队",
            }
            phases.append(frame["receipt"]["phase"])
            assert frame["input"]["request"]["thinking"] is not (
                phases[-1] == "generate" and task != "business_qa"
            )
            assert frame["receipt"]["task"] == task and frame["receipt"]["generation"] > 0
        if task == "data_analysis":
            assert phases == ["query", "query", "analyze", "generate", "generate"]
        if task == "business_report":
            assert phases == ["plan", "plan", "query", "query", "generate", "generate"]
    store.finish(run_id, epoch, "completed", answer=result.final_answer, delivery=result._delivery)


async def exercise_classifiers(engine, store, owner):
    from compass_agent.context_runtime.classifier import call_classifier
    from compass_agent.models.errors import ModelError
    from compass_agent.routing.intent import decision_input, flash_input

    from .test_observability import observer, spans

    submit = SessionSubmit.model_validate(
        {
            "requestId": str(uuid4()),
            "question": "分析门店订单",
            "selection": {"mode": "fixed", "provider_id": str(PROVIDER_ID)},
        }
    )
    env = {"tenantName": "分类器租户", "workspaceName": "北区", "timezone": "Asia/Shanghai"}
    rid = UUID(store.submit(submit, owner, run_environment=env)["runId"])
    journal = ExecutionJournal(store, LocalPublisher(), rid, store.claim(rid, uuid4()))
    journal.capabilities = lambda: capability_grants(owner)
    metrics, exporter = observer(compass_observability_sample_ratio=1)
    root = metrics.root(uuid4(), owner, capture_content=False)
    sent = []

    async def dispatch(_command, _owner, role, _provider, prepared, *_args):
        sent.append(role)
        assert journal.prompt_facts is not None
        if role == "intent_jev":
            assert journal.prompt_facts.instructions in prepared.questions["scene"].instructions
            assert prepared.state["runOrganization"]["tenantName"] == env["tenantName"]
        else:
            assert journal.prompt_facts.instructions in prepared.messages[0].content
            assert json.loads(prepared.messages[1].content)["tenantName"] == env["tenantName"]
        assert "latest-private-query" in prepared.model_dump_json()
        assert "old-private-query" not in prepared.model_dump_json()
        return model_result(text="{}")

    provider = SimpleNamespace(
        id=PROVIDER_ID,
        binding_version=1,
        vendor="test",
        api_model="fixture",
        generation_profile=SimpleNamespace(effective_limits=LIMITS),
    )
    backend = SimpleNamespace(
        models=SimpleNamespace(
            root_observation=root, repository=SimpleNamespace(get=lambda _id: provider)
        ),
        _dispatch=dispatch,
    )
    command = SimpleNamespace(
        input=GenerateInput(
            messages=[
                ChatMessage(role="user", content="old-private-query" + "x" * 200_000),
                ChatMessage(role="assistant", content="old-private-answer"),
                ChatMessage(role="user", content="latest-private-query"),
            ]
        )
    )
    try:
        for role, factory in (("intent_jev", decision_input), ("intent_flash", flash_input)):
            value = factory(command.input, managed=True)
            args = (journal, backend, command, owner, role, PROVIDER_ID, value, None, None)
            answer = await call_classifier(*args)
            assert await call_classifier(*args) == answer
        assert sent == ["intent_jev", "intent_flash"]

        async def revoked():
            return {"analysis": False, "model": True, "plugins": True}

        journal.capabilities = revoked
        with pytest.raises(ModelError, match="forbidden"):
            await call_classifier(*args)
        assert len(sent) == 2
        root.end()
        exported = spans(metrics, exporter)
        events = [event for span in exported for event in span.events]
        assert {"prompt.used", "context.material", "context.compacted", "context.rejected"} <= {
            e.name for e in events
        }
        for span in exported:
            for values in [span.attributes, *(e.attributes for e in span.events)]:
                assert "private-query" not in str(values) and "private-answer" not in str(values)
        dropped = [
            event
            for event in events
            if event.name == "context.material"
            and event.attributes.get("langfuse.observation.metadata.reason") == "budget"
        ]
        assert len(dropped) == 8
        assert all(s.dropped_events == 0 for s in exported)
        with Session(engine) as tx:
            stored = tx.scalars(
                select(AgentStep.prepared_context).where(AgentStep.run_id == rid)
            ).all()
            assert len(stored) == 2 and all(stored)
    finally:
        root.end()
        metrics.shutdown()


@pytest.mark.parametrize(("search", "key"), [(False, "registry"), (True, "glossary")])
def test_empty_registry_release_accepts_and_pins_first_run(targets, monkeypatch, search, key):
    from compass_agent.documents import client as documents
    from compass_agent.semantic import client

    url, _ = targets("empty_registry")
    db.migrate()
    engine = install_prompts(create_engine(url))
    workspace = uuid4()
    owner = CHAT.model_copy(
        update={"scope": TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=workspace)}
    )
    settings = Settings(
        compass_semantic_workspaces=str(workspace),
        compass_search_workspaces=str(workspace) if search else "",
        compass_context_runtime_enabled=True,
    )
    store = RuntimeStore(engine, Fernet.generate_key().decode(), settings=settings)
    store.ready = True
    command = SessionSubmit.model_validate(
        {
            "requestId": str(uuid4()),
            "question": "我部门的指标如何计算？",
            "selection": {"mode": "fixed", "provider_id": str(PROVIDER_ID)},
        }
    )
    monkeypatch.setattr(documents, "accept", lambda *_: {"kinds": ["knowledge"]})
    monkeypatch.setattr(client, "request", lambda *_: {"manifest_hash": "e" * 64, "releases": []})
    try:
        run_id = UUID(store.submit(command, owner)["runId"])
        binding = store.read_run(run_id).config_snapshot["contextRuntime"]
        assert ("registry" in binding) is not search
        assert binding[key] == {
            "pins": [],
            "manifest": "e" * 64,
        }
    finally:
        engine.dispose()
