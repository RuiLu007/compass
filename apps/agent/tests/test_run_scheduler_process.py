"""Real dual-Worker HTTP quota, SIGKILL recovery and unknown-effect acceptance."""

import json
import math
import os
import secrets
import socket
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from compass_agent.runtime.entities import AgentAttempt, AgentRun, AgentRunJob
from sqlalchemy import select
from sqlalchemy.orm import Session

from .test_agent_run import auth_headers
from .test_database_tools_integration import targets as targets
from .test_durable_runtime_integration import command
from .test_run_scheduler_integration import TENANT_A, TENANT_B, owner
from .test_run_scheduler_integration import engine as engine

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = "apps.agent.tests.test_run_scheduler_process_fixture"


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def percentile(values):
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50": ordered[max(0, math.ceil(len(ordered) * 0.5) - 1)],
        "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        "max": ordered[-1],
    }


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


def test_two_real_workers_quotas_three_crash_recoveries_and_unknown_capacity(engine, tmp_path):
    directory = ROOT / "work/lesson-20"
    directory.mkdir(parents=True, exist_ok=True)
    audit = tmp_path / "process-audit.jsonl"
    report = {
        "scope": "real PostgreSQL/Redis/HTTP Agent and Worker; deterministic local executor",
        "leaseSeconds": 15,
        "heartbeatSeconds": 3,
        "globalCapacity": 2,
        "tenantCapacity": 1,
        "recoveries": [],
        "samples": [],
        "processes": [],
    }
    credentials = {identifier: secrets.token_urlsafe(32) for identifier in ("worker-a", "worker-b")}
    service_token = secrets.token_urlsafe(32)
    base = f"http://127.0.0.1:{free_port()}"
    effect_url = f"http://127.0.0.1:{free_port()}"
    env = {
        **os.environ,
        "DATABASE_URL": str(engine.url.render_as_string(hide_password=False)),
        "COMPASS_WORKER_CREDENTIALS": json.dumps(credentials),
        "COMPASS_AGENT_SERVICE_TOKEN": service_token,
        "COMPASS_PROCESS_AUDIT": str(audit),
        "COMPASS_PROCESS_EFFECT_URL": effect_url,
        "COMPASS_OBSERVABILITY_ENABLED": "false",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "COMPASS_RUNTIME_LEASE_SECONDS": "15",
        "COMPASS_RUNTIME_MAX_ACTIVE": "2",
        "COMPASS_RUNTIME_TENANT_ACTIVE": "1",
        "COMPASS_RUNTIME_TENANT_BACKLOG": "4",
        "COMPASS_RUNTIME_MAX_BACKLOG": "12",
    }
    children = []
    streams = []
    workers = {}

    def spawn(args, child_env, name):
        output = (directory / f"process-{name}.log").open("ab")
        streams.append(output)
        process = subprocess.Popen(
            args, cwd=ROOT, env=child_env, stdout=output, stderr=subprocess.STDOUT
        )
        children.append(process)
        report["processes"].append({"name": name, "pid": process.pid})
        return process

    def start_worker(identifier):
        # Workers receive only their HTTP credential and process configuration.
        child_env = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": "en_US.UTF-8",
            "AGENT_API_URL": base,
            "COMPASS_WORKER_ID": identifier,
            "COMPASS_WORKER_TOKEN": credentials[identifier],
            "COMPASS_RUNTIME_LEASE_SECONDS": "15",
        }
        workers[identifier] = spawn(
            [str(Path(sys.executable).with_name("compass-worker"))], child_env, identifier
        )

    def snapshot():
        with Session(engine) as tx:
            rows = tx.execute(select(AgentRunJob, AgentRun).join(AgentRun)).all()
            values = [
                {
                    "runId": str(run.id),
                    "sessionId": str(run.session_id),
                    "tenantId": str(job.tenant_id),
                    "jobStatus": job.status,
                    "runStatus": run.status,
                    "recoveryStatus": run.recovery_status,
                    "reasonCode": run.reason_code,
                    "workerId": job.worker_id,
                    "bootId": str(job.worker_boot_id) if job.worker_boot_id else None,
                    "epoch": str(run.execution_epoch) if run.execution_epoch else None,
                    "checkpointNo": run.checkpoint_no,
                    "claimCount": job.claim_count,
                    "createdAt": run.created_at.isoformat(),
                    "firstClaimedAt": job.first_claimed_at.isoformat()
                    if job.first_claimed_at
                    else None,
                }
                for job, run in rows
            ]
        active = [value for value in values if value["jobStatus"] in {"leased", "reclaiming"}]
        counts = Counter(value["tenantId"] for value in active)
        assert len(active) <= 2, active
        assert all(count <= 1 for count in counts.values()), active
        report["samples"].append(
            {
                "monotonic": time.monotonic(),
                "occupied": len(active),
                "leased": sum(value["jobStatus"] == "leased" for value in active),
                "reclaiming": sum(value["jobStatus"] == "reclaiming" for value in active),
                "tenants": dict(counts),
            }
        )
        return {value["runId"]: value for value in values}

    def wait_until(predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.05)
        raise AssertionError(f"Process acceptance condition timed out after {timeout}s")

    def live_event(kind, run_id):
        return next(
            (
                row
                for row in reversed(records(audit))
                if row["kind"] == kind
                and (row.get("runId") or row.get("grant", {}).get("runId")) == run_id
            ),
            None,
        )

    def get_health(url):
        try:
            return httpx.get(url + "/healthz", timeout=1, trust_env=False).status_code == 200
        except httpx.HTTPError:
            return False

    try:
        for name, url, application in [
            ("effect", effect_url, "effect_app"),
            ("agent", base, "app"),
        ]:
            spawn(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    f"{FIXTURE}:{application}",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    url.rsplit(":", 1)[1],
                    "--timeout-graceful-shutdown",
                    "2",
                ],
                env,
                name,
            )
            wait_until(lambda url=url: get_health(url), 20)
        with httpx.Client(base_url=base, timeout=5, trust_env=False) as client:

            def submit(tenant, question):
                response = client.post(
                    "/v1/analysis/sessions/submit",
                    headers=auth_headers(owner(tenant), service_token),
                    json=command(question=question).model_dump(mode="json", by_alias=True),
                )
                assert response.status_code == 202, response.text
                return response.json()

            def assert_answer(receipt, tenant):
                def projected():
                    response = client.get(
                        f"/v1/analysis/sessions/{receipt['sessionId']}/messages",
                        headers=auth_headers(owner(tenant), service_token),
                    )
                    assert response.status_code == 200, response.text
                    packets = response.json()["packets"]
                    answers = [
                        packet["event"]["payload"]
                        for packet in packets
                        if packet["event"]["type"] == "message.upsert"
                        and packet["event"]["payload"]["role"] == "assistant"
                    ]
                    assert len(answers) == 1
                    if answers[0]["status"] != "completed":
                        return False
                    assert answers[0]["content"]["text"] == "本轮计算结果为 380。"
                    assert any(
                        packet["event"]["type"] == "run.upsert"
                        and packet["event"]["payload"]["status"] == "completed"
                        for packet in packets
                    )
                    return True

                # Authoritative Run commit precedes the separate M5 public projection.
                wait_until(projected, 5)

            start_worker("worker-a")
            start_worker("worker-b")
            wait_until(
                lambda: (
                    {row["workerId"] for row in records(audit) if row["kind"] == "idle_claim"}
                    == set(credentials)
                ),
                10,
            )
            report["observedFrom"] = datetime.now(UTC).isoformat()
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [
                    pool.submit(submit, tenant, f"process:baseline:{tenant}:{index}")
                    for index in range(4)
                    for tenant in (TENANT_A, TENANT_B)
                ]
                baseline = [future.result() for future in futures]
            rejected = client.post(
                "/v1/analysis/sessions/submit",
                headers=auth_headers(owner(TENANT_A), service_token),
                json=command(question="process:excess").model_dump(mode="json", by_alias=True),
            )
            assert rejected.status_code == 429, rejected.text
            initial = snapshot()
            assert len(initial) == 8 and len({row["sessionId"] for row in initial.values()}) == 8
            first_b = wait_until(
                lambda: next(
                    (
                        value
                        for value in snapshot().values()
                        if value["tenantId"] == str(TENANT_B) and value["firstClaimedAt"]
                    ),
                    None,
                ),
                3,
            )
            first_b_delay = (
                datetime.fromisoformat(first_b["firstClaimedAt"])
                - datetime.fromisoformat(first_b["createdAt"])
            ).total_seconds()
            assert first_b_delay <= 3, first_b_delay
            baseline_ids = {value["runId"] for value in baseline}
            wait_until(
                lambda: all(
                    value["runStatus"] == "completed"
                    for identifier, value in snapshot().items()
                    if identifier in baseline_ids
                ),
                25,
            )
            settled_baseline = snapshot()
            for receipt in baseline:
                assert_answer(receipt, UUID(settled_baseline[receipt["runId"]]["tenantId"]))
            report["baseline"] = {
                "runs": [settled_baseline[identifier] for identifier in sorted(baseline_ids)],
                "rejectedTenantA": rejected.status_code,
                "firstTenantBSeconds": first_b_delay,
                "queueSeconds": {
                    str(tenant): percentile(
                        [
                            (
                                datetime.fromisoformat(row["firstClaimedAt"])
                                - datetime.fromisoformat(row["createdAt"])
                            ).total_seconds()
                            for identifier, row in settled_baseline.items()
                            if identifier in baseline_ids and row["tenantId"] == str(tenant)
                        ]
                    )
                    for tenant in (TENANT_A, TENANT_B)
                },
            }

            for index in range(3):
                accepted = submit(TENANT_A, f"process:recover:{index}")
                run_id = accepted["runId"]
                wait_until(lambda run_id=run_id: live_event("checkpoint_boundary_held", run_id), 10)
                heartbeat = wait_until(lambda run_id=run_id: live_event("heartbeat", run_id), 6)
                old_grant = heartbeat["grant"]
                victim = workers[old_grant["workerId"]]
                with Session(engine) as tx:
                    original_attempt = tx.scalars(
                        select(AgentAttempt).where(AgentAttempt.run_id == UUID(run_id))
                    ).one()
                    first_attempt_id = str(original_attempt.id)
                    assert original_attempt.status == "completed"
                victim.kill()
                victim.wait(3)
                assert victim.returncode == -9
                killed_at = time.monotonic()
                successor = wait_until(
                    lambda run_id=run_id: (
                        row if (row := snapshot()[run_id])["claimCount"] == 2 else None
                    ),
                    21,
                )
                next_claim = live_event("claim", run_id)
                assert next_claim and next_claim["grant"]["epoch"] != old_grant["epoch"]
                assert next_claim["grant"]["workerId"] != old_grant["workerId"]
                assert next_claim["grant"]["checkpointNo"] == 1
                delay = next_claim["monotonic"] - heartbeat["monotonic"]
                assert delay <= 21, delay
                stale_headers = {"Authorization": "Bearer " + credentials[old_grant["workerId"]]}
                for operation, body in [
                    ("heartbeat", {"grant": old_grant}),
                    ("advance", {"grant": old_grant, "expectedCheckpoint": 1}),
                ]:
                    stale = client.post(
                        "/internal/runtime/jobs/" + operation, headers=stale_headers, json=body
                    )
                    assert stale.status_code == 409 and stale.json()["code"] == "lease_lost"
                wait_until(lambda run_id=run_id: snapshot()[run_id]["runStatus"] == "completed", 10)
                assert_answer(accepted, TENANT_A)
                physical = [
                    row
                    for row in records(audit)
                    if row["kind"] == "physical"
                    and row["runId"] == run_id
                    and row["state"] == "started"
                ]
                assert Counter(row["step"] for row in physical) == {"sum": 1, "double": 1}
                with Session(engine) as tx:
                    attempts = tx.scalars(
                        select(AgentAttempt).where(AgentAttempt.run_id == UUID(run_id))
                    ).all()
                    assert len(attempts) == 2 and all(a.status == "completed" for a in attempts)
                    assert first_attempt_id in {str(value.id) for value in attempts}
                report["recoveries"].append(
                    {
                        "runId": run_id,
                        "killedPid": victim.pid,
                        "oldGrant": old_grant,
                        "newGrant": next_claim["grant"],
                        "lastHeartbeat": heartbeat["time"],
                        "lastHeartbeatToClaimSeconds": delay,
                        "killToClaimSeconds": next_claim["monotonic"] - killed_at,
                        "originalAcceptedAttemptId": first_attempt_id,
                        "physicalCalls": len(physical),
                        "lateAdvances": 0,
                        "observedSuccessor": successor,
                    }
                )
                start_worker(old_grant["workerId"])

            accepted = submit(TENANT_A, "process:unknown")
            unknown_id = accepted["runId"]
            wait_until(lambda: live_event("effect_response_lost", unknown_id), 10)
            heartbeat = wait_until(lambda: live_event("heartbeat", unknown_id), 6)
            victim = workers[heartbeat["grant"]["workerId"]]
            victim.kill()
            victim.wait(3)
            blocked = wait_until(
                lambda: (
                    row if (row := snapshot()[unknown_id])["recoveryStatus"] == "blocked" else None
                ),
                21,
            )
            assert blocked["jobStatus"] == "reclaiming" and blocked["epoch"] is None
            assert blocked["claimCount"] == 1 and blocked["reasonCode"] == "cleanup_unconfirmed"
            start_worker(heartbeat["grant"]["workerId"])
            queued_a = submit(TENANT_A, "process:behind-unknown")
            free_b = submit(TENANT_B, "process:free-capacity")
            wait_until(lambda: snapshot()[free_b["runId"]]["runStatus"] == "completed", 10)
            current = snapshot()
            assert current[queued_a["runId"]]["jobStatus"] == "ready"
            assert current[queued_a["runId"]]["claimCount"] == 0
            assert current[unknown_id]["claimCount"] == 1
            effects = [row for row in records(audit) if row["kind"] == "external_effect_committed"]
            assert len(effects) == 1 and effects[0]["runId"] == unknown_id
            with Session(engine) as tx:
                attempts = tx.scalars(
                    select(AgentAttempt).where(AgentAttempt.run_id == UUID(unknown_id))
                ).all()
                assert len(attempts) == 2
                assert sum(row.status == "completed" for row in attempts) == 1
                unsettled = [row for row in attempts if row.status in {"dispatched", "unknown"}]
                assert len(unsettled) == 1
                assert unsettled[0].output_content is None
                assert unsettled[0].output_sha256 is None
                unsettled_attempt = {
                    "id": str(unsettled[0].id),
                    "status": unsettled[0].status,
                    "output": None,
                }
            usage = client.get(
                "/v1/analysis/runtime/usage",
                headers=auth_headers(
                    owner().model_copy(update={"purpose": "runtime_usage"}), service_token
                ),
            )
            assert usage.status_code == 200, usage.text
            assert usage.json()["occupied"] == 1 and usage.json()["jobs"]["reclaiming"] == 1
            report["unknown"] = {
                "run": current[unknown_id],
                "externalEffectCount": len(effects),
                "unsettledAttempt": unsettled_attempt,
                "blockedFollower": current[queued_a["runId"]],
                "otherTenantCompleted": current[free_b["runId"]],
                "usage": usage.json(),
            }
            report["recoverySummary"] = {
                "required": 4,
                "recovered": 3,
                "blockedUnknown": 1,
                "allSamplesRecoveryRate": 3 / 4,
                "safeSubsetRecoveryRate": 1,
                "seconds": percentile(
                    [r["lastHeartbeatToClaimSeconds"] for r in report["recoveries"]]
                ),
            }
            report["observedUntil"] = datetime.now(UTC).isoformat()
            report["finalRuns"] = list(snapshot().values())
            report["result"] = "passed"
    except BaseException as error:
        report["result"] = "failed"
        report["failureType"] = type(error).__name__
        report["failure"] = str(error)
        raise
    finally:
        report["audit"] = records(audit)
        report.setdefault("observedUntil", datetime.now(UTC).isoformat())
        with Session(engine) as tx:
            timelines = []
            for job, run in tx.execute(select(AgentRunJob, AgentRun).join(AgentRun)).all():
                for obj in run.public_view.get("objects", {}).values():
                    event = obj.get("value", {}).get("event", {})
                    payload = event.get("payload", {})
                    if payload.get("name") == "compass.runtime.scheduling":
                        timelines.append(
                            {
                                "runId": str(run.id),
                                "tenantId": str(job.tenant_id),
                                **payload["data"],
                            }
                        )
        report["timeline"] = timelines
        if "observedFrom" in report:
            start = datetime.fromisoformat(report["observedFrom"])
            end = datetime.fromisoformat(report["observedUntil"])
            total = isolation = 0.0
            for identifier in {row["runId"] for row in timelines}:
                events = sorted(
                    [row for row in timelines if row["runId"] == identifier],
                    key=lambda row: row["occurredAt"],
                )
                for index, row in enumerate(events):
                    if row["status"] not in {"leased", "reclaiming"}:
                        continue
                    since = max(start, datetime.fromisoformat(row["occurredAt"]))
                    until = (
                        min(end, datetime.fromisoformat(events[index + 1]["occurredAt"]))
                        if index + 1 < len(events)
                        else end
                    )
                    duration = max(0, (until - since).total_seconds())
                    total += duration
                    if row["status"] == "reclaiming":
                        isolation += duration
            window = (end - start).total_seconds()
            report["utilization"] = {
                "observationSeconds": window,
                "capacity": 2,
                "denominatorSlotSeconds": window * 2,
                "occupiedSlotSeconds": total,
                "reclaimingSlotSeconds": isolation,
                "utilization": total / (window * 2),
            }
        target = directory / "process-acceptance.json"
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        target.chmod(0o600)
        for process in reversed(children):
            stop_process(process)
        for output in streams:
            output.close()
