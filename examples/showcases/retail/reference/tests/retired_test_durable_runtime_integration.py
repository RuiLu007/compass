"""Historical fixed-case tests; execution contracts retired in Feature 978."""

def test_budget_wait_survives_restart_and_effect_commit_crash_is_at_most_once(targets):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            worker, models = executor(runtime)
            run = await execute(runtime, command(showcase="budget_proposal"), worker)
            assert run.status == "waiting"
            original_calls = len(models.child_requests)
            approval = decision(runtime, run)
            receipt = runtime.hitl.decide(
                run.id, approval, CHAT, may_approve=True, boot_id=runtime.boot_id
            )
            assert runtime.hitl.decide(run.id, approval, CHAT, may_approve=True) == receipt

            async def fault(name, identifier):
                if name == "effect_committed":
                    raise RecoveryStop("injected_process_exit")

            runtime.fault = fault
            runtime.schedule(run.id, worker, CHAT)
            await runtime.tasks[run.id]
            assert runtime.store.read_run(run.id).status == "paused"
            assert len(models.child_requests) == original_calls
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 1
        async with service(engine) as restarted:
            worker, models = executor(restarted)
            restarted.schedule(run.id, worker, CHAT)
            await restarted.tasks[run.id]
            result = restarted.store.read_run(run.id)
            assert result.status == "completed"
            assert not models.child_requests
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 1
            snapshot = restarted.reader.messages(result.session_id, CHAT)
            assert any(
                p.event.type == "message.upsert" and "已批准" in p.event.payload.content.text
                for p in snapshot.packets
            )

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


