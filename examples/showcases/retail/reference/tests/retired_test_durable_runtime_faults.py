"""Historical fixed-case tests; execution contracts retired in Feature 978."""

def test_http_disconnect_and_process_exit_after_effect_commit_preserve_one_effect(
    targets, tmp_path
):
    import sys
    import time

    import httpx
    from compass_agent.runtime.entities import (
        AgentAction,
        AgentDemoEffect,
        AgentRun,
        AgentStep,
        AgentWait,
    )
    from sqlalchemy import func

    from .test_agent_run import auth_headers

    engine = prepare(targets)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "COMPASS_AGENT_SERVICE_TOKEN": "run-service-token",
        "COMPASS_TEST_CRASH_BOUNDARY": "effect_committed",
    }
    child = None
    log = (tmp_path / "agent-crash.log").open("w")

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
        for _ in range(120):
            assert process.poll() is None, (tmp_path / "agent-crash.log").read_text()
            try:
                if httpx.get(origin + "/readyz", timeout=1).status_code == 200:
                    return process
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        process.terminate()
        process.wait(timeout=8)
        pytest.fail("isolated Agent readiness timed out")

    try:
        child = start()
        with httpx.Client(base_url=origin, headers=auth_headers(CHAT), timeout=10) as client:
            response = client.post(
                "/v1/analysis/sessions/submit",
                json=command(showcase="budget_proposal").model_dump(mode="json", by_alias=True),
            )
            assert response.status_code == 202, response.text
            accepted = response.json()
            run_id = UUID(accepted["runId"])
            # Closing this subscription must leave the application-owned task running.
            with client.stream("GET", f"/v1/analysis/runs/{run_id}/events") as stream:
                assert stream.status_code == 200
                assert next(line for line in stream.iter_lines() if line.startswith("data:"))
            for _ in range(150):
                with Session(engine) as tx:
                    run = tx.get(AgentRun, run_id)
                    if run and run.status == "waiting":
                        wait = tx.scalars(select(AgentWait).where(AgentWait.run_id == run_id)).one()
                        before_steps = tx.scalar(
                            select(func.count())
                            .select_from(AgentStep)
                            .where(AgentStep.run_id == run_id)
                        )
                        decision_body = {
                            "requestId": str(uuid4()),
                            "expectedVersion": run.version,
                            "waitId": str(wait.id),
                            "proposalId": str(wait.proposal_id),
                            "proposalRevision": wait.proposal_revision,
                            "proposalDigest": wait.proposal_sha256,
                            "decisionId": str(uuid4()),
                            "decision": "approve",
                        }
                        break
                time.sleep(0.1)
            else:
                pytest.fail("disconnected Run did not reach its saved approval")
            decided = client.post(f"/v1/analysis/runs/{run_id}/decisions", json=decision_body)
            assert decided.status_code == 200, decided.text
            assert child.wait(timeout=12) == 86
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 1
                assert tx.scalars(select(AgentAction)).one().state == "pending"
            env["COMPASS_TEST_CRASH_BOUNDARY"] = ""
            child = start()
            snapshot = client.get(f"/v1/analysis/sessions/{accepted['sessionId']}/messages")
            assert snapshot.status_code == 200
            assert any(
                p["event"]["type"] == "run.upsert"
                and p["event"]["payload"]["status"] == "completed"
                for p in snapshot.json()["packets"]
            )
            with Session(engine) as tx:
                assert tx.scalar(select(func.count()).select_from(AgentDemoEffect)) == 1
                action = tx.scalars(select(AgentAction)).one()
                assert action.state == "applied" and action.receipt
                assert (
                    tx.scalar(
                        select(func.count())
                        .select_from(AgentStep)
                        .where(AgentStep.run_id == run_id)
                    )
                    == before_steps
                )
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        log.close()
        engine.dispose()
