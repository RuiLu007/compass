"""Historical fixed-case tests; execution contracts retired in Feature 978."""

@pytest.mark.parametrize("competitor", ["cancel", "expire"])
def test_approval_competes_atomically_with_cancel_or_expiry(targets, competitor):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            worker, models = executor(runtime)
            run = await execute(runtime, command(showcase="budget_proposal"), worker)
            assert run.status == "waiting"
            model_count = len(models.child_requests) + len(models.parent_requests)
            approve = decision(runtime, run)
            assert runtime.ticker is not None
            runtime.ticker.cancel()
            await asyncio.gather(runtime.ticker, return_exceptions=True)
            if competitor == "expire":
                with Session(engine) as tx, tx.begin():
                    wait = tx.get(AgentWait, approve.wait_id)
                    assert wait is not None
                    clock = tx.scalar(select(func.clock_timestamp()))
                    assert clock is not None
                    wait.created_at = clock - timedelta(minutes=1)
                    wait.expires_at = clock - timedelta(seconds=1)
                other = runtime.hitl.expire
            else:
                cancel = ControlCommand(requestId=uuid4(), expectedVersion=run.version)

                def other():
                    return control(runtime.store, run.id, "cancel", cancel, CHAT)

            outcomes = await asyncio.to_thread(
                concurrent,
                [
                    lambda: runtime.hitl.decide(
                        run.id, approve, CHAT, may_approve=True, boot_id=runtime.boot_id
                    ),
                    other,
                ],
            )
            settled = runtime.store.read_run(run.id)
            await runtime.publisher.flush(run.id)
            with Session(engine) as tx:
                wait = tx.get(AgentWait, approve.wait_id)
                assert wait is not None
                actions = tx.scalar(select(func.count()).select_from(AgentAction))
                assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 0
                if competitor == "cancel":
                    assert outcomes.count("version_conflict") == 1
                    assert (settled.status, wait.status, actions) in {
                        ("running", "approved", 1),
                        ("canceled", "canceled", 0),
                    }
                else:
                    assert settled.status == "completed"
                    assert settled.reason_code == "approval_expired"
                    assert wait.status == "expired" and actions == 0
                    assert (
                        sum(
                            isinstance(item, dict)
                            and item.get("code") == "approval_expired"
                            or isinstance(item, list)
                            and run.id in item
                            for item in outcomes
                        )
                        == 1
                    )
            assert message_run(runtime, settled).status == settled.status
            assert model_count == len(models.child_requests) + len(models.parent_requests)

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_same_source_drafts_dedupe_across_runs_and_expired_offer_cannot_save(targets):
    engine = prepare(targets)

    async def scenario():
        async with service(engine) as runtime:
            drafts = []
            for _ in range(3):
                worker, _ = executor(runtime)
                run = await execute(
                    runtime, command(question=DYNAMIC, showcase="dynamic_workflow"), worker
                )
                assert run.status == "completed"
                offered = next(
                    packet.event.payload.value
                    for packet in runtime.reader.messages(run.session_id, CHAT).packets
                    if packet.event.type == "view.upsert"
                    and packet.event.payload.kind == "workflow"
                )
                drafts.append((run, UUID(offered.draft_id)))
            resources = RuntimeResources(runtime.store)
            saved = resources.confirm(CHAT, drafts[0][1], "跨运行保存名称")
            duplicate = resources.confirm(CHAT, drafts[1][1], "同源重复保存")
            assert saved is not None and duplicate is not None
            assert saved.asset_id == duplicate.asset_id
            assert duplicate.name == saved.name == "跨运行保存名称"
            for run, identifier in drafts[:2]:
                await runtime.publisher.flush(run.id)
                saved_view = next(
                    packet.event.payload.value
                    for packet in runtime.reader.messages(run.session_id, CHAT).packets
                    if packet.event.type == "view.upsert"
                    and packet.event.payload.kind == "workflow"
                )
                assert saved_view.status == "saved"
                assert saved_view.asset_id == str(saved.asset_id)
                assert saved_view.draft.name == "跨运行保存名称"
                repeated = resources.confirm(CHAT, identifier, "重复确认")
                assert repeated is not None and repeated.asset_id == saved.asset_id
            with Session(engine) as tx, tx.begin():
                expired = tx.get(AgentWorkflowDraft, drafts[2][1])
                assert expired is not None
                expired.expires_at = now() - timedelta(seconds=1)
                assert tx.scalar(select(func.count()).select_from(WorkflowAsset)) == 1
            for action in [
                lambda: resources.get(CHAT, drafts[2][1]),
                lambda: resources.confirm(CHAT, drafts[2][1], "已过期"),
            ]:
                with pytest.raises(ModelError, match="draft_expired") as denied:
                    action()
                assert denied.value.status == 410
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(WorkflowAsset)) == 1

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_parallel_workflow_process_exit_after_host_commit_replays_without_child_dispatch(
    targets, tmp_path
):
    import json
    import socket
    import subprocess
    import sys
    import time
    from collections import Counter
    from pathlib import Path

    from compass_agent.runtime.entities import AgentCheckpoint, AgentRun

    from .test_agent_run import auth_headers
    from .workflow_fixture import SOURCE

    engine = prepare(targets)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    audit_path = tmp_path / "workflow-calls.jsonl"
    log_path = tmp_path / "workflow-crash.log"
    env = {
        **os.environ,
        "COMPASS_AGENT_SERVICE_TOKEN": "run-service-token",
        "COMPASS_TEST_CRASH_BOUNDARY": "host_result_committed",
        "COMPASS_TEST_CRASH_HOST_RESULTS": "3",
        "COMPASS_TEST_CALL_AUDIT": str(audit_path),
        "COMPASS_TEST_REAL_MODEL": "",
    }
    child = None
    log = log_path.open("w")

    def start():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "apps.agent.tests.durable_process_fixture:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--timeout-graceful-shutdown",
                "2",
            ],
            env=env,
            cwd=Path(__file__).resolve().parents[3],
            stdout=log,
            stderr=log,
        )
        for _ in range(150):
            assert process.poll() is None, log_path.read_text()
            try:
                if httpx.get(origin + "/readyz", timeout=1).status_code == 200:
                    return process
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        process.terminate()
        process.wait(timeout=8)
        pytest.fail("isolated Workflow Agent readiness timed out")

    def audit_rows():
        return [json.loads(line) for line in audit_path.read_text().splitlines()]

    def accepted_attempts(tx, identifier):
        return {
            row.id: (
                row.step_id,
                row.number,
                row.output_sha256,
                row.dispatched_at,
                row.finished_at,
                row.delivery_no,
            )
            for row in tx.scalars(
                select(AgentAttempt).where(
                    AgentAttempt.run_id == identifier, AgentAttempt.status == "completed"
                )
            )
        }

    try:
        child = start()
        with httpx.Client(base_url=origin, headers=auth_headers(CHAT), timeout=15) as client:
            submitted = client.post(
                "/v1/analysis/sessions/submit",
                json=command(question=DYNAMIC, showcase="dynamic_workflow").model_dump(
                    mode="json", by_alias=True
                ),
            )
            assert submitted.status_code == 202, submitted.text
            receipt = submitted.json()
            identifier = UUID(receipt["runId"])
            assert child.wait(timeout=45) == 86
            before_audit = audit_rows()
            assert before_audit[-1] == {
                "kind": "boundary",
                "runId": str(identifier),
                "hostResults": 3,
            }
            assert (
                sum(
                    row["kind"] == "model"
                    and row["phase"] == "child"
                    and row["state"] == "completed"
                    for row in before_audit
                )
                == 6
            )
            assert (
                sum(row["kind"] == "retail" and row["state"] == "completed" for row in before_audit)
                == 3
            )
            with Session(engine) as tx:
                run = tx.get(AgentRun, identifier)
                assert run is not None and run.status == "running"
                before = accepted_attempts(tx, identifier)
                workflow = tx.scalars(
                    select(AgentStep).where(
                        AgentStep.run_id == identifier, AgentStep.kind == "workflow"
                    )
                ).one()
                definition = workflow.definition_snapshot
                assert definition["source"] == SOURCE
                host_rows = tx.execute(
                    select(AgentStep, AgentAttempt)
                    .join(AgentAttempt, AgentStep.accepted_attempt_id == AgentAttempt.id)
                    .where(AgentStep.run_id == identifier, AgentStep.kind == "workflow_host")
                    .order_by(AgentAttempt.delivery_no)
                ).all()
                assert len(host_rows) == 3
                delivery_order = [
                    (step.node_key, attempt.id, attempt.delivery_no) for step, attempt in host_rows
                ]
                assert {int(key.rsplit(":", 1)[1]) for key, _, _ in delivery_order} == {1, 2, 3}
                checkpoint = tx.get(AgentCheckpoint, (identifier, run.checkpoint_no))
                assert checkpoint is not None
                completed_ids = {ref["attemptId"] for ref in checkpoint.completed_refs}
                assert {str(attempt.id) for _, attempt in host_rows} <= completed_ids
                workflow_id = workflow.id
            env["COMPASS_TEST_CRASH_BOUNDARY"] = ""
            child = start()
            with Session(engine) as tx:
                run = tx.get(AgentRun, identifier)
                assert run is not None and run.status == "paused"
                version = run.version
                assert accepted_attempts(tx, identifier) == before
            resumed = client.post(
                f"/v1/analysis/runs/{identifier}/resume",
                json={"requestId": str(uuid4()), "expectedVersion": version},
            )
            assert resumed.status_code == 200, resumed.text
            for _ in range(300):
                with Session(engine) as tx:
                    run = tx.get(AgentRun, identifier)
                    assert run is not None
                    if run.status in {"completed", "failed", "paused", "canceled"}:
                        # Scheduling may briefly retain paused before the task claims its epoch.
                        if run.status != "paused" or run.reason_code:
                            break
                time.sleep(0.1)
            assert run.status == "completed", (run.status, run.reason_code)
            with Session(engine) as tx:
                after = accepted_attempts(tx, identifier)
                assert {key: after[key] for key in before} == before
                workflow = tx.get(AgentStep, workflow_id)
                assert workflow is not None and workflow.definition_snapshot == definition
                host_rows = tx.execute(
                    select(AgentStep.node_key, AgentAttempt.id, AgentAttempt.delivery_no)
                    .join(AgentAttempt, AgentStep.accepted_attempt_id == AgentAttempt.id)
                    .where(AgentStep.run_id == identifier, AgentStep.kind == "workflow_host")
                    .order_by(AgentAttempt.delivery_no)
                ).all()
                assert [tuple(row) for row in host_rows] == delivery_order
            after_audit = audit_rows()
            before_children = [
                row for row in before_audit if row["kind"] == "model" and row["phase"] == "child"
            ]
            after_children = [
                row for row in after_audit if row["kind"] == "model" and row["phase"] == "child"
            ]
            assert after_children == before_children
            assert [row for row in after_audit if row["kind"] == "retail"] == [
                row for row in before_audit if row["kind"] == "retail"
            ]
            assert all(
                count == 1
                for count in Counter(
                    row["callId"]
                    for row in after_audit
                    if row["kind"] == "model" and row["state"] == "started"
                ).values()
            )
            draft = None
            snapshot = None
            for _ in range(100):
                snapshot = client.get(f"/v1/analysis/sessions/{receipt['sessionId']}/messages")
                assert snapshot.status_code == 200
                draft = next(
                    (
                        packet["event"]["payload"]["value"]
                        for packet in snapshot.json()["packets"]
                        if packet["event"]["type"] == "view.upsert"
                        and packet["event"]["payload"]["kind"] == "workflow"
                    ),
                    None,
                )
                if draft:
                    break
                time.sleep(0.1)
            assert snapshot is not None
            assert draft is not None, snapshot.text
            assert draft["draft"]["scriptSource"] == SOURCE
            saved = client.post(
                f"/v1/analysis/workflows/drafts/{draft['draftId']}/confirm",
                json={"name": "真实并行进程恢复后保存"},
            )
            assert saved.status_code == 200, saved.text
            repeated = client.post(
                f"/v1/analysis/workflows/drafts/{draft['draftId']}/confirm",
                json={"name": "重复保存"},
            )
            assert (
                repeated.status_code == 200
                and repeated.json()["assetId"] == saved.json()["assetId"]
            )
            assert audit_rows() == after_audit
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        log.close()
        engine.dispose()
