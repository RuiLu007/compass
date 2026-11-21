"""Public tool data stays bounded and both read paths preserve packet identity."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from compass_agent.models.errors import ModelError
from compass_agent.runtime.durable_service import CurrentAccess
from compass_agent.runtime.public_visibility import public_event, visible_response
from compass_agent.runtime.run_scheduler import ExecutionGrant
from compass_agent.schemas import AgentResponse
from compass_agent.settings import Settings

from .test_agent_run import CHAT


def tool_event():
    return {
        "type": "view.upsert",
        "payload": {
            "objectId": "timeline:tool-1",
            "revision": 3,
            "kind": "timeline",
            "value": {
                "ordinal": 1,
                "event": {
                    "type": "tool.finished",
                    "payload": {
                        "stepId": str(uuid4()),
                        "toolCallId": "tool-1",
                        "toolIndex": 1,
                        "status": "ok",
                        "rowCount": 7,
                        "resultPreview": '{"password":"secret-984","sql":"select private_table"}',
                    },
                },
            },
        },
    }


def test_raw_tool_result_is_removed_before_publication_and_permission_filter_keeps_identity():
    raw = tool_event()
    public = public_event(raw)
    assert "secret-984" not in str(public) and "private_table" not in str(public)
    assert public["payload"]["value"]["event"]["payload"]["rowCount"] == 7
    hidden = public_event(public, details=False)
    assert hidden["payload"]["objectId"] == raw["payload"]["objectId"]
    assert hidden["payload"]["revision"] == 3
    assert "rowCount" not in str(hidden)
    assert hidden["payload"]["value"]["event"]["payload"]["required"] is False
    assert "secret-984" in str(raw)


def test_execution_details_follow_visibility_without_changing_node_identity():
    identifier = str(uuid4())
    raw = {
        "type": "view.upsert",
        "payload": {
            "objectId": identifier,
            "revision": 2,
            "kind": "execution.node",
            "value": {
                "id": identifier,
                "revision": 2,
                "kind": "tool",
                "title": "lookup_sales",
                "status": "completed",
                "startedAtMs": 1000,
                "endedAtMs": 2000,
                "detail": {"input": '{"channel":"POS"}', "output": '{"total":120}'},
            },
        },
    }
    assert public_event(raw)["payload"]["value"]["detail"] == raw["payload"]["value"]["detail"]
    hidden = public_event(raw, details=False)["payload"]
    assert hidden["objectId"] == identifier and hidden["revision"] == 2
    assert hidden["value"]["title"] == "工具调用"
    assert "detail" not in hidden["value"]


def test_delta_permission_filter_keeps_seq_packet_and_cursor():
    run, session, request = (str(uuid4()) for _ in range(3))
    packet = str(uuid4())
    response = AgentResponse.model_validate(
        {
            "protocol": "compass.run",
            "version": 2,
            "sessionId": session,
            "mode": "delta",
            "packets": [
                {
                    "packetId": packet,
                    "runId": run,
                    "seq": "100-1",
                    "requestId": request,
                    "occurredAt": "2026-11-12T00:00:00+00:00",
                    "event": tool_event(),
                }
            ],
            "cursors": [{"runId": run, "seq": "100-1"}],
            "page": None,
        }
    )
    filtered = visible_response(response, details=False)
    assert filtered.packets[0].packet_id == packet
    assert filtered.packets[0].seq == "100-1" and filtered.cursors == response.cursors
    assert "secret-984" not in filtered.model_dump_json()


def test_grant_json_strictly_rejects_extra_authority_but_parses_uuid_and_database_time():
    value = {
        "runId": str(uuid4()),
        "workerId": "one",
        "bootId": str(uuid4()),
        "epoch": str(uuid4()),
        "expiresAt": "2026-11-12T00:00:00Z",
        "checkpointNo": 1,
    }
    grant = ExecutionGrant.model_validate(value)
    assert str(grant.run_id) == value["runId"]
    with pytest.raises(ValueError):
        ExecutionGrant.model_validate({**value, "tenantId": str(uuid4())})


def test_visibility_requires_current_authority_and_closed_shape(monkeypatch):
    access = CurrentAccess(Settings())
    real_client = httpx.AsyncClient

    async def scenario():
        for reply in [
            {"allowed": True},
            {"allowed": True, "visibility": {"details": "yes", "revision": "2"}},
        ]:
            transport = httpx.MockTransport(
                lambda request, reply=reply: httpx.Response(200, json=reply)
            )
            monkeypatch.setattr(
                httpx,
                "AsyncClient",
                lambda transport=transport, **kwargs: real_client(transport=transport, **kwargs),
            )
            with pytest.raises(ModelError, match="authorization_unavailable"):
                await access.visibility(CHAT)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "allowed": True,
                    "visibility": {"details": False, "revision": "3"},
                },
            )
        )
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda transport=transport, **kwargs: real_client(transport=transport, **kwargs),
        )
        assert await access.visibility(CHAT) == {"details": False, "revision": "3"}

    asyncio.run(scenario())


def test_search_projection_keeps_only_progress_metadata_for_authorized_users():
    from compass_agent.runtime.public_visibility import search_event

    raw = {
        "type": "view.upsert",
        "payload": {
            "objectId": "plan",
            "revision": 2,
            "kind": "timeline",
            "value": {
                "ordinal": 1,
                "event": {
                    "type": "stream.extension",
                    "payload": {
                        "name": "compass.autonomy.action",
                        "data": {
                            "planVersion": 1,
                            "selectedTodoId": "t1",
                            "todoId": "t1",
                            "items": [{"goal": "private draft"}],
                        },
                    },
                },
            },
        },
    }
    public = search_event(raw)
    data = public["payload"]["value"]["event"]["payload"]["data"]
    assert data == {"planVersion": 1, "items": [], "todoId": None, "selectedTodoId": None}
    assert "private draft" in str(raw)
    assert search_event(public) == public


@pytest.mark.parametrize("reason", ["blocked", "sufficient", "no_progress", "budget_exhausted"])
def test_search_result_carries_stop_reason_and_physical_tool_allowance(reason):
    from types import SimpleNamespace

    from compass_agent.context_runtime.search_control import SearchProgress
    from compass_agent.harness.autonomy import AutonomyLimits
    from compass_agent.runtime.session import RuntimeSession
    from compass_agent.schemas import RunAutonomyResult

    session = RuntimeSession((), AutonomyLimits(tools=2))
    session.budget.tools = 2
    session.search_progress = SearchProgress({})
    session.search_progress.stop_reason = reason
    event = session.result_event(SimpleNamespace(status="completed"), None)
    status = "budget_exhausted" if reason == "budget_exhausted" else "completed"
    outcome = RunAutonomyResult.model_validate(event.payload.data)
    assert outcome.outcome == status and outcome.reason == reason
    assert outcome.budget.tools.model_dump() == {"limit": 2, "used": 2, "remaining": 0}
