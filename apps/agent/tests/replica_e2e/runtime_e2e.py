"""Ensure real Agent replicas can resume a Run and Scheduler can deliver one due plan."""

import base64
import json
import os
import signal
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from runtime_e2e_env import ROOT, IsolatedRuntime, wait_until

SUBMIT = "/v1/analysis/sessions/submit"
RUNS = "/v1/analysis/runs"


def body(response: httpx.Response, status: int) -> Any:
    assert response.status_code == status, (response.status_code, response.text)
    return response.json()


class BusinessAPI:
    def __init__(self, runtime, client):
        self.runtime, self.client = runtime, client
        self.actor_a, self.actor_b = str(uuid4()), str(uuid4())
        self.tenants = {
            actor: (str(uuid4()), str(uuid4())) for actor in (self.actor_a, self.actor_b)
        }

    def headers(self, actor, purpose="chat"):
        if purpose == "manage":
            scope = {"kind": "platform"}
        else:
            tenant, workspace = self.tenants[actor]
            scope = {"kind": "tenant", "tenant_id": tenant, "workspace_id": workspace}
        context = json.dumps({"actor_user_id": actor, "scope": scope, "purpose": purpose})
        encoded = base64.urlsafe_b64encode(context.encode()).decode().rstrip("=")
        return {"Authorization": "Bearer " + self.runtime.token, "X-Compass-Context": encoded}

    def provider(self, url):
        result = self.client.post(
            url + "/v1/models/providers",
            headers=self.headers(self.actor_a, "manage"),
            json={
                "operation_id": str(uuid4()),
                "catalog_id": "deepseek/deepseek-v4-pro",
                "api_model": "deepseek-v4-pro",
                "alias": "Lesson 20 CI",
                "base_url": "https://api.deepseek.com",
                "api_key": "local-fixture-only",
                "enabled": True,
                "chat_visible": True,
            },
        )
        return {"mode": "fixed", "provider_id": body(result, 201)["provider_id"]}

    def submit(self, url, actor, selection):
        return body(
            self.client.post(
                url + SUBMIT,
                headers=self.headers(actor),
                json={
                    "requestId": str(uuid4()),
                    "question": "lesson20-ci:hold",
                    "selection": selection,
                },
            ),
            202,
        )

    def query(self, statement, *parameters) -> Any:
        with (
            psycopg.connect(self.runtime.owner) as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            cursor.execute(statement, parameters)
            return cursor.fetchone() if cursor.description else None

    def run(self, identifier):
        return self.query(
            """
            SELECT r.id, r.session_id, r.status, r.version, r.execution_epoch, r.checkpoint_no,
                   r.request_id, j.claim_count, j.worker_boot_id, j.status AS job_status
            FROM agent_run r JOIN agent_run_job j ON j.run_id=r.id WHERE r.id=%s
        """,
            identifier,
        )

    def status(self, identifier, status, job, *, timeout=20):
        def reached():
            row = self.run(identifier)
            return row if (row["status"], row["job_status"]) == (status, job) else None

        return wait_until(reached, f"{identifier} did not reach {status}/{job}", timeout=timeout)

    def audit(self, identifier, kind):
        ledger = self.runtime.directory / "execution.jsonl"
        if not ledger.exists():
            return []
        values = [json.loads(line) for line in ledger.read_text().splitlines()]
        return [row for row in values if row["runId"] == identifier and row["kind"] == kind]

    def held(self, identifier, process):
        wait_until(
            lambda: any(row["pid"] == process.pid for row in self.audit(identifier, "held")),
            f"Run {identifier} did not reach the controlled boundary on PID {process.pid}",
            timeout=32,
        )

    def events(self, url, identifier, *, after="0-0", first=False):
        output = []
        with self.client.stream(
            "GET",
            url + f"{RUNS}/{identifier}/events",
            params={"after": after},
            headers=self.headers(self.actor_a),
            timeout=22,
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data: "):
                    output.append(json.loads(line.removeprefix("data: ")))
                    if first:
                        break
        return output

    def result(self, url, session):
        def completed():
            document = body(
                self.client.get(
                    url + f"/v1/analysis/sessions/{session}/messages",
                    headers=self.headers(self.actor_a),
                ),
                200,
            )
            answers = [
                row["event"]["payload"]
                for row in document["packets"]
                if row["event"]["type"] == "message.upsert"
                and row["event"]["payload"]["role"] == "assistant"
            ]
            return answers if any(item["status"] == "completed" for item in answers) else None

        values = wait_until(completed, f"Run result absent for Session {session}")
        assert [item["content"]["text"] for item in values] == ["验收汇总金额 380 分。"]


def ready(client, url, path):
    wait_until(
        lambda: client.get(url + path).status_code == 200,
        f"{path} unavailable on {url}",
        timeout=45,
        transient=(httpx.HTTPError,),
    )


def test_runtime_replica_and_scheduler_delivery():
    output = Path(os.environ.get("COMPASS_ACCEPTANCE_RESULTS", ROOT / "work/ci/lesson20-runtime"))
    directory = output / uuid4().hex[:8]
    directory.mkdir(parents=True)
    with ExitStack() as stack, httpx.Client(timeout=7, trust_env=False) as client:
        runtime = IsolatedRuntime(stack, directory)
        authz, _ = runtime.http("authz", "runtime_e2e_host:support")
        ready(client, authz, "/openapi.json")
        settings = {"COMPASS_WEB_AUTHZ_URL": f"{authz}/authorize"}
        a, agent_a = runtime.http(
            "agent-a", "runtime_e2e_host:create_agent", factory=True, **settings
        )
        ready(client, a, "/readyz")
        api = BusinessAPI(runtime, client)
        model = api.provider(a)
        preview = body(
            client.post(
                a + "/v1/analysis/schedules/preview",
                headers=api.headers(api.actor_a, "schedule_manage"),
                json={
                    "cron": "0 9 * * 1",
                    "timezone": "Asia/Shanghai",
                    "selection": {"mode": "auto"},
                },
            ),
            200,
        )
        assert preview["autoRouting"] == {"flash": False, "pro": True}
        assert len(preview["times"]) == 5
        mismatched, _ = runtime.http(
            "mismatched",
            "runtime_e2e_host:create_agent",
            factory=True,
            COMPASS_ROUTING_PRO_PROVIDER_ID=str(uuid4()),
            **settings,
        )
        ready(client, mismatched, "/healthz")
        assert client.get(mismatched + "/readyz").status_code == 503
        first = api.submit(a, api.actor_a, model)
        run_a = first["runId"]
        api.held(run_a, agent_a)
        before = api.status(run_a, "running", "leased")
        assert before["checkpoint_no"] == 1
        second = api.submit(a, api.actor_b, model)
        run_b = second["runId"]
        assert api.status(run_b, "created", "ready")["claim_count"] == 0

        b, agent_b = runtime.http(
            "agent-b", "runtime_e2e_host:create_agent", factory=True, **settings
        )
        ready(client, b, "/readyz")
        api.held(run_b, agent_b)
        other = api.status(run_b, "running", "leased")
        assert other["worker_boot_id"] != before["worker_boot_id"]
        assert api.run(run_a)["execution_epoch"] == before["execution_epoch"]
        cursor = api.events(a, run_a, first=True)[-1]["cursors"][0]["seq"]

        cancel = client.post(
            a + f"{RUNS}/{run_b}/cancel", headers=api.headers(api.actor_b), json={}
        )
        body(cancel, 202)
        api.status(run_b, "canceled", "done", timeout=8)
        assert not api.audit(run_b, "finished")
        agent_a.kill()
        assert agent_a.wait(5) == -signal.SIGKILL
        api.held(run_a, agent_b)
        resumed = api.status(run_a, "running", "leased")
        assert resumed["checkpoint_no"] == before["checkpoint_no"]
        assert resumed["execution_epoch"] != before["execution_epoch"]
        assert resumed["claim_count"] == 2
        (directory / f"release-{run_a}").touch()
        api.status(run_a, "completed", "done")
        assert len(api.audit(run_a, "calculated")) == 1
        api.result(b, first["sessionId"])
        assert api.events(b, run_a, after=cursor)

        plan = body(
            client.post(
                b + "/v1/analysis/schedules",
                headers=api.headers(api.actor_a, "schedule_manage"),
                json={
                    "requestId": str(uuid4()),
                    "name": "CI scheduled delivery",
                    "question": "lesson20-ci:timed",
                    "selection": model,
                    "cron": "0 0 1 1 *",
                    "timezone": "UTC",
                    "enabled": True,
                },
            ),
            201,
        )
        api.query(
            "UPDATE agent_schedule SET next_fire_at=date_trunc('year', clock_timestamp()) "
            "WHERE id=%s",
            plan["id"],
        )
        runtime.spawn("scheduler", "compass_agent.scheduler.main", COMPASS_SCHEDULER_AGENT_URL=b)
        wait_until(
            lambda: api.query(
                "SELECT holder_id FROM agent_runtime_control "
                "WHERE key='scheduler' AND expires_at > clock_timestamp()"
            ),
            "Scheduler could not acquire its lease",
            timeout=45,
        )
        trigger = wait_until(
            lambda: api.query(
                "SELECT id,run_id FROM agent_schedule_trigger "
                "WHERE schedule_id=%s AND delivery_status='submitted'",
                plan["id"],
            ),
            "Scheduler did not submit the due plan",
            timeout=45,
        )
        terminal = api.status(str(trigger["run_id"]), "completed", "done")
        assert terminal["request_id"] == trigger["id"]
        assert api.query(
            "SELECT count(*) AS total FROM agent_run WHERE request_id=%s", trigger["id"]
        ) == {"total": 1}
        api.result(b, terminal["session_id"])
        scheduler_log = directory / "scheduler.log"
        wait_until(
            lambda: all(
                field in scheduler_log.read_text()
                for field in ("discovery_ms=", "admission_ms=", "oldest_ms=", "sync_ms=")
            ),
            "Scheduler did not record detection, admission, backlog and synchronization metrics",
        )
