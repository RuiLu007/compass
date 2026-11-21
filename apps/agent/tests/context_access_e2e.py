"""Recheck retained deliveries after upstream row and column permission changes."""

import json
import os
import time
from hashlib import sha256
from uuid import uuid4

import httpx
import psycopg
from sqlalchemy import create_engine, text

SCOPE = {
    "tenantId": "99500000-0000-4000-8000-000000000010",
    "workspaceId": "99500000-0000-4000-8000-000000000020",
}
BASE = "/api/web/v1/analysis"
DENIED = "这份结果的来源当前不可读取"


def exercise():
    engine = create_engine(os.environ["DATABASE_URL"])
    origin = f"http://127.0.0.1:{int(os.environ['WEB_TEST_PORT'])}"
    with engine.connect() as agent:
        files = agent.execute(
            text("""
            SELECT DISTINCT ON (a.media_type) a.media_type,a.run_id,a.id,r.session_id,a.sha256
            FROM agent_artifact a JOIN agent_run r ON r.id=a.run_id
            JOIN agent_session s ON s.id=r.session_id
            WHERE s.workspace_id=:workspace AND a.media_type IN ('text/markdown','application/json')
            AND (a.media_type='application/json' OR
                (a.scope->'source_refs'->0->'columns'->'core.channel_product_daily') ? 'cost')
            ORDER BY a.media_type,a.created_at DESC
        """),
            {"workspace": SCOPE["workspaceId"]},
        ).all()
    engine.dispose()
    assert len(files) == 2, "create a dashboard and a report using the retail fixture first"

    with (
        httpx.Client(base_url=origin, timeout=20, headers={"origin": origin}) as web,
        psycopg.connect(
            host="127.0.0.1",
            port=int(os.environ["CONTEXT_RETAIL_TEST_PORT"]),
            dbname="compass_retail_test_975",
            user="compass_test",
            autocommit=True,
        ) as retail,
    ):
        login = web.post(
            "/api/web/v1/auth/login",
            json={"loginName": "teaching.admin", "password": os.environ["WEB_TEST_PASSWORD"]},
        )
        login.raise_for_status()

        def read(file, *, denied):
            _, run, artifact, session, digest = file
            response = web.get(f"{BASE}/runs/{run}/artifacts/{artifact}", params=SCOPE)
            assert response.status_code == (403 if denied else 200), (response.status_code, denied)
            if not denied:
                assert sha256(response.content).hexdigest() == digest
            snapshot = web.get(
                f"{BASE}/sessions/{session}/messages", params={**SCOPE, "runId": str(run)}
            )
            snapshot.raise_for_status()
            events = [p["event"] for p in snapshot.json()["packets"]]
            answer = next(
                e["payload"]["content"]
                for e in events
                if e["type"] == "message.upsert" and e["payload"]["role"] == "assistant"
            )
            assert (DENIED in answer["text"]) == denied
            assert bool(answer["artifactIds"]) != denied
            assert (
                any(
                    e["type"] == "view.upsert" and e["payload"]["kind"] == "artifact"
                    for e in events
                )
                != denied
            )
            stream = web.get(f"{BASE}/runs/{run}/events", params={**SCOPE, "after": "0-0"})
            stream.raise_for_status()
            frames = [json.loads(s[6:]) for s in stream.text.splitlines() if s.startswith("data: ")]
            packets = [p for frame in frames for p in frame["packets"]]
            assert packets and all(
                f["cursors"][0]["seq"] == f["packets"][-1]["seq"] for f in frames
            )
            assert (DENIED in stream.text) == denied
            assert ("区域1为120" in stream.text) != denied
            assert (str(artifact) in stream.text) != denied

        dashboard, report = files
        for file in files:
            read(file, denied=False)
        try:
            assert (
                retail.execute(
                    "DELETE FROM sec.grants WHERE principal='compass_l21_reader' "
                    "AND tenant_id=101 AND region_id=2"
                ).rowcount
                == 1
            )
            for file in files:
                read(file, denied=True)
            csrf = web.get("/api/web/v1/session", params={"pageId": "analysis-new", **SCOPE})
            csrf.raise_for_status()
            command = {
                **SCOPE,
                "sessionId": str(dashboard.session_id),
                "requestId": str(uuid4()),
                "selection": {"mode": "auto", "provider_id": None},
                "question": "经营问答：请重新查询目前可读区域的销售额。",
            }
            accepted = web.post(
                f"{BASE}/sessions/submit",
                json=command,
                headers={"x-csrf-token": csrf.json()["csrfToken"]},
            )
            accepted.raise_for_status()
            follow = accepted.json()["runId"]
            for _ in range(100):
                response = web.get(
                    f"{BASE}/sessions/{dashboard.session_id}/messages",
                    params={**SCOPE, "runId": follow},
                )
                response.raise_for_status()
                events = [p["event"] for p in response.json()["packets"] if p["runId"] == follow]
                if any(
                    e["type"] == "run.upsert" and e["payload"]["status"] == "completed"
                    for e in events
                ):
                    answer = next(
                        e["payload"]["content"]["text"]
                        for e in events
                        if e["type"] == "message.upsert" and e["payload"]["role"] == "assistant"
                    )
                    assert "120" in answer and "200" not in answer and "80" not in answer, answer
                    break
                time.sleep(0.4)
            else:
                raise AssertionError("follow-up did not complete")
        finally:
            retail.execute(
                "INSERT INTO sec.grants VALUES('compass_l21_reader',101,2) ON CONFLICT DO NOTHING"
            )
        try:
            retail.execute(
                "REVOKE SELECT(cost) ON core.channel_product_daily FROM compass_l21_reader"
            )
            read(report, denied=True)
            read(dashboard, denied=False)
        finally:
            retail.execute("GRANT SELECT(cost) ON core.channel_product_daily TO compass_l21_reader")
        for file in files:
            read(file, denied=False)
    print("Row and column revocation, follow-up, history, SSE and original downloads passed.")


if __name__ == "__main__":
    exercise()
