"""Lesson 19 acceptance against isolated PostgreSQL databases and real Redis Streams."""

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from compass_agent.models.errors import ModelError
from compass_agent.runtime.commands import context_reset, control
from compass_agent.runtime.control import RunRegistry
from compass_agent.runtime.durable_service import DurableRuntime
from compass_agent.runtime.entities import AgentCommand, AgentRun, AgentStep, AgentWait
from compass_agent.runtime.journal import RecoveryStop
from compass_agent.runtime.resources import RuntimeResources
from compass_agent.schemas import (
    ControlCommand,
    DecisionCommand,
    SessionSubmit,
    TenantScope,
    WorkflowAssetPatch,
)
from compass_agent.settings import Settings
from compass_agent.workflows.assets import WorkflowAssets
from compass_agent.workflows.catalog import tool_name
from compass_agent.workflows.drafts import WorkflowDrafts
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from .test_agent_run import CHAT, model_result
from .test_database_tools_integration import db
from .test_database_tools_integration import targets as targets
from .test_dynamic_workflow import ParentAndChildModels, create_call, response, service_for
from .workflow_fixture import make_executor

QUESTION = (
    "请按固定的预算提案流程，查询 Retail 在 2025-11-17 至 2025-11-23 和 "
    "2025-11-24 至 2025-11-30 的渠道实付，生成待人工审批的预算调整提案。"
)
DYNAMIC = (
    "对比 2025-11-17 至 2025-11-23 和 2025-11-24 至 2025-11-30，"
    "找出实付额变化最大的渠道，再并行拆解前三项商品贡献并独立复核。"
)


def command(**fields):
    return SessionSubmit.model_validate(
        {
            "requestId": str(uuid4()),
            "question": QUESTION,
            "selection": {"mode": "fixed", "provider_id": "40000000-0000-0000-0000-000000000004"},
            **fields,
        }
    )


def prepare(targets):
    url, _ = targets("durable")
    db.migrate()
    return create_engine(url)


@asynccontextmanager
async def service(engine):
    value = DurableRuntime(engine, Settings(), RunRegistry())

    async def allowed(context, **kwargs):
        return None

    value.access.check = allowed
    await value.start()
    assert value.store.ready
    try:
        yield value
    finally:
        ids = list(value.tasks)
        await value.close()
        # Keep per-Run streams for restart scenarios; TTL owns terminal cleanup.
        assert all(task.done() for identifier, task in value.tasks.items() if identifier in ids)


def executor(service):
    assert isinstance(CHAT.scope, TenantScope)
    return make_executor(
        WorkflowAssets(service.store.engine),
        os.environ.get("COMPASS_WORKFLOW_E2E_RETAIL_DSN", ""),
        CHAT.scope,
        workflow_drafts=WorkflowDrafts(RuntimeResources(service.store)),
        legacy_approval=True,
    )


async def execute(service, value, worker):
    receipt = await asyncio.to_thread(service.store.submit, value, CHAT)
    identifier = UUID(receipt["runId"])
    service.schedule(identifier, worker, CHAT)
    await asyncio.wait_for(service.tasks[identifier], 35)
    return service.store.read_run(identifier)


def decision(service, run, choice="approve"):
    snapshot = service.reader.messages(run.session_id, CHAT)
    wait = next(
        p.event.payload.value
        for p in snapshot.packets
        if p.event.type == "view.upsert" and p.event.payload.kind == "approval"
    )
    return DecisionCommand.model_validate(
        {
            "requestId": str(uuid4()),
            "expectedVersion": run.version,
            "waitId": wait.wait_id,
            "proposalId": wait.proposal_id,
            "proposalRevision": wait.proposal_revision,
            "proposalDigest": wait.proposal_digest,
            "decisionId": str(uuid4()),
            "decision": choice,
        }
    )