@pytest.mark.parametrize("choice", ["reject", "expire"])
def test_reject_or_expire_preserves_analysis_without_new_model_calls(targets, choice):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            worker, models = executor(runtime)
            run = await execute(runtime, command(showcase="budget_proposal"), worker)
            assert run.status == "waiting"
            calls = len(models.child_requests)
            value = decision(runtime, run, "reject" if choice == "reject" else "approve")
            if choice == "expire":
                with engine.begin() as conn:
                    conn.execute(
                        update(AgentWait)
                        .where(AgentWait.id == value.wait_id)
                        .values(
                            created_at=now() - timedelta(hours=1),
                            expires_at=now() - timedelta(seconds=1),
                        )
                    )
            receipt = runtime.hitl.decide(run.id, value, CHAT, may_approve=choice == "expire")
            assert receipt["status"] == "completed"
            await runtime.publisher.flush(run.id)
            assert runtime.store.read_run(run.id).public_view["run"]["status"] == "completed"
            assert len(models.child_requests) == calls
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 0

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_quickjs_host_receipt_replay_and_draft_survive_restart(targets):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            worker, models = executor(runtime)
            seen = False

            async def fault(name, identifier):
                nonlocal seen
                if name == "host_result_committed" and not seen:
                    seen = True
                    raise RecoveryStop("injected_host_delivery_exit")

            runtime.fault = fault
            run = await execute(
                runtime, command(question=DYNAMIC, showcase="dynamic_workflow"), worker
            )
            assert seen and run.status == "paused"
            before = len(models.child_requests) + len(models.parent_requests)
        async with service(engine) as restarted:
            worker, models = executor(restarted)
            run = restarted.store.read_run(run.id)
            receipt = control(
                restarted.store,
                run.id,
                "resume",
                ControlCommand(requestId=uuid4(), expectedVersion=run.version),
                CHAT,
            )
            assert receipt["status"] == "paused"
            restarted.schedule(run.id, worker, CHAT)
            await restarted.tasks[run.id]
            run = restarted.store.read_run(run.id)
            assert run.status == "completed", run.reason_code
            assert before + len(models.child_requests) + len(models.parent_requests) <= 10
            snapshot = restarted.reader.messages(run.session_id, CHAT)
            draft = next(
                p.event.payload.value
                for p in snapshot.packets
                if p.event.type == "view.upsert" and p.event.payload.kind == "workflow"
            )
            resources = RuntimeResources(restarted.store)
            saved = resources.confirm(CHAT, UUID(draft.draft_id), "恢复后的工作流")
            repeated = resources.confirm(CHAT, UUID(draft.draft_id), "重复确认")
            assert saved is not None and repeated is not None
            assert repeated.asset_id == saved.asset_id

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_control_versions_pause_resume_and_wait_cancel_preserve_terminal_state(targets):
    from compass_agent.runtime.entities import AgentStep
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
            step, attempt, _ = journal.prepare("database", "query:1", {"query": "fixture"}, None)
            journal.dispatched(attempt)
            journal.accept(step, attempt, {"result": 7, "budget": {}, "continuation": None})
            run = runtime.store.read_run(identifier)
            commands = [
                ControlCommand(requestId=uuid4(), expectedVersion=run.version) for _ in range(2)
            ]
            outcomes = await asyncio.gather(
                *(
                    asyncio.to_thread(control, runtime.store, identifier, "pause", value, CHAT)
                    for value in commands
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(value, dict) for value in outcomes) == 1
            assert [value.code for value in outcomes if isinstance(value, ModelError)] == [
                "version_conflict"
            ]
            with pytest.raises(RecoveryStop) as stopped:
                await journal.gate()
            assert stopped.value.reason == "user_paused"
            await runtime.pause_interrupted(identifier, epoch, stopped.value.reason)
            paused = runtime.store.read_run(identifier)
            assert paused.status == "paused" and paused.checkpoint_no == 1
            control(
                runtime.store,
                identifier,
                "resume",
                ControlCommand(requestId=uuid4(), expectedVersion=paused.version),
                CHAT,
            )
            epoch = runtime.store.claim(identifier, runtime.boot_id)
            recovered = ExecutionJournal(runtime.store, runtime.publisher, identifier, epoch)
            recovered.validate_checkpoint()
            assert recovered.prepare("database", "query:1", {"query": "fixture"}, None) == (
                step,
                attempt,
                {"result": 7, "budget": {}, "continuation": None},
            )
            runtime.store.finish(identifier, epoch, "completed", answer="7")
            with pytest.raises(ModelError, match="run_terminal"):
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
            worker, _ = executor(runtime)
            waiting = await execute(runtime, command(showcase="budget_proposal"), worker)
            cancel = ControlCommand(requestId=uuid4(), expectedVersion=waiting.version)
            result = control(runtime.store, waiting.id, "cancel", cancel, CHAT)
            assert result["status"] == "canceled"
            assert control(runtime.store, waiting.id, "cancel", cancel, CHAT) == result
            await runtime.publisher.flush(waiting.id)
            with Session(engine) as tx:
                assert (
                    tx.scalar(
                        select(func.count())
                        .select_from(AgentStep)
                        .where(AgentStep.run_id == waiting.id, AgentStep.status == "waiting")
                    )
                    == 0
                )
                assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 0
            assert not any(
                value["value"].get("status") in {"waiting", "running"}
                for value in runtime.store.read_run(waiting.id).public_view["objects"].values()
                if value["kind"] == "execution.node"
            )

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_approval_rejects_wrong_proposal_scope_and_revoked_approver_before_effect(targets):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            worker, _ = executor(runtime)
            run = await execute(runtime, command(showcase="budget_proposal"), worker)
            approval = decision(runtime, run)
            for update_fields in ({"proposal_revision": 2}, {"proposal_digest": "0" * 64}):
                with pytest.raises(ModelError, match="proposal_conflict"):
                    runtime.hitl.decide(
                        run.id, approval.model_copy(update=update_fields), CHAT, may_approve=True
                    )
            with pytest.raises(ModelError, match="forbidden"):
                runtime.hitl.decide(run.id, approval, CHAT, may_approve=False)
            assert isinstance(CHAT.scope, TenantScope)
            stranger = CHAT.model_copy(
                update={"scope": CHAT.scope.model_copy(update={"workspace_id": uuid4()})}
            )
            with pytest.raises(ModelError, match="not_found"):
                runtime.hitl.decide(run.id, approval, stranger, may_approve=True)
            runtime.hitl.decide(run.id, approval, CHAT, may_approve=True, boot_id=runtime.boot_id)

            async def revoked(context, *, approval=False):
                if approval:
                    raise ModelError("forbidden", 403)

            runtime.access.check = revoked
            runtime.schedule(run.id, worker, CHAT)
            await runtime.tasks[run.id]
            paused = runtime.store.read_run(run.id)
            assert paused.status == "paused" and paused.reason_code == "forbidden"
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 0

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_legacy_http_uses_durable_storage_and_cancel_after_restart(targets, monkeypatch, caplog):
    import logging

    from compass_agent.api.runs import get_agent_executor
    from compass_agent.main import app
    from compass_agent.runtime.durable_service import CurrentAccess
    from fastapi.testclient import TestClient

    from .test_agent_run import auth_headers, run_command

    engine = prepare(targets)
    monkeypatch.setattr("compass_agent.main.get_engine", lambda: engine)
    settings = Settings(compass_agent_service_token="run-service-token")
    monkeypatch.setattr("compass_agent.api.models.get_settings", lambda: settings)
    monkeypatch.setattr("compass_agent.main.get_settings", lambda: settings)

    async def allowed(self, context, **kwargs):
        return None

    monkeypatch.setattr(CurrentAccess, "check", allowed)
    caplog.set_level(logging.INFO, logger="compass_agent.api.runs")
    try:
        with TestClient(app) as client:
            runtime = app.state.durable_runtime

            async def http_executor():
                return executor(app.state.durable_runtime)[0]

            app.dependency_overrides[get_agent_executor] = http_executor

            # The production app now accepts into the M6 queue. Drive its real
            # scheduler while TestClient consumes the finite legacy response.
            async def consume_queued_run():
                from compass_agent.runtime.run_scheduler import WorkerIdentity
                from compass_agent.schemas import WorkerAdvanceCommand

                queue = runtime.store.scheduler
                assert queue is not None
                identity = WorkerIdentity("legacy-http-test", uuid4())
                async with asyncio.timeout(40):
                    while True:
                        grant = await asyncio.to_thread(queue.claim, identity)
                        if grant is not None:
                            break
                        await asyncio.sleep(0.05)
                    runner = await http_executor()
                    checkpoint = grant.checkpoint_no
                    while True:
                        grant = await asyncio.to_thread(queue.heartbeat, identity, grant)
                        request = WorkerAdvanceCommand(grant=grant, expectedCheckpoint=checkpoint)
                        _, start = await asyncio.to_thread(queue.prepare_advance, identity, request)
                        assert start
                        runtime.start_boundary(grant.run_id, runner, CHAT, grant.epoch)
                        await runtime.tasks[grant.run_id]
                        ack, start = await asyncio.to_thread(
                            queue.prepare_advance, identity, request
                        )
                        assert not start
                        if ack.status in {"parked", "done"}:
                            return
                        assert ack.status == "continue"
                        checkpoint = ack.checkpoint_no

            assert client.portal is not None
            consumer = client.portal.start_task_soon(consume_queued_run)
            response = client.post(
                "/v1/analysis/runs",
                headers=auth_headers(CHAT),
                json=run_command()
                .model_copy(update={"question": QUESTION, "showcase": "budget_proposal"})
                .model_dump(mode="json", by_alias=True),
            )
            consumer.result(timeout=45)
            assert response.status_code == 200, response.text
            import json

            packets = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            run_id = packets[0]["runId"]
            assert packets[-1]["event"]["payload"]["status"] == "waiting"
            run = runtime.store.read_run(UUID(run_id), CHAT)
            assert run.status == "waiting" and run.checkpoint_no
            assert "no-store" in response.headers["cache-control"]
            assert {"analysis.run.started", "analysis.run.finished", "analysis.run.routed"} <= {
                getattr(record, "event_name", "") for record in caplog.records
            }
        with TestClient(app) as client:
            response = client.post(
                f"/v1/analysis/runs/{run_id}/cancel", headers=auth_headers(CHAT), json={}
            )
            assert response.status_code == 202
            assert app.state.durable_runtime.store.read_run(UUID(run_id)).status == "canceled"
            hidden = client.post(
                f"/v1/analysis/runs/{run_id}/cancel",
                headers=auth_headers(CHAT.model_copy(update={"actor_user_id": uuid4()})),
                json={},
            )
            assert hidden.status_code == 404
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_durable_observation_retains_trace_across_restart_and_terminal_replay(targets):
    from .test_observability import observer, spans

    engine = prepare(targets)
    owner, export = observer()

    async def scenario():
        async with service(engine) as runtime:
            runtime.observer = owner
            worker, _ = executor(runtime)
            value = command(showcase="budget_proposal")
            receipt = runtime.store.submit(value, CHAT)
            identifier = UUID(receipt["runId"])
            headers = runtime.begin_observation(identifier, CHAT, worker)
            assert len(headers["X-Compass-Trace-Id"]) == 32
            assert headers["X-Compass-Trace-Sampled"] == "1"
            assert runtime.begin_observation(identifier, CHAT, worker) == headers
            runtime.schedule(identifier, worker, CHAT)
            await runtime.tasks[identifier]
            run = runtime.store.read_run(identifier)
            assert run.status == "waiting"
            assert runtime.begin_observation(identifier, CHAT, worker) == headers
            assert not runtime.observations
        async with service(engine) as restarted:
            restarted.observer = owner
            worker, _ = executor(restarted)
            run = restarted.store.read_run(identifier)
            restarted.hitl.decide(
                identifier,
                decision(restarted, run),
                CHAT,
                may_approve=True,
                boot_id=restarted.boot_id,
            )
            restarted.schedule(identifier, worker, CHAT)
            await restarted.tasks[identifier]
            assert restarted.store.read_run(identifier).status == "completed"
            assert restarted.begin_observation(identifier, CHAT, worker) == headers
            assert not restarted.observations
        roots = [span for span in spans(owner, export) if span.name == "compass.request"]
        assert len(roots) == 2
        assert {f"{span.context.trace_id:032x}" for span in roots} == {
            headers["X-Compass-Trace-Id"]
        }
        assert roots[1].parent.span_id == roots[0].context.span_id

    try:
        asyncio.run(scenario())
    finally:
        owner.shutdown()
        engine.dispose()
