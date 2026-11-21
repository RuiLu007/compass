"""Submit frozen L22 questions to a local, authenticated Chat and retain its public receipts."""

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

import httpx
from effect_cohort import samples

DONE = {
    "completed",
    "failed",
    "cancelled",
    "canceled",
    "waiting",
    "interrupted",
    "reconciliation_required",
}


def write(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    tmp.replace(path)


class Actor:
    def __init__(self, origin, user):
        self.scope = {k: user[k] for k in ("tenantId", "workspaceId")}
        self.http = httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=45)
        deadline = time.monotonic() + 120
        while True:
            signed = self.http.post(
                "/api/web/v1/auth/login",
                json={"loginName": user["handle"], "password": os.environ["WEB_TEST_PASSWORD"]},
            )
            if signed.status_code != 429 or time.monotonic() >= deadline:
                signed.raise_for_status()
                break
            time.sleep(float(signed.headers.get("Retry-After", "5")))
        session = self.http.get(
            "/api/web/v1/session", params={**self.scope, "pageId": "data-metrics"}
        )
        session.raise_for_status()
        self.http.headers["X-CSRF-Token"] = session.json()["csrfToken"]

    def submit(self, request_id, question, provider):
        response = self.http.post(
            "/api/web/v1/analysis/sessions/submit",
            json={
                **self.scope,
                "requestId": request_id,
                "question": question,
                "selection": {"mode": "fixed", "provider_id": str(provider)},
            },
        )
        response.raise_for_status()
        return response.json()

    def messages(self, receipt):
        run_id = receipt["runId"]
        response = self.http.get(
            f"/api/web/v1/analysis/sessions/{receipt['sessionId']}/messages",
            params={**self.scope, "runId": run_id},
        )
        response.raise_for_status()
        packets = [p for p in response.json()["packets"] if p["runId"] == run_id]
        states = [p["event"]["payload"] for p in packets if p["event"]["type"] == "run.upsert"]
        return (states[-1] if states else {}), packets

    def status(self, receipt):
        response = self.http.get(
            f"/api/web/v1/analysis/sessions/{receipt['sessionId']}", params=self.scope
        )
        response.raise_for_status()
        item = response.json()
        if item["latestRunId"] != receipt["runId"]:
            raise ValueError("the isolated evaluation session acquired another run")
        return item["executionStatus"]

    def wait(self, receipt, packets):
        for packet in packets:
            payload = packet["event"]["payload"]
            if payload.get("kind") == "approval" and payload["value"].get("status") == "pending":
                wait_id = payload["value"]["waitId"]
                response = self.http.get(
                    f"/api/web/v1/analysis/runs/{receipt['runId']}/waits/{wait_id}",
                    params=self.scope,
                )
                response.raise_for_status()
                return response.json()
        return None

    def artifacts(self, receipt, packets):
        files = []
        for packet in packets:
            record = packet["event"]["payload"]
            if record.get("kind") != "artifact":
                continue
            item = record["value"]
            if item["mediaType"] not in {"text/markdown", "text/csv", "application/json"}:
                continue
            response = self.http.get(
                f"/api/web/v1/analysis/runs/{receipt['runId']}/artifacts/{item['id']}",
                params=self.scope,
            )
            if response.status_code in {403, 404}:
                files.append({"id": item["id"], "status": response.status_code})
                continue
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != item["sha256"]:
                raise ValueError("downloaded artifact differs from its approved receipt")
            files.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "status": 200,
                    "text": response.content.decode("utf-8-sig"),
                }
            )
        return files


def capture_public_state(actor, data):
    state, packets = actor.messages(data["receipt"])
    data.update(state=state, packets=packets)
    status = state.get("status")
    if status not in DONE:
        return False
    if status == "waiting":
        data["wait"] = actor.wait(data["receipt"], packets)
    elif status == "completed":
        data["artifacts"] = actor.artifacts(data["receipt"], packets)
    data["terminal"] = status
    return True


