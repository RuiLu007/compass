"""Historical fixed-case tests; execution contracts retired in Feature 978."""

@pytest.mark.parametrize("showcase", ["dynamic_workflow", "budget_proposal"])
def test_real_executor_workflow_advances_to_answer_without_repeating_model_or_retail_calls(
    engine, monkeypatch, showcase
):
    physical_reads = []
    actual_query = retail_handler._query_retail_live

    async def audited_query(prepared, control, **kwargs):
        physical_reads.append(prepared.tool_call_id)
        return await actual_query(prepared, control, **kwargs)

    monkeypatch.setattr(retail_handler, "_query_retail_live", audited_query)

    async def scenario():
        context = owner()
        assert isinstance(context.scope, TenantScope)
        async with runtime_service(engine) as runtime:
            resources = RuntimeResources(runtime.store)
            executor, models = make_executor(
                WorkflowAssets(engine),
                os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"],
                context.scope,
                workflow_drafts=WorkflowDrafts(resources),
                legacy_approval=showcase == "budget_proposal",
            )
            request = command(
                question=DYNAMIC if showcase == "dynamic_workflow" else QUESTION,
                showcase=showcase,
            )
            run_id = UUID(runtime.store.submit(request, context)["runId"])
            await runtime.publisher.flush(run_id)
            worker = WorkerIdentity("workflow-worker", uuid4())
            grant = await claim(runtime, worker, run_id)
            result, continuations = await advance_to_boundary_stop(
                runtime, worker, grant, executor, context
            )
            assert continuations
            run = runtime.store.read_run(run_id)
            if showcase == "budget_proposal":
                assert run.status == "waiting" and result.status == "parked", run.reason_code
                assert job(engine, run_id).status == "parked"
                model_calls = len(models.parent_requests) + len(models.child_requests)
                read_calls = len(physical_reads)
                with Session(engine) as tx:
                    wait = tx.scalars(select(AgentWait).where(AgentWait.run_id == run_id)).one()
                    approval = DecisionCommand(
                        requestId=uuid4(),
                        expectedVersion=run.version,
                        waitId=wait.id,
                        proposalId=wait.proposal_id,
                        proposalRevision=wait.proposal_revision,
                        proposalDigest=wait.proposal_sha256,
                        decisionId=uuid4(),
                        decision="approve",
                    )
                runtime.hitl.decide(
                    run_id, approval, context, may_approve=True, boot_id=runtime.boot_id
                )
                assert job(engine, run_id).status == "ready"
                assert runtime.store.read_run(run_id).execution_epoch is None
                await runtime.publisher.flush(run_id)
                successor = await claim(runtime, worker, run_id)
                assert successor.epoch != grant.epoch
                result, _ = await advance_to_boundary_stop(
                    runtime, worker, successor, executor, context
                )
                assert len(models.parent_requests) + len(models.child_requests) == model_calls
                assert len(physical_reads) == read_calls
                with Session(engine) as tx:
                    assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 1
            final = runtime.store.read_run(run_id)
            assert final.status == "completed" and result.status == "done", final.reason_code
            assert job(engine, run_id).status == "done"
            expected_reads = 3 if showcase == "dynamic_workflow" else 1
            assert len(physical_reads) == expected_reads
            assert len(models.child_requests) == expected_reads
            snapshot = runtime.reader.messages(final.session_id, context)
            answers = [
                packet.event.payload
                for packet in snapshot.packets
                if packet.event.type == "message.upsert"
                and packet.event.payload.role == "assistant"
                and packet.event.payload.status == "completed"
            ]
            assert len(answers) == 1 and answers[0].content.text.strip()
            if showcase == "dynamic_workflow":
                assert models.generated == 1
                drafts = [
                    packet.event.payload.value
                    for packet in snapshot.packets
                    if packet.event.type == "view.upsert"
                    and packet.event.payload.kind == "workflow"
                ]
                assert len(drafts) == 1
                saved = resources.confirm(context, UUID(drafts[0].draft_id), "调度边界诊断")
                assert saved is not None
                assert saved.name == "调度边界诊断"
            with Session(engine) as tx:
                steps = tx.scalars(select(AgentStep).where(AgentStep.run_id == run_id)).all()
                attempts = tx.scalars(
                    select(AgentAttempt).where(AgentAttempt.run_id == run_id)
                ).all()
                accepted_steps = [step for step in steps if step.accepted_attempt_id is not None]
                assert all(
                    attempt.number == 1 and attempt.status == "completed" for attempt in attempts
                )
                assert len(attempts) == len(accepted_steps)
                assert len(models.parent_requests) + len(models.child_requests) == sum(
                    step.kind == "model" for step in accepted_steps
                )
                assert len(physical_reads) == sum(
                    "/retail:" in step.node_key for step in accepted_steps
                )
                assert len(physical_reads) > 0

    asyncio.run(scenario())


