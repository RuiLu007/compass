"""Cross the Agent dispatcher, PostgreSQL journal, Redis and Web owner for source acceptance."""

import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from compass_agent.context_runtime.search_control import SearchDispatch
from compass_agent.context_runtime.source_access import (
    allows,
    assistant_message,
    project_sources,
    read_private,
    record_sources,
    require_run_sources,
)
from compass_agent.data_connections.connections import DataConnections
from compass_agent.data_connections.tools import provider
from compass_agent.documents.service import DocumentTools
from compass_agent.execution_context import current_execution
from compass_agent.harness.autonomy import AutonomyBudget, AutonomyLimits
from compass_agent.models.errors import ModelError
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.runtime.durable_service import DurableRuntime
from compass_agent.runtime.journal import ExecutionJournal
from compass_agent.runtime.message_reader import MessageReader
from compass_agent.runtime.redis_stream import OutboxPublisher
from compass_agent.runtime.tool_bindings import RunToolBindings
from compass_agent.schemas import (
    DataConnectionSave,
    SessionSubmit,
    ToolCall,
    ToolCallFunction,
    TrustedContext,
)
from compass_agent.semantic.service import RegistryTools
from compass_agent.settings import Settings
from compass_agent.tools.artifacts import ArtifactStore
from compass_agent.tools.batch import dispatch_batch
from compass_agent.tools.builtin.todo_write import execute as execute_todo
from compass_agent.tools.builtin.todo_write.plan import ExecutionPlan
from compass_agent.tools.catalog import registrations
from compass_agent.tools.catalog_contracts import CatalogContext
from compass_agent.tools.contracts import ToolBatchPolicy, ToolExecutionContext
from compass_agent.tools.dispatcher import ExecutionSlots, ToolDispatcher
from compass_agent.tools.registry import ToolRegistry
from sqlalchemy import create_engine


def publish_long_collection(root, tenant, workspace):
    """Publish isolated acceptance paragraphs; keep the classroom source edition intact."""
    from materials import atomic_json, sha

    pointer = root / "bindings" / tenant / (workspace + ".json")
    binding = json.loads(pointer.read_bytes())
    folder = root / "collections"
    collection = json.loads((folder / (binding["collectionDigest"] + ".json")).read_bytes())
    calendar = next(d for d in collection["documents"] if d["docId"] == "E-CAL-2025")
    offer = next(d for d in collection["documents"] if d["docId"] == "E-1009")
    paragraphs = []
    for index in range(64):
        body = f"独立验收数据第 {index + 1} 段"
        section = dict(
            sectionId=f"acceptance-{index}",
            heading=f"测试段 {index}",
            text=body,
            sectionHash=sha(body.encode()),
        )
        calendar["sections"].append(section)
        paragraphs.append((calendar, section))
    paragraphs.append((offer, offer["sections"][0]))
    raw = json.dumps(collection, ensure_ascii=False).encode()
    binding["collectionDigest"] = sha(raw)
    atomic_json(folder / (sha(raw) + ".json"), raw)
    atomic_json(pointer, json.dumps(binding, ensure_ascii=False).encode())
    return paragraphs