def test_atomic_first_submit_idempotency_scope_and_busy(targets):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            value = command()
            accepted = await asyncio.gather(
                *(asyncio.to_thread(runtime.store.submit, value, CHAT) for _ in range(8))
            )
            assert all(item == accepted[0] for item in accepted)
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentRun)) == 1
                assert tx.scalar(select(func.count()).select_from(AgentCommand)) == 1
            with pytest.raises(ModelError, match="request_conflict"):
                runtime.store.submit(value.model_copy(update={"question": "different"}), CHAT)
            run = runtime.store.read_run(UUID(accepted[0]["runId"]))
            with pytest.raises(ModelError, match="session_busy"):
                runtime.store.submit(command(sessionId=str(run.session_id)), CHAT)
            with pytest.raises(ModelError, match="not_found"):
                runtime.store.read_run(run.id, CHAT.model_copy(update={"actor_user_id": uuid4()}))

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure", ["forbidden", "stream_unavailable"])
def test_admitted_command_failure_exposes_resumable_state_without_dispatch(targets, failure):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            accepted = runtime.store.submit(command(), CHAT)
            worker, models = executor(runtime)

            async def unavailable(*args, **kwargs):
                raise ModelError(failure, 403 if failure == "forbidden" else 503)

            original_flush = runtime.publisher.flush
            if failure == "forbidden":
                runtime.access.check = unavailable
            else:
                runtime.publisher.flush = unavailable
            identifier = UUID(accepted["runId"])
            runtime.schedule(identifier, worker, CHAT)
            await runtime.tasks[identifier]
            run = runtime.store.read_run(identifier)
            assert run.status == "paused" and run.reason_code == failure
            assert run.execution_epoch is None and run.recovery_status == "required"
            assert not models.child_requests and not models.parent_requests
            runtime.publisher.flush = original_flush
            async with asyncio.timeout(10):
                while not runtime.owner_healthy:
                    await asyncio.sleep(0.05)
            await runtime.publisher.flush(identifier)
            assert runtime.store.read_run(identifier).public_view["run"]["status"] == "paused"

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_history_and_context_reset_use_server_messages(targets):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            session_id = None
            for index in range(13):
                receipt = runtime.store.submit(
                    command(question=f"question {index}", sessionId=session_id), CHAT
                )
                session_id = receipt["sessionId"]
                identifier = UUID(receipt["runId"])
                await runtime.publisher.flush(identifier)
                epoch = runtime.store.claim(identifier, runtime.boot_id)
                await runtime.publisher.flush(identifier)
                runtime.store.finish(identifier, epoch, "completed", answer=f"answer {index}")
                await runtime.publisher.flush(identifier)
            page = runtime.reader.messages(UUID(session_id), CHAT)
            assert page.page is not None
            assert len(page.cursors) == 10 and page.page.next_cursor
            older = runtime.reader.messages(UUID(session_id), CHAT, cursor=page.page.next_cursor)
            assert len(older.cursors) == 3
            assert not {c.run_id for c in page.cursors} & {c.run_id for c in older.cursors}
            latest = runtime.reader.sessions(CHAT).items[0]
            context_reset(
                runtime.store,
                UUID(session_id),
                ControlCommand(requestId=uuid4(), expectedVersion=latest.version),
                CHAT,
            )
            receipt = runtime.store.submit(command(sessionId=session_id), CHAT)
            assert runtime.store.history_command(UUID(receipt["runId"])).history == []
            assert len(runtime.reader.messages(UUID(session_id), CHAT).cursors) == 10

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_outbox_lost_ack_partial_batch_projection_and_stream_loss(targets):
    from compass_agent.runtime.entities import AgentMessage
    from compass_agent.runtime.journal import ExecutionJournal
    from compass_agent.runtime.redis_stream import complete_prefix
    from redis.exceptions import ConnectionError as RedisConnectionError

    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            receipt = runtime.store.submit(command(), CHAT)
            identifier = UUID(receipt["runId"])
            run = runtime.store.read_run(identifier)
            box = runtime.store.pending(identifier)[0]
            original = runtime.streams.writer.xadd

            async def lost_ack(*args, **kwargs):
                await original(*args, **kwargs)
                raise RedisConnectionError("injected lost reply")

            runtime.streams.writer.xadd = lost_ack
            await runtime.streams.append(
                run,
                box.payload["packets"][0],
                outbox=box.id,
                index=0,
                count=len(box.payload["packets"]),
            )
            runtime.streams.writer.xadd = original
            assert complete_prefix(await runtime.streams.rows(identifier)) == []
            assert runtime.store.read_run(identifier).projected_seq == "0-0"
            await runtime.publisher.flush(identifier)
            rows = await runtime.streams.rows(identifier)
            assert len(rows) == len(box.payload["packets"])
            assert len({row[1]["packet"]["packetId"] for row in rows}) == len(rows)
            assert runtime.store.pending(identifier) == []
            epoch = runtime.store.claim(identifier, runtime.boot_id)
            await runtime.publisher.flush(identifier)
            assert await runtime.streams.reader.ttl(runtime.streams.key(identifier)) == -1
            journal = ExecutionJournal(runtime.store, runtime.publisher, identifier, epoch)
            await journal.text_delta("已接收的增量")
            # Observe the asynchronous publisher rather than assuming one timer tick
            # finishes all database work within 100 ms under the full acceptance load.
            async with asyncio.timeout(5):
                while True:
                    with Session(engine) as tx:
                        message = tx.scalars(
                            select(AgentMessage).where(
                                AgentMessage.run_id == identifier,
                                AgentMessage.role == "assistant",
                                AgentMessage.channel == "conversation",
                            )
                        ).one()
                        if (
                            message.public_content
                            and message.public_content["text"] == "已接收的增量"
                        ):
                            break
                    await asyncio.sleep(0.05)
            await runtime.streams.writer.delete(runtime.streams.key(identifier))
            with pytest.raises(ModelError, match="stream_data_lost"):
                await runtime.streams.validate(runtime.store.read_run(identifier))
            runtime.store.blocked(identifier, "stream_data_lost")
            snapshot = runtime.reader.messages(run.session_id, CHAT)
            state = next(p.event.payload for p in snapshot.packets if p.event.type == "run.upsert")
            assert state.recovery_status == "blocked" and state.status == "paused"
            assert not await runtime.streams.reader.exists(runtime.streams.key(identifier))
            with pytest.raises(ModelError, match="resume_blocked"):
                control(
                    runtime.store,
                    identifier,
                    "resume",
                    ControlCommand(
                        requestId=uuid4(),
                        expectedVersion=runtime.store.read_run(identifier).version,
                    ),
                    CHAT,
                )

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_terminal_ttl_and_checkpoint_unknown_or_corrupt_never_dispatch(targets):
    from compass_agent.runtime.entities import AgentCheckpoint
    from compass_agent.runtime.journal import ExecutionJournal

    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            accepted = runtime.store.submit(command(), CHAT)
            identifier = UUID(accepted["runId"])
            await runtime.publisher.flush(identifier)
            epoch = runtime.store.claim(identifier, runtime.boot_id)
            await runtime.publisher.flush(identifier)
            journal = ExecutionJournal(runtime.store, runtime.publisher, identifier, epoch)
            step, attempt, _ = journal.prepare("database", "read:1", {"query": "fixture"}, None)
            journal.dispatched(attempt)
            with pytest.raises(ModelError, match="execution_unknown"):
                journal.validate_checkpoint()
            journal.accept(
                step, attempt, {"result": {"value": 5}, "budget": {}, "continuation": None}
            )
            journal.validate_checkpoint()
            with Session(engine) as tx, tx.begin():
                checkpoint = tx.get(AgentCheckpoint, (identifier, 1))
                assert checkpoint is not None
                checkpoint.budget_state = {"schemaVersion": 1, "activeMs": 0, "receipts": []}
            with pytest.raises(ModelError, match="checkpoint_invalid"):
                journal.validate_checkpoint()
            runtime.store.finish(identifier, epoch, "completed", answer="committed")
            await runtime.publisher.flush(identifier)
            rows = await runtime.streams.rows(identifier)
            assert rows[-1][1]["packet"]["event"]["type"] == "run.upsert"
            assert 3500 <= await runtime.streams.reader.ttl(runtime.streams.key(identifier)) <= 3600
            await runtime.streams.writer.delete(runtime.streams.key(identifier))
            with pytest.raises(ModelError, match="history_reload_required"):
                await runtime.streams.validate(runtime.store.read_run(identifier))
            snapshot = runtime.reader.messages(UUID(accepted["sessionId"]), CHAT)
            terminal_packet = next(p for p in snapshot.packets if p.event.type == "run.upsert")
            assert terminal_packet.occurred_at == rows[-1][1]["packet"]["occurredAt"]
            assert terminal_packet.occurred_at > next(
                p.occurred_at
                for p in snapshot.packets
                if p.event.type == "message.upsert" and p.event.payload.role == "user"
            )
            assert any(
                p.event.type == "message.upsert" and p.event.payload.content.text == "committed"
                for p in snapshot.packets
            )

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_artifact_atomic_idempotency_scope_and_corruption_blocks_recovery(targets):
    from dataclasses import replace
    from hashlib import sha256

    from compass_agent.runtime.entities import AgentArtifact, AgentOutbox
    from compass_agent.runtime.journal import ExecutionJournal
    from compass_agent.tools.contracts import ArtifactRef

    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            accepted = runtime.store.submit(command(), CHAT)
            identifier = UUID(accepted["runId"])
            await runtime.publisher.flush(identifier)
            epoch = runtime.store.claim(identifier, runtime.boot_id)
            await runtime.publisher.flush(identifier)
            journal = ExecutionJournal(runtime.store, runtime.publisher, identifier, epoch)
            step, attempt, _ = journal.prepare("tool", "artifact:1", {}, None)
            journal.dispatched(attempt)
            journal.last_attempt = attempt
            content = b"channel,revenue\nfixture,123\n"
            ref = ArtifactRef(
                str(uuid4()),
                "diagnosis.csv",
                "text/csv",
                identifier,
                step,
                "artifact:1",
                len(content),
                sha256(content).hexdigest(),
            )
            resources = RuntimeResources(runtime.store)
            with Session(engine) as tx:
                before = tx.scalar(select(func.count()).select_from(AgentOutbox))
                assert before is not None
            with pytest.raises(ModelError, match="artifact_invalid"):
                resources.artifact(journal, replace(ref, sha256="0" * 64), content)
            resources.artifact(journal, ref, content)
            resources.artifact(journal, ref, content)
            changed = content + b"other,10\n"
            with pytest.raises(ModelError, match="artifact_conflict"):
                resources.artifact(
                    journal,
                    replace(ref, byte_size=len(changed), sha256=sha256(changed).hexdigest()),
                    changed,
                )
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentArtifact)) == 1
                assert tx.scalar(select(func.count()).select_from(AgentOutbox)) == before + 1
            stored = runtime.store.artifact(identifier, UUID(ref.id), CHAT)
            assert stored.content == content and stored.name == ref.name
            with pytest.raises(ModelError, match="not_found"):
                runtime.store.artifact(
                    identifier, UUID(ref.id), CHAT.model_copy(update={"actor_user_id": uuid4()})
                )
            journal.accept(
                step,
                attempt,
                {"result": {"artifactId": ref.id}, "budget": {}, "continuation": None},
            )
            journal.validate_checkpoint()
            with Session(engine) as tx, tx.begin():
                tx.execute(
                    update(AgentArtifact)
                    .where(AgentArtifact.id == UUID(ref.id))
                    .values(content=b"x" * len(content))
                )
            with pytest.raises(ModelError, match="artifact_corrupt"):
                runtime.store.artifact(identifier, UUID(ref.id), CHAT)
            with pytest.raises(ModelError, match="checkpoint_invalid"):
                journal.validate_checkpoint()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_sql_artifact_projection_failures_are_atomic_and_text_is_batched(targets):
    from contextlib import suppress
    from hashlib import sha256

    import psycopg
    from compass_agent.runtime.entities import (
        AgentArtifact,
        AgentCheckpoint,
        AgentMessage,
        AgentOutbox,
    )
    from compass_agent.runtime.journal import ExecutionJournal
    from compass_agent.tools.contracts import ArtifactRef
    from psycopg import sql
    from sqlalchemy import event
    from sqlalchemy.exc import IntegrityError, ProgrammingError

    engine = prepare(targets)

    def fault_sql(statement):
        with psycopg.connect(db.dsn(os.environ["AGENT_MIGRATION_DATABASE_URL"])) as connection:
            connection.execute("SET ROLE compass_agent_owner")
            connection.execute(statement)

    async def scenario():
        async with service(engine) as runtime:
            assert runtime.ticker is not None
            runtime.ticker.cancel()
            with suppress(asyncio.CancelledError):
                await runtime.ticker
            accepted = runtime.store.submit(command(), CHAT)
            identifier = UUID(accepted["runId"])
            await runtime.publisher.flush(identifier)
            epoch = runtime.store.claim(identifier, runtime.boot_id)
            await runtime.publisher.flush(identifier)
            journal = ExecutionJournal(runtime.store, runtime.publisher, identifier, epoch)
            step, attempt, _ = journal.prepare("tool", "file:1", {}, None)
            journal.dispatched(attempt)
            journal.last_attempt = attempt
            content = b"original csv bytes"
            ref = ArtifactRef(
                str(uuid4()),
                "analysis.csv",
                "text/csv",
                identifier,
                step,
                "file:1",
                len(content),
                sha256(content).hexdigest(),
            )
            with Session(engine) as tx:
                before = tx.scalar(select(func.count()).select_from(AgentOutbox))
            fault_sql(
                sql.SQL(
                    "CREATE FUNCTION reject_artifact() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN RAISE EXCEPTION 'injected artifact storage failure'; END $$"
                )
            )
            fault_sql(
                sql.SQL(
                    "CREATE TRIGGER artifact_failure BEFORE INSERT ON agent_artifact "
                    "FOR EACH ROW EXECUTE FUNCTION reject_artifact()"
                )
            )
            with pytest.raises(ProgrammingError):
                RuntimeResources(runtime.store).artifact(journal, ref, content)
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentArtifact)) == 0
                assert tx.scalar(select(func.count()).select_from(AgentOutbox)) == before
            fault_sql(sql.SQL("DROP TRIGGER artifact_failure ON agent_artifact"))
            fault_sql(sql.SQL("DROP FUNCTION reject_artifact()"))
            RuntimeResources(runtime.store).artifact(journal, ref, content)
            journal.accept(
                step,
                attempt,
                {"result": {"artifactId": ref.id}, "budget": {}, "continuation": None},
            )
            cursor = runtime.store.read_run(identifier).projected_seq
            fault_sql(
                sql.SQL(
                    "ALTER TABLE agent_run ADD CONSTRAINT projection_failure "
                    "CHECK (projected_seq = {}) NOT VALID"
                ).format(sql.Literal(cursor))
            )
            with pytest.raises(IntegrityError):
                await runtime.publisher.flush(identifier)
            with Session(engine) as tx:
                run = tx.get(AgentRun, identifier)
                assert run is not None and run.projected_seq == cursor
                assert tx.get(AgentCheckpoint, (identifier, 1)) is not None
            before_stream = await runtime.streams.rows(identifier)
            fault_sql(sql.SQL("ALTER TABLE agent_run DROP CONSTRAINT projection_failure"))
            await runtime.publisher.flush(identifier)
            after_stream = await runtime.streams.rows(identifier)
            assert after_stream[: len(before_stream)] == before_stream
            assert len({row[1]["packet"]["packetId"] for row in after_stream}) == len(after_stream)
            assert runtime.store.pending(identifier) == []
            assert journal.prepare("tool", "file:1", {}, None)[1] == attempt
            journal.validate_checkpoint()
            writes = []

            def count_writes(connection, cursor, statement, parameters, context, executemany):
                if statement.startswith("UPDATE agent_message "):
                    writes.append(statement)

            with Session(engine) as tx:
                boxes = tx.scalar(select(func.count()).select_from(AgentOutbox))
            event.listen(engine, "before_cursor_execute", count_writes)
            try:
                for _ in range(120):
                    await journal.text_delta("增量")
                await runtime.publisher.flush(identifier)
            finally:
                event.remove(engine, "before_cursor_execute", count_writes)
            assert 0 < len(writes) < 120
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentOutbox)) == boxes
                message = tx.scalars(
                    select(AgentMessage).where(
                        AgentMessage.run_id == identifier,
                        AgentMessage.role == "assistant",
                        AgentMessage.channel == "conversation",
                    )
                ).one()
                assert message.public_content is not None
                assert message.public_content["text"] == "增量" * 120

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_prepared_boundary_external_event_validation_dedupe_and_replay(targets):
    from compass_agent.runtime.entities import AgentAttempt, AgentStep
    from compass_agent.runtime.journal import ExecutionJournal
    from compass_agent.schemas import ExternalRunEvent

    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            accepted = runtime.store.submit(command(), CHAT)
            identifier = UUID(accepted["runId"])
            await runtime.publisher.flush(identifier)
            epoch = runtime.store.claim(identifier, runtime.boot_id)
            journal = ExecutionJournal(runtime.store, runtime.publisher, identifier, epoch)

            async def first():
                return {"readonly": 17}

            assert await journal.call("tool", "read", {}, first, dict) == {"readonly": 17}
            prepared = journal.prepare("tool", "not-sent", {"source": "frozen"}, None)
            assert (
                journal.prepare("tool", "not-sent", {"source": "frozen"}, None)[:2] == prepared[:2]
            )
            payload_schema = {
                "type": "object",
                "properties": {"confirmed": {"const": True}},
                "required": ["confirmed"],
                "additionalProperties": False,
            }
            with pytest.raises(RecoveryStop):
                await journal.external_event(
                    "payment", "payment.accepted", "order-17", payload_schema
                )
            run = runtime.store.read_run(identifier)
            assert run.status == "waiting" and run.execution_epoch is None
            with Session(engine) as tx:
                wait = tx.scalars(select(AgentWait).where(AgentWait.run_id == identifier)).one()
                step_id = wait.step_id
                wait_id = wait.id
                step = tx.get(AgentStep, step_id)
                assert step and step.status == "waiting"
            event = ExternalRunEvent.model_validate(
                {
                    "eventId": str(uuid4()),
                    "waitId": str(wait_id),
                    "eventType": "payment.accepted",
                    "correlationKey": "order-17",
                    "payload": {"confirmed": True},
                }
            )
            for changes, code in [
                ({"correlation_key": "wrong"}, "event_mismatch"),
                ({"payload": {"confirmed": False}}, "validation_failed"),
            ]:
                with pytest.raises(ModelError, match=code):
                    runtime.hitl.external_event(
                        identifier, event.model_copy(update=changes), boot_id=runtime.boot_id
                    )
            receipt = runtime.hitl.external_event(identifier, event, boot_id=runtime.boot_id)
            assert (
                runtime.hitl.external_event(identifier, event, boot_id=runtime.boot_id) == receipt
            )
            restarted = ExecutionJournal(
                runtime.store,
                runtime.publisher,
                identifier,
                runtime.store.read_run(identifier).execution_epoch,
            )
            restarted.validate_checkpoint()
            assert await restarted.external_event(
                "payment", "payment.accepted", "order-17", payload_schema
            ) == {"confirmed": True}
            assert await restarted.external_event(
                "payment", "payment.accepted", "order-17", payload_schema
            ) == {"confirmed": True}
            with Session(engine) as tx:
                attempts = tx.scalars(
                    select(AgentAttempt).where(AgentAttempt.step_id == step_id)
                ).all()
                assert len(attempts) == 1 and attempts[0].status == "completed"
            with pytest.raises(ModelError, match="request_conflict"):
                runtime.hitl.external_event(
                    identifier,
                    event.model_copy(update={"payload": {"confirmed": False}}),
                    boot_id=runtime.boot_id,
                )
            other = runtime.store.submit(command(), CHAT)
            other_id = UUID(other["runId"])
            await runtime.publisher.flush(other_id)
            other_epoch = runtime.store.claim(other_id, runtime.boot_id)
            another = ExecutionJournal(runtime.store, runtime.publisher, other_id, other_epoch)
            await another.call("tool", "read", {}, first, dict)
            with pytest.raises(RecoveryStop):
                await another.external_event(
                    "payment", "payment.accepted", "other-order", payload_schema
                )
            current = runtime.store.read_run(other_id)
            with Session(engine) as tx:
                other_wait = (
                    tx.scalars(select(AgentWait).where(AgentWait.run_id == other_id)).one().id
                )
            control(
                runtime.store,
                other_id,
                "cancel",
                ControlCommand(requestId=uuid4(), expectedVersion=current.version),
                CHAT,
            )
            with pytest.raises(ModelError, match="event_conflict"):
                runtime.hitl.external_event(
                    other_id,
                    event.model_copy(
                        update={
                            "event_id": uuid4(),
                            "wait_id": other_wait,
                            "correlation_key": "other-order",
                        }
                    ),
                    boot_id=runtime.boot_id,
                )

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_cancel_during_admission_confirms_no_physical_dispatch(targets):
    from compass_agent.runtime.entities import AgentAttempt, ModelAttempt

    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            reached = asyncio.Event()
            checks = 0

            async def access(context, **kwargs):
                nonlocal checks
                checks += 1
                if checks == 2:
                    reached.set()
                    await asyncio.Event().wait()

            runtime.access.check = access
            worker, models = executor(runtime)
            receipt = runtime.store.submit(command(), CHAT)
            identifier = UUID(receipt["runId"])
            runtime.schedule(identifier, worker, CHAT)
            await asyncio.wait_for(reached.wait(), 10)
            current = runtime.store.read_run(identifier)
            control(
                runtime.store,
                identifier,
                "cancel",
                ControlCommand(requestId=uuid4(), expectedVersion=current.version),
                CHAT,
            )
            runtime.controls[identifier].request_stop("user")
            await asyncio.wait_for(runtime.tasks[identifier], 10)
            final = runtime.store.read_run(identifier)
            assert final.status == "canceled" and final.cleanup_status == "confirmed"
            assert not models.parent_requests and not models.child_requests
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(ModelAttempt)) == 0
                attempts = tx.scalars(
                    select(AgentAttempt).where(AgentAttempt.run_id == identifier)
                ).all()
                assert attempts and all(item.status == "prepared" for item in attempts)

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_dynamic_workflow_confirm_and_reuse_across_sessions(targets):
    engine = prepare(targets)
    assets = WorkflowAssets(engine)

    async def scenario():
        async with service(engine) as runtime:
            drafts = WorkflowDrafts(RuntimeResources(runtime.store))
            first = ParentAndChildModels([create_call(), model_result(text="试跑已完成。")])
            created = await execute(
                runtime, command(question="分析后创建工作流"), service_for(first, assets, drafts)
            )
            assert created.status == "completed", created.reason_code
            assert not assets.list(CHAT).items
            snapshot = runtime.reader.messages(created.session_id, CHAT)
            draft_id = next(
                UUID(packet.event.payload.value.draft_id)
                for packet in snapshot.packets
                if packet.event.type == "view.upsert" and packet.event.payload.kind == "workflow"
            )
            asset = drafts.confirm(CHAT, draft_id, "跨期复盘", assets)
            assert drafts.confirm(CHAT, draft_id, "跨期复盘", assets).asset_id == asset.asset_id
            name = tool_name(asset.asset_id)
            with Session(engine) as tx:
                children = tx.scalars(
                    select(AgentStep).where(
                        AgentStep.run_id == created.id, AgentStep.kind == "subagent"
                    )
                ).all()
                assert len(children) == 3
                assert all(
                    child.parent_step_id
                    and child.accepted_attempt_id
                    and child.status == "completed"
                    for child in children
                )

        async with service(engine) as runtime:
            fresh_assets = WorkflowAssets(engine)
            second = ParentAndChildModels(
                [response(name, {"window": "December"}), model_result(text="新时段复盘完成。")]
            )
            reused = await execute(
                runtime,
                command(question="查询另一个月的同类数据"),
                service_for(second, fresh_assets, WorkflowDrafts(RuntimeResources(runtime.store))),
            )
            assert reused.status == "completed", reused.reason_code
            assert reused.session_id != created.session_id
            assert any(
                request.messages[1].content == "Read December" for request in second.children
            )
            assert len(fresh_assets.list(CHAT).items) == 1
            foreign = CHAT.model_copy(update={"actor_user_id": uuid4()})
            assert not fresh_assets.active_tools(foreign, limit=3)
            disabled = fresh_assets.patch(
                CHAT,
                asset.asset_id,
                WorkflowAssetPatch(
                    metadataRevision=asset.metadata_revision, name=asset.name, status="disabled"
                ),
            )
            assert not fresh_assets.active_tools(CHAT, limit=3)
            with pytest.raises(ModelError, match="not_found"):
                fresh_assets.get(CHAT, asset.asset_id, active=True)
            enabled = fresh_assets.patch(
                CHAT,
                asset.asset_id,
                WorkflowAssetPatch(
                    metadataRevision=disabled.metadata_revision, name=asset.name, status="active"
                ),
            )
            assert tool_name(enabled.asset_id) == name

    asyncio.run(scenario())