def test_supervisor_recovers_committed_effect_with_revoked_access_but_denies_new_execution(engine):
    async def scenario():
        context = owner()
        assert isinstance(context.scope, TenantScope)
        async with runtime_service(
            engine,
            compass_worker_credentials=SecretStr(
                '{"effect-worker":"isolated-effect-worker-secret"}'
            ),
        ) as runtime:
            executor, models = make_executor(
                WorkflowAssets(engine),
                os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"],
                context.scope,
                legacy_approval=True,
            )
            run_id = UUID(
                runtime.store.submit(command(showcase="budget_proposal"), context)["runId"]
            )
            await runtime.publisher.flush(run_id)
            worker = WorkerIdentity("effect-worker", uuid4())
            grant = await claim(runtime, worker, run_id)
            waiting, _ = await advance_to_boundary_stop(runtime, worker, grant, executor, context)
            assert waiting.status == "parked" and runtime.store.read_run(run_id).status == "waiting"
            with Session(engine) as tx:
                wait = tx.scalars(select(AgentWait).where(AgentWait.run_id == run_id)).one()
                approval = DecisionCommand(
                    requestId=uuid4(),
                    expectedVersion=runtime.store.read_run(run_id).version,
                    waitId=wait.id,
                    proposalId=wait.proposal_id,
                    proposalRevision=wait.proposal_revision,
                    proposalDigest=wait.proposal_sha256,
                    decisionId=uuid4(),
                    decision="approve",
                )
            runtime.hitl.decide(
                run_id, approval, context, may_approve=True, boot_id=runtime.boot_id
            )
            await runtime.publisher.flush(run_id)
            approved_grant = await claim(runtime, worker, run_id)
            abandoned = WorkerAdvanceCommand(
                grant=approved_grant, expectedCheckpoint=approved_grant.checkpoint_no
            )
            ack, start = scheduler(runtime).prepare_advance(worker, abandoned)
            assert start
            with Session(engine) as tx:
                action = tx.scalars(select(AgentAction).where(AgentAction.run_id == run_id)).one()
                action_id = action.id
            # Commit the real independent effect transaction, then lose its Run receipt.
            committed = runtime.hitl.apply_demo(run_id, approved_grant.epoch, action_id)
            assert committed["state"] == "applied"
            with Session(engine) as tx:
                action = tx.get(AgentAction, action_id)
                effect = tx.get(AgentDemoEffect, action_id)
                assert action is not None and effect is not None
                assert action.state == "pending" and not action.receipt
                committed_at, delta = effect.committed_at, effect.delta_cent
            assert runtime.store.read_run(run_id).status == "running"
            denied_checks = []

            async def revoked(context, **kwargs):
                assert context == owner()
                denied_checks.append(context.actor_user_id)
                raise ModelError("forbidden", 403)

            runtime.access.check = revoked
            calls_before = len(models.parent_requests) + len(models.child_requests)
            expire_lease(engine, run_id)
            await runtime.recover_scheduled()
            assert denied_checks == []
            assert job(engine, run_id).status == "ready"
            with Session(engine) as tx:
                action = tx.get(AgentAction, action_id)
                assert action is not None and action.state == "applied"
                assert action.receipt == committed
                receipt = tx.scalars(
                    select(AgentCommand).where(AgentCommand.request_id == ack.command_id)
                ).one()
                assert receipt.response["state"] == "interrupted"
            await runtime.publisher.flush(run_id)
            next_worker = WorkerIdentity(worker.worker_id, uuid4())
            resumed = await claim(runtime, next_worker, run_id)
            assert resumed.epoch != approved_grant.epoch
            app = FastAPI()
            app.state.durable_runtime = runtime
            app.include_router(worker_runs.router)
            app.dependency_overrides[worker_runs.get_worker_executor] = lambda: executor

            @app.exception_handler(ModelError)
            async def errors(request: Request, error: ModelError):
                return JSONResponse({"code": error.code}, status_code=error.status)

            headers = {"Authorization": "Bearer isolated-effect-worker-secret"}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://effect-recovery.test"
            ) as client:
                request = WorkerAdvanceCommand(
                    grant=resumed, expectedCheckpoint=resumed.checkpoint_no
                )
                response = await client.post(
                    "/internal/runtime/jobs/advance",
                    json=request.model_dump(mode="json", by_alias=True),
                    headers=headers,
                )
                assert response.status_code == 200, response.text
                await asyncio.wait_for(runtime.tasks[run_id], 10)
                final = await client.post(
                    "/internal/runtime/jobs/advance",
                    json=request.model_dump(mode="json", by_alias=True),
                    headers=headers,
                )
                assert final.status_code == 200 and final.json()["status"] == "done"
                assert runtime.store.read_run(run_id).status == "completed"
                assert denied_checks == []
                assert len(models.parent_requests) + len(models.child_requests) == calls_before
                with Session(engine) as tx:
                    effect = tx.get(AgentDemoEffect, action_id)
                    assert effect is not None and (effect.committed_at, effect.delta_cent) == (
                        committed_at,
                        delta,
                    )
                    assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 1
                    action = tx.get(AgentAction, action_id)
                    assert action is not None
                    accepted = tx.scalars(
                        select(AgentAttempt).where(AgentAttempt.step_id == action.step_id)
                    ).all()
                    assert len(accepted) == 1 and accepted[0].receipt == committed
                fresh_run = await submit(runtime)
                fresh_grant = await claim(runtime, next_worker, fresh_run)
                runtime.owner_healthy = False
                release = await client.post(
                    "/internal/runtime/jobs/release",
                    json={"grant": fresh_grant.model_dump(mode="json", by_alias=True)},
                    headers=headers,
                )
                assert release.status_code == 503 and job(engine, fresh_run).status == "leased"
                runtime.owner_healthy = True
                denied = await client.post(
                    "/internal/runtime/jobs/advance",
                    json=WorkerAdvanceCommand(grant=fresh_grant, expectedCheckpoint=0).model_dump(
                        mode="json", by_alias=True
                    ),
                    headers=headers,
                )
                assert denied.status_code >= 400
                assert denied_checks == [context.actor_user_id]
                assert fresh_run not in runtime.tasks
                assert job(engine, fresh_run).status == "reclaiming"
                assert len(models.parent_requests) + len(models.child_requests) == calls_before
                with Session(engine) as tx:
                    assert (
                        tx.scalar(
                            select(func.count())
                            .select_from(AgentAttempt)
                            .where(AgentAttempt.run_id == fresh_run)
                        )
                        == 0
                    )
                    assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 1

    asyncio.run(scenario())
