"""Every classroom scenario runs the actual executor, SDK and output validators."""

import asyncio
from uuid import uuid4

import pytest
from compass_agent.reliability.service import CATALOG, run_showcase
from compass_agent.schemas import ShowcaseInput

from .test_models import MANAGE


@pytest.mark.parametrize("scenario", [s.id for s in CATALOG.items])
def test_showcase_scenario(scenario):
    async def run():
        events = []

        async def emit(event):
            events.append(event)

        report = await run_showcase(ShowcaseInput(call_id=uuid4(), scenario=scenario), MANAGE, emit)
        assert report.passed, report.model_dump()
        assert report.simulation
        assert events
        assert all(e.call_id == report.call_id for e in events)

    asyncio.run(run())


def test_showcase_http_requires_platform_admin_and_rejects_arbitrary_input():
    from unittest.mock import patch

    from compass_agent.main import app
    from compass_agent.settings import Settings
    from fastapi.testclient import TestClient

    from .test_models import CHAT, headers

    settings = Settings(compass_agent_service_token="unit-service-token")
    with (
        patch("compass_agent.model_api.get_settings", return_value=settings),
        TestClient(app) as client,
    ):
        assert client.get("/v1/models/reliability/scenarios").status_code == 401
        assert (
            client.get("/v1/models/reliability/scenarios", headers=headers(CHAT)).status_code == 403
        )
        assert client.get("/v1/models/reliability/scenarios", headers=headers()).status_code == 200
        invalid = client.post(
            "/v1/models/reliability/runs",
            headers=headers(),
            json={
                "call_id": str(uuid4()),
                "scenario": "success",
                "base_url": "https://untrusted.invalid",
            },
        )
        assert invalid.status_code == 422
        response = client.post(
            "/v1/models/reliability/runs",
            headers=headers(),
            json={"call_id": str(uuid4()), "scenario": "rate_limit"},
        )
        assert response.status_code == 200
        import json

        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events[0]["type"] == "start" and events[-1]["type"] == "final"
        assert events[-1]["passed"] and events[-1]["simulation"]
        assert not app.state.showcase_running


def test_cancelling_showcase_owns_and_settles_all_children():
    async def run():
        entered = asyncio.Event()
        existing = asyncio.all_tasks()

        async def emit(event):
            if "供应商收到" in event.message:
                entered.set()

        task = asyncio.create_task(
            run_showcase(ShowcaseInput(call_id=uuid4(), scenario="timeout"), MANAGE, emit)
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert not [t for t in asyncio.all_tasks() - existing if not t.done()]

    asyncio.run(run())
