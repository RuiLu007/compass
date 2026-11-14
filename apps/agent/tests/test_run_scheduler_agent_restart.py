"""SIGKILL the real M6 Agent while two unchanged Worker processes keep polling."""

import json
import os
import secrets
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from compass_agent.runtime.entities import (
    AgentAttempt,
    AgentCommand,
    AgentOutbox,
    AgentRun,
    AgentRunJob,
)
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .test_agent_run import auth_headers
from .test_database_tools_integration import targets as targets
from .test_durable_runtime_integration import command
from .test_run_scheduler_integration import engine as engine
from .test_run_scheduler_integration import owner
from .test_run_scheduler_process import FIXTURE, ROOT, free_port, records, stop_process


def test_real_agent_restart_interrupts_advance_and_preserves_checkpoint_and_pause(engine, tmp_path):
    directory = ROOT / "work/lesson-20"
    directory.mkdir(parents=True, exist_ok=True)
    audit = tmp_path / "agent-restart-audit.jsonl"
    report: dict[str, object] = {
        "result": "running",
        "scope": "real Agent SIGKILL; two persistent HTTP Workers; in-flight Worker SIGTERM",
    }
    credentials = {name: secrets.token_urlsafe(32) for name in ("restart-a", "restart-b")}
    service_token = secrets.token_urlsafe(32)
    base = f"http://127.0.0.1:{free_port()}"
    environment = {
        **os.environ,
        "DATABASE_URL": engine.url.render_as_string(hide_password=False),
        "COMPASS_WORKER_CREDENTIALS": json.dumps(credentials),
        "COMPASS_AGENT_SERVICE_TOKEN": service_token,
        "COMPASS_PROCESS_AUDIT": str(audit),
        "COMPASS_OBSERVABILITY_ENABLED": "false",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "COMPASS_RUNTIME_LEASE_SECONDS": "15",
        "COMPASS_RUNTIME_MAX_ACTIVE": "2",
        "COMPASS_RUNTIME_TENANT_ACTIVE": "1",
    }
    children, streams, workers = [], [], []
    lock = None

    def spawn(args, env, name):
        output = (directory / f"agent-restart-{name}.log").open("wb")
        streams.append(output)
        process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
        children.append(process)
        return process

    def start_agent(name):
        return spawn(
            [
                sys.executable,
                "-m",
                "uvicorn",
                f"{FIXTURE}:app",
                "--host",
                "127.0.0.1",
                "--port",
                base.rsplit(":", 1)[1],
                "--timeout-graceful-shutdown",
                "2",
            ],
            environment,
            name,
        )

    def wait_until(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.05)
        raise AssertionError(f"Agent restart condition timed out after {timeout}s")

    def live_event(kind, run_id):
        return next(
            (
                row
                for row in reversed(records(audit))
                if row["kind"] == kind and row.get("runId") == run_id
            ),
            None,
        )

    def run_snapshot(run_id):
        with Session(engine) as tx:
            run, job = tx.execute(
                select(AgentRun, AgentRunJob).join(AgentRunJob).where(AgentRun.id == UUID(run_id))
            ).one()
            return {
                "runId": run_id,
                "status": run.status,
                "version": run.version,
                "job": job.status,
                "epoch": str(run.execution_epoch) if run.execution_epoch else None,
                "claimCount": job.claim_count,
                "checkpointNo": run.checkpoint_no,
                "reason": run.reason_code,
            }

    try:
        agent = start_agent("before")
        with httpx.Client(base_url=base, timeout=5, trust_env=False) as client:

            def health(expected):
                try:
                    return client.get("/healthz").status_code == expected
                except httpx.HTTPError:
                    return False

            def submit(question):
                response = client.post(
                    "/v1/analysis/sessions/submit",
                    headers=auth_headers(owner(), service_token),
                    json=command(question=question).model_dump(mode="json", by_alias=True),
                )
                assert response.status_code == 202, response.text
                return response.json()

            def messages(receipt):
                response = client.get(
                    f"/v1/analysis/sessions/{receipt['sessionId']}/messages",
                    headers=auth_headers(owner(), service_token),
                )
                assert response.status_code == 200, response.text
                return response.json()

            wait_until(lambda: health(200), 25)
            for name, token in credentials.items():
                workers.append(
                    spawn(
                        [str(Path(sys.executable).with_name("compass-worker"))],
                        {
                            "PATH": os.environ.get("PATH", ""),
                            "LANG": "en_US.UTF-8",
                            "AGENT_API_URL": base,
                            "COMPASS_WORKER_ID": name,
                            "COMPASS_WORKER_TOKEN": token,
                            "COMPASS_RUNTIME_LEASE_SECONDS": "15",
                        },
                        name,
                    )
                )
            wait_until(
                lambda: (
                    {row["workerId"] for row in records(audit) if row["kind"] == "idle_claim"}
                    == set(credentials)
                )
            )
            paused = submit("process:recover:paused-before-agent-restart")
            paused_id = paused["runId"]
            wait_until(lambda: live_event("checkpoint_boundary_held", paused_id))
            before_pause = run_snapshot(paused_id)
            response = client.post(
                f"/v1/analysis/runs/{paused_id}/pause",
                headers=auth_headers(owner(), service_token),
                json={"requestId": str(uuid4()), "expectedVersion": before_pause["version"]},
            )
            assert response.status_code == 200, response.text
            paused_record = wait_until(
                lambda: row if (row := run_snapshot(paused_id))["job"] == "parked" else None
            )
            assert paused_record["status"] == "paused" and paused_record["checkpointNo"] == 1

            def paused_published():
                with Session(engine) as tx:
                    return (
                        tx.scalar(
                            select(func.count())
                            .select_from(AgentOutbox)
                            .where(
                                AgentOutbox.run_id == UUID(paused_id),
                                AgentOutbox.delivered_at.is_(None),
                            )
                        )
                        == 0
                    )

            wait_until(paused_published)
            paused_messages = messages(paused)
            accepted = submit("process:agent-restart:safe-checkpoint")
            run_id = accepted["runId"]
            wait_until(lambda: live_event("agent_restart_boundary_held", run_id))
            before = run_snapshot(run_id)
            assert before["checkpointNo"] == 1 and before["claimCount"] == 1
            with Session(engine) as tx:
                original = tx.scalars(
                    select(AgentAttempt).where(AgentAttempt.run_id == UUID(run_id))
                ).one()
                assert original.status == "completed" and original.output_sha256
                original_identity = original.id, original.output_sha256
                receipt = tx.scalars(
                    select(AgentCommand).where(
                        AgentCommand.run_id == UUID(run_id), AgentCommand.kind == "worker_advance"
                    )
                ).all()
                running = [row for row in receipt if row.response["state"] == "running"]
                assert len(running) == 1 and running[0].response["expectedCheckpoint"] == 1
                interrupted_id, old_grant = running[0].id, running[0].response["grant"]
            agent.kill()
            agent.wait(3)
            assert agent.returncode == -9 and all(worker.poll() is None for worker in workers)
            killed_at = time.monotonic()
            # A real conflicting owner lock holds only this test's isolated database.
            # Startup therefore serves its actual unavailable state until released.
            lock = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
            held_lock = lock
            wait_until(lambda: held_lock.scalar(text("SELECT pg_try_advisory_lock(985001)")))
            successor = start_agent("after")
            wait_until(lambda: health(503), 25)
            before_ready = run_snapshot(run_id)
            assert before_ready == before
            claim = client.post(
                "/internal/runtime/jobs/claim",
                headers={"Authorization": "Bearer " + credentials["restart-a"]},
                json={"bootId": str(uuid4())},
            )
            assert claim.status_code == 503 and claim.json()["code"] == "runtime_owner_unavailable"
            unready_advance = client.post(
                "/internal/runtime/jobs/advance",
                headers={"Authorization": "Bearer " + credentials[old_grant["workerId"]]},
                json={"grant": old_grant, "expectedCheckpoint": 1},
            )
            assert unready_advance.status_code == 503
            assert run_snapshot(run_id) == before
            assert (
                len(
                    [
                        row
                        for row in records(audit)
                        if row["kind"] == "physical" and row.get("runId") == run_id
                    ]
                )
                == 2
            )
            lock.execute(text("SELECT pg_advisory_unlock(985001)"))
            lock.close()
            lock = None
            released_at = time.monotonic()
            wait_until(lambda: health(200))
            settled = wait_until(
                lambda: row if (row := run_snapshot(run_id))["status"] == "completed" else None
            )
            assert settled["job"] == "done" and settled["claimCount"] == 2
            revoked = live_event("revoked", run_id)
            assert revoked and revoked["reason"] == "agent_restarted" and revoked["epoch"] is None
            claims = [
                row
                for row in records(audit)
                if row["kind"] == "claim" and row["grant"]["runId"] == run_id
            ]
            assert len(claims) == 2
            new_grant = claims[-1]["grant"]
            assert new_grant["epoch"] != old_grant["epoch"] and new_grant["checkpointNo"] == 1
            assert all(worker.poll() is None for worker in workers)
            stale = client.post(
                "/internal/runtime/jobs/advance",
                headers={"Authorization": "Bearer " + credentials[old_grant["workerId"]]},
                json={"grant": old_grant, "expectedCheckpoint": 1},
            )
            assert stale.status_code == 409 and stale.json()["code"] == "lease_lost"
            with Session(engine) as tx:
                receipt = tx.get(AgentCommand, interrupted_id)
                assert receipt and receipt.response["state"] == "interrupted"
                assert receipt.response["ack"]["reasonCode"] == "agent_restarted"
                interrupted_receipt = receipt.response
                attempts = tx.scalars(
                    select(AgentAttempt).where(AgentAttempt.run_id == UUID(run_id))
                ).all()
                assert len(attempts) == 2 and all(row.status == "completed" for row in attempts)
                assert original_identity in {(row.id, row.output_sha256) for row in attempts}
            physical = [
                row
                for row in records(audit)
                if row["kind"] == "physical"
                and row.get("runId") == run_id
                and row["state"] == "started"
            ]
            assert Counter(row["step"] for row in physical) == {"sum": 1, "double": 1}

            def completed_message(receipt):
                value = messages(receipt)
                answers = [
                    packet["event"]["payload"]
                    for packet in value["packets"]
                    if packet["event"]["type"] == "message.upsert"
                    and packet["event"]["payload"]["role"] == "assistant"
                ]
                assert len(answers) == 1
                return answers[0] if answers[0]["status"] == "completed" else None

            answer = wait_until(lambda: completed_message(accepted))
            assert answer["content"]["text"] == "本轮计算结果为 380。"
            assert run_snapshot(paused_id) == paused_record
            assert messages(paused) == paused_messages
            assert (
                len(
                    [
                        row
                        for row in records(audit)
                        if row["kind"] == "claim" and row["grant"]["runId"] == paused_id
                    ]
                )
                == 1
            )
            report.update(
                result="passed",
                killedAgentPid=agent.pid,
                newAgentPid=successor.pid,
                persistentWorkerPids=[worker.pid for worker in workers],
                runId=run_id,
                originalAttemptId=str(original_identity[0]),
                originalOutputSha256=original_identity[1],
                oldGrant=old_grant,
                newGrant=new_grant,
                interruptedReceipt=interrupted_receipt,
                beforeReadyClaimStatus=claim.status_code,
                beforeReadyAdvanceStatus=unready_advance.status_code,
                killToClaimSeconds=claims[-1]["monotonic"] - killed_at,
                ownerUnlockToClaimSeconds=claims[-1]["monotonic"] - released_at,
                finalRun=settled,
                finalAnswer=answer["content"]["text"],
                physicalCalls={"sum": 1, "double": 1},
                retainedPausedRun=paused_record,
                workerRecoveryDenominator=(
                    "separate Agent restart sample; excluded from Worker SIGKILL statistics"
                ),
            )

            graceful = submit("process:normal:in-flight-sigterm")
            graceful_id = graceful["runId"]
            in_flight = wait_until(lambda: live_event("physical", graceful_id))
            assert in_flight["step"] == "sum" and in_flight["state"] == "started"
            graceful_grant = next(
                row["grant"]
                for row in reversed(records(audit))
                if row["kind"] == "claim" and row["grant"]["runId"] == graceful_id
            )
            victim = workers[list(credentials).index(graceful_grant["workerId"])]
            signaled_at = time.monotonic()
            victim.terminate()
            victim.wait(12)
            exit_seconds = time.monotonic() - signaled_at
            assert victim.returncode == 0 and exit_seconds <= 12
            graceful_run = wait_until(
                lambda: row if (row := run_snapshot(graceful_id))["status"] == "completed" else None
            )
            assert graceful_run["job"] == "done" and graceful_run["claimCount"] == 2
            graceful_answer = wait_until(lambda: completed_message(graceful))
            assert graceful_answer["content"]["text"] == "本轮计算结果为 380。"
            graceful_records = records(audit)
            releases = [
                row
                for row in graceful_records
                if row["kind"] == "released" and row["grant"]["runId"] == graceful_id
            ]
            assert len(releases) == 1 and releases[0]["epoch"] is None
            assert releases[0]["grant"]["epoch"] == graceful_grant["epoch"]
            assert not [
                row
                for row in graceful_records
                if row["kind"] == "claim"
                and row["grant"]["workerId"] == graceful_grant["workerId"]
                and row["monotonic"] > signaled_at
            ]
            graceful_claims = [
                row["grant"]
                for row in graceful_records
                if row["kind"] == "claim" and row["grant"]["runId"] == graceful_id
            ]
            assert len(graceful_claims) == 2
            assert graceful_claims[1]["workerId"] != graceful_grant["workerId"]
            assert graceful_claims[1]["epoch"] != graceful_grant["epoch"]
            assert graceful_claims[1]["checkpointNo"] == 1
            graceful_physical = [
                row
                for row in graceful_records
                if row["kind"] == "physical"
                and row.get("runId") == graceful_id
                and row["state"] == "started"
            ]
            assert Counter(row["step"] for row in graceful_physical) == {"sum": 1, "double": 1}
            report["workerSigterm"] = {
                "runId": graceful_id,
                "workerPid": victim.pid,
                "exitCode": victim.returncode,
                "exitSeconds": exit_seconds,
                "signalMonotonic": signaled_at,
                "releaseCount": len(releases),
                "oldGrant": graceful_grant,
                "newGrant": graceful_claims[1],
                "physicalCalls": {"sum": 1, "double": 1},
                "finalAnswer": graceful_answer["content"]["text"],
                "finalRun": graceful_run,
                "includedInWorkerCrashDenominator": False,
            }
    except BaseException as error:
        report.update(result="failed", failureType=type(error).__name__, failure=str(error))
        raise
    finally:
        if lock is not None:
            lock.execute(text("SELECT pg_advisory_unlock(985001)"))
            lock.close()
        report["audit"] = records(audit)
        target = directory / "agent-restart-acceptance.json"
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        target.chmod(0o600)
        for process in reversed(children):
            stop_process(process)
        for output in streams:
            output.close()