def run(case, actor, provider, directory, timeout):
    path = directory / (case["id"] + ".json")
    data = (
        json.loads(path.read_text())
        if path.exists()
        else {
            "case": case["id"],
            "question": case["question"],
            "persona": case["persona"],
            "requestId": str(uuid4()),
        }
    )
    if (data["question"], data["persona"]) != (case["question"], case["persona"]):
        raise ValueError(f"{path} belongs to a different question or identity")
    if data.get("terminal") in DONE or data.get("observation") == "past_run_deadline":
        return data["terminal"]
    write(path, data)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if "receipt" not in data:
                data["receipt"] = actor.submit(data["requestId"], case["question"], provider)
                write(path, data)
            if actor.status(data["receipt"]) in DONE and capture_public_state(actor, data):
                break
        except (httpx.NetworkError, httpx.TimeoutException):
            time.sleep(2)
            continue
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(4)
                continue
            data.update(
                terminal="error", detail=f"{exc.response.status_code} {exc.request.url.path}"
            )
            break
        time.sleep(4)
    else:
        data.update(terminal="timeout")
        if "receipt" in data:
            try:
                capture_public_state(actor, data)
            except httpx.HTTPError as error:
                data["snapshot_error"] = type(error).__name__
    write(path, data)
    return data["terminal"]


def close_captured_wait(actor, path):
    data = json.loads(path.read_text())
    card = data["wait"]
    if data.get("after_capture") or card.get("semanticKind") != "choice":
        return
    wait_id = UUID(card["waitId"])
    response = actor.http.post(
        "/api/web/v1/analysis/runs/" + data["receipt"]["runId"] + "/decisions",
        json={
            **actor.scope,
            **{
                key: card[key]
                for key in ("waitId", "proposalId", "proposalRevision", "proposalDigest")
            },
            "expectedVersion": card["runVersion"],
            "requestId": str(uuid5(wait_id, "close-evaluation-wait")),
            "decisionId": str(uuid5(wait_id, "owner-declines")),
            "decision": "reject",
        },
    )
    response.raise_for_status()
    data["after_capture"] = "owner_declined"
    write(path, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument(
        "personas", type=Path, help="JSON of test users: handle, tenantId, workspaceId"
    )
    parser.add_argument("provider", type=UUID, help="enabled model provider in the test Agent")
    parser.add_argument(
        "output", type=Path, help="private local directory for user-readable snapshots"
    )
    parser.add_argument("--group", choices=("identification", "ambiguity", "complex"))
    parser.add_argument(
        "--cases",
        nargs="+",
        help="run only the specified frozen case IDs, e.g. C002 C012",
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--close-captured-waits",
        action="store_true",
        help="after capturing a choice card, let its test owner decline to free queue capacity",
    )
    args = parser.parse_args()
    if urlsplit(args.origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("the local acceptance server is required")
    if not urlsplit(os.environ["WEB_TEST_DATABASE_URL"]).path.startswith("/compass_web_test_"):
        parser.error("a dedicated Web test database is required")
    if not 1 <= args.workers <= 16 or args.timeout <= 0:
        parser.error("use 1–16 workers and a positive timeout")
    args.output.mkdir(parents=True, exist_ok=True)
    cohort = [c for c in samples() if not args.group or c["stratum"] == args.group]
    if args.cases:
        unknown = sorted(set(args.cases) - {c["id"] for c in cohort})
        if unknown:
            parser.error("unknown case IDs in the selected group: " + ", ".join(unknown))
        cohort = [c for c in cohort if c["id"] in args.cases]
    roster = json.loads(args.personas.read_text())
    actors = {name: Actor(args.origin, roster[name]) for name in {c["persona"] for c in cohort}}

    def process(case):
        actor = actors[case["persona"]]
        state = run(case, actor, args.provider, args.output, args.timeout)
        if state == "waiting" and args.close_captured_waits:
            close_captured_wait(actor, args.output / (case["id"] + ".json"))
        print(case["id"], state, flush=True)
        return state

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            states = list(pool.map(process, cohort))
    finally:
        for actor in actors.values():
            actor.http.close()
    return int(any(s in {"error", "timeout"} for s in states))


if __name__ == "__main__":
    raise SystemExit(main())