async def exercise_runtime(
    db, tenant, workspace, group, member, actor, grant, release, connection, web
):
    url = os.environ["DATABASE_URL"]
    if not urlsplit(url).path.startswith("/compass_agent_test_"):
        raise ValueError("a dedicated Agent test database is required")
    engine = create_engine(url)
    trusted = TrustedContext.model_validate(
        {
            "purpose": "chat",
            "actor_user_id": actor,
            "scope": {"kind": "tenant", "tenant_id": tenant, "workspace_id": workspace},
        }
    )
    fields = json.loads(connection.read_text())
    settings = Settings(
        compass_search_workspaces=workspace,
        compass_semantic_workspaces="",
        compass_web_authz_url=web,
        compass_retail_allowed_endpoints=f"{fields['host']}:{fields['port']}",
    )
    runtime = DurableRuntime(engine, settings, RunRegistry())
    store, streams = runtime.store, runtime.streams
    await streams.ready()
    store.ready = True
    # Flush only the synthetic Run. Other Runs can be served by a real worker.
    publisher = runtime.publisher = OutboxPublisher(store, streams)
    connections = DataConnections(engine, settings)
    fixed = connections.save_connection(
        trusted.scope,
        DataConnectionSave.model_validate({**fields, "name": "验收时点副本", "revision": 0}),
        None,
    )
    extra = connections.save_connection(
        trusted.scope,
        DataConnectionSave.model_validate(
            {**fields, "name": "未绑定的来源", "retail_tenant_id": None, "revision": 0}
        ),
        None,
    )
    assert fixed.id != extra.id and str(fixed.id) == workspace
    release()

    async def start():
        command = SessionSubmit.model_validate(
            {
                "requestId": str(uuid4()),
                "question": "查询当前材料",
                "selection": {"mode": "fixed", "provider_id": str(uuid4())},
            }
        )
        submitted = await asyncio.to_thread(store.submit, command, trusted)
        rid = UUID(submitted["runId"])
        await publisher.flush(rid)
        epoch = await asyncio.to_thread(store.claim, rid, uuid4())
        await publisher.flush(rid)
        return ExecutionJournal(store, publisher, rid, epoch)

    async def assemble(journal):
        control = RunRegistry().register(
            OwnerKey.from_context(trusted), run_id=journal.run_id, lifetime_s=600
        )
        budget = AutonomyBudget((), AutonomyLimits(tools=16))
        plan = ExecutionPlan()
        tool_step = SimpleNamespace(id=uuid4(), index=1)
        # Reuse the production plan replay callback and the real local todo handler.
        bindings = SimpleNamespace(todo_plan=plan, state=SimpleNamespace(steps=[tool_step]))

        def restore(prepared, context, receipt):
            tool_step.id = context.step_id
            RunToolBindings.restore_plan(bindings, prepared, context, receipt)

        meter = SearchDispatch(journal, journal.search_snapshot, budget, restore)

        def handler(name):
            assert name == "todo_write"

            async def invoke(args, ctx):
                return await execute_todo(plan, args, ctx, tool_step.index)

            return invoke

        details = await provider(connections)(trusted, control, lambda _event: None)
        docs = registrations(
            CatalogContext(
                frozenset({"documents", "todo"}), documents=DocumentTools(journal, trusted)
            ),
            handler,
        )
        items = (*docs, *details)
        registry = ToolRegistry(items)
        dispatcher = ToolDispatcher(registry, ExecutionSlots(), ArtifactStore())
        context = ToolExecutionContext(
            trusted,
            journal.run_id,
            uuid4(),
            control,
            budget,
            frozenset(registry.entries),
            search_dispatch=meter.invoke,
        )

        async def call(name, args, *, step=None, call_id=None):
            c = replace(context, step_id=step or uuid4())
            entry = ToolCall(
                id=call_id or "c_" + uuid4().hex,
                function=ToolCallFunction(name=name, arguments=json.dumps(args)),
            )
            return (await dispatch_batch(dispatcher, (entry,), c, ToolBatchPolicy()))[0]

        return budget, call, list(registry.entries), plan

    journal = await start()
    marker = current_execution.set(journal)
    try:
        budget, call, _, plan = await assemble(journal)
        # This tenant owns a distinct digest. Withhold its copy to exercise a real 503
        # from the document owner without changing any other tenant's collection.
        path = (
            Path(os.environ["COMPASS_DOCUMENTS_ROOT"])
            / "collections"
            / (journal.search_snapshot["collectionDigest"] + ".json")
        )
        missing = path.with_suffix(".unavailable")
        path.rename(missing)
        try:
            failed = await call("search_documents", {"query": "报价", "kind": "external"})
            assert (failed.reason_code, budget.tools) == ("document_service_unavailable", 2), (
                failed,
                budget.tools,
            )
            blocked = await call("search_documents", {"query": "报价", "kind": "external"})
            assert blocked.reason_code == "source_blocked" and budget.tools == 2, (
                blocked,
                budget.tools,
            )
            rewritten = await call("search_documents", {"query": "当期媒介排期"})
            assert rewritten.reason_code == "source_blocked" and budget.tools == 2
        finally:
            missing.rename(path)
        results = await call("database_query", {"op": "list_connections"})
        assert [i["id"] for i in results.payload["connections"]] == [str(fixed.id)], results
        rejected = await call(
            "database_query", {"op": "list_tables", "connectionId": str(extra.id)}
        )
        assert rejected.status == "error" and rejected.payload["code"] == "connection_required", (
            rejected
        )
        actual = await call(
            "database_query",
            {
                "op": "query",
                "connectionId": str(fixed.id),
                "sql": "SELECT tenant_id FROM core.tenant",
            },
        )
        assert actual.status == "ok" and actual.payload["rows"] == [[fields["retail_tenant_id"]]], (
            actual
        )
        with store.transaction() as tx:
            retained = assistant_message(tx, journal.run_id).source_refs
            witness = next(r for r in retained if r["kind"] == "retail")
        unauthorized = {**witness, "keys": [{**witness["keys"][0], "tenant": -1}]}
        assert allows(store, trusted, [witness, witness])
        assert not allows(store, trusted, [witness, unauthorized])
        # The restored owner becomes eligible for one probe after its backoff.
        with store.transaction() as tx:
            ledger = read_private(store, tx, journal.run_id, "search-allowance")
            retry_at = max(row.get("retryAt", 0) for row in ledger)
        await asyncio.sleep(max(0, retry_at - time.time()))
        previous = budget.tools
        a, b = await asyncio.gather(
            *(
                call("search_documents", {"query": "E-1009 档位", "kind": "external"})
                for _ in range(2)
            )
        )
        assert a.status == b.status == "ok" and budget.tools == previous + 1, (
            a.status,
            a.reason_code,
            b.status,
            b.reason_code,
            previous,
            budget.tools,
        )
        with store.transaction() as tx:
            ledger = read_private(store, tx, journal.run_id, "search-allowance")
            assert sum(1 for item in ledger if item.get("failure") and item.get("retryable")) == 2
        offer = next(s["ref"] for s in a.payload["sections"] if s["heading"] == "本期排期")
        opened = await call("read_document", {"ref": offer})
        assert "6,000" in opened.payload["sections"][0]["text"]
        uses = budget.tools
        reopened = await call("read_document", {"ref": offer})
        assert reopened.status == "ok" and budget.tools == uses
        await asyncio.to_thread(
            require_run_sources, store, journal.run_id, trusted, epoch=journal.epoch
        )
        initial = dict(
            op="replace",
            baseVersion=0,
            items=[{"id": "t1", "goal": "核对已接收的来源"}],
            selectTodoId="t1",
        )
        final = dict(op="complete", baseVersion=1, todoId="t1")
        first, second = uuid4(), uuid4()
        used_before_todo = budget.tools
        await call("todo_write", initial, step=first, call_id="plan_new")
        await call("todo_write", final, step=second, call_id="plan_done")
        assert plan.status == {"t1": "verified"} and budget.tools == used_before_todo + 2
        await call("todo_write", final, step=second, call_id="plan_done")
        assert budget.tools == used_before_todo + 2
        old_epoch = journal.epoch
        await runtime.pause_interrupted(journal.run_id, old_epoch, "process_interrupted")
        replacement = await asyncio.to_thread(store.claim, journal.run_id, uuid4())
        journal = ExecutionJournal(store, publisher, journal.run_id, replacement)
        journal.validate_checkpoint()
        assert old_epoch != replacement
        try:
            await asyncio.to_thread(
                record_sources,
                SimpleNamespace(store=store, run_id=journal.run_id, epoch=old_epoch),
                [],
            )
        except ModelError as exc:
            assert exc.code == "execution_fenced"
        else:
            raise AssertionError("previous owner retained write authority")
        current_execution.set(journal)
        restored_budget, replay, _, restored_plan = await assemble(journal)
        await replay("todo_write", initial, step=first, call_id="plan_new")
        await replay("todo_write", final, step=second, call_id="plan_done")
        await replay("todo_write", final, step=second, call_id="plan_done")
        assert (
            restored_plan.verified == plan.verified and restored_plan.snapshot() == plan.snapshot()
        )
        assert restored_budget.tools == 2 and restored_plan.first_step == 1
        with store.transaction() as tx:
            assert len(read_private(store, tx, journal.run_id, "search-allowance")) == budget.tools
        nid = str(uuid4())
        await asyncio.to_thread(
            store.progress,
            journal.run_id,
            journal.epoch,
            [
                {
                    "type": "view.upsert",
                    "payload": {
                        "objectId": nid,
                        "revision": 1,
                        "kind": "execution.node",
                        "value": {
                            "id": nid,
                            "revision": 1,
                            "kind": "model",
                            "status": "completed",
                            "title": "分析请求",
                            "startedAtMs": 1,
                            "endedAtMs": 2,
                            "detail": {
                                "narration": "synthetic-private-draft",
                                "thinking": "synthetic-private-reasoning",
                                "output": "synthetic-private-origin",
                            },
                        },
                    },
                }
            ],
        )
        await publisher.flush(journal.run_id)
        session_id = store.read_run(journal.run_id).session_id
        snapshot = MessageReader(store).messages(session_id, trusted, run_id=journal.run_id)
        projected = project_sources(store, snapshot, trusted)
        public_nodes = [
            p.event.payload
            for p in projected.packets
            if p.event.type == "view.upsert" and p.event.payload.kind == "execution.node"
        ]
        assert len(public_nodes) == 1 and public_nodes[0].value.detail is None
        assert "synthetic-private" not in projected.model_dump_json()
        db.execute("DELETE FROM web.workspace_member WHERE id=%s", (grant,))
        try:
            await asyncio.to_thread(
                require_run_sources, store, journal.run_id, trusted, epoch=journal.epoch
            )
        except ModelError as exc:
            assert exc.code == "source_access_denied"
        else:
            raise AssertionError("model-frame access granted after audience revocation")
        recovered = ExecutionJournal(store, publisher, journal.run_id, journal.epoch)
        recovered.validate_checkpoint()
        try:
            await asyncio.to_thread(
                require_run_sources, store, recovered.run_id, trusted, epoch=recovered.epoch
            )
        except ModelError as exc:
            assert exc.code == "source_access_denied"
        else:
            raise AssertionError("recovered run retained revoked source")
        db.execute(
            "INSERT INTO web.workspace_member(id,tenant_id,workspace_id,tenant_member_id,role_key) "
            "VALUES(%s,%s,%s,%s,'analyst')",
            (grant, tenant, group, member),
        )
        release(exclude=["semantic", "detail"])
        paragraphs = publish_long_collection(
            Path(os.environ["COMPASS_DOCUMENTS_ROOT"]), tenant, workspace
        )
        journal2 = await start()
        marker2 = current_execution.set(journal2)
        try:
            _, _, names, _ = await assemble(journal2)
            assert "database_query" not in names and not journal2.context_binding.get("registry")
            try:
                await RegistryTools(journal2, trusted).api("context")
            except ModelError as exc:
                assert exc.code == "source_access_denied"
            else:
                raise AssertionError("disabled semantic service fell back to a registry")
            # Sixty-four open paragraphs followed by one protected paragraph.
            # Both the complete admission and the denial must cross HTTP pages.
            refs = [
                dict(
                    kind="document",
                    runId=str(journal2.run_id),
                    tenant=tenant,
                    workspace=workspace,
                    collectionDigest=journal2.search_snapshot["collectionDigest"],
                    docId=doc["docId"],
                    version=doc["version"],
                    sectionId=para["sectionId"],
                    sectionHash=para["sectionHash"],
                    businessDate="2025-10-09",
                )
                for doc, para in paragraphs
            ]
            await asyncio.to_thread(record_sources, journal2, refs)
            await asyncio.to_thread(
                require_run_sources, store, journal2.run_id, trusted, epoch=journal2.epoch
            )
            db.execute("DELETE FROM web.workspace_member WHERE id=%s", (grant,))
            try:
                await asyncio.to_thread(
                    require_run_sources, store, journal2.run_id, trusted, epoch=journal2.epoch
                )
            except ModelError as exc:
                assert exc.code == "source_access_denied"
            else:
                raise AssertionError("withdrawal in last page retained 65-source access")
            finally:
                db.execute(
                    "INSERT INTO web.workspace_member"
                    "(id,tenant_id,workspace_id,tenant_member_id,role_key) "
                    "VALUES(%s,%s,%s,%s,'analyst')",
                    (grant, tenant, group, member),
                )
            store.finish(journal2.run_id, journal2.epoch, "canceled", reason="user_canceled")
            await publisher.flush(journal2.run_id)
        finally:
            current_execution.reset(marker2)
        return {
            "documentOutagePhysicalAttempts": 2,
            "confirmedCacheHit": True,
            "pinnedConnectionOnly": True,
            "pinnedSqlExecuted": True,
            "allRowWitnessesRequired": True,
            "parallelDeduplication": True,
            "modelFrameRevoked": True,
            "recoveryRevoked": True,
            "deletedDataKindsUnavailable": True,
            "todoRecoveryIdempotent": True,
            "priorEpochFenced": True,
            "progressMetadataOnly": True,
            "retainedParagraphs": 65,
            "lastPageRevocation": True,
        }
    finally:
        release()
        current_execution.reset(marker)
        store.finish(journal.run_id, journal.epoch, "canceled", reason="user_canceled")
        await publisher.flush(journal.run_id)
        await streams.close()
        engine.dispose()
