import json
from datetime import datetime

from compass_agent.context_runtime.run_facts import facts_for
from compass_agent.schemas import ChatMessage

from .test_models import CHAT, MANAGE


def test_run_clock_and_organization_at_local_midnight():
    accepted = datetime.fromisoformat("2026-11-21T16:02:03+00:00")
    environment = {
        "tenantName": "甲公司\n显示名称",
        "workspaceName": "北区",
        "timezone": "Asia/Shanghai",
    }
    source = {"knownAt": "2025-10-09T01:00:00Z", "timezone": "America/New_York"}
    facts = facts_for(CHAT, accepted, environment, source)
    draft = [
        ChatMessage(role="system", content="默认规则"),
        ChatMessage(role="user", content="上周呢？"),
    ]
    messages = facts.messages(draft)
    system = messages[0].content or ""
    assert facts.local_date.isoformat() == "2026-11-22"
    assert "2026-11-22T00:02:03+08:00（Asia/Shanghai，星期日）" in system
    assert "资料可见截止：2025-10-09T09:00:00+08:00" in system
    assert CHAT.scope.kind == "tenant"
    assert str(CHAT.scope.tenant_id) in system and str(CHAT.scope.workspace_id) in system
    assert str(CHAT.actor_user_id) not in system and environment["tenantName"] not in system
    assert messages[1].role == "user" and json.loads(messages[1].content or "{}") == {
        "source": "runOrganization",
        "tenantName": environment["tenantName"],
        "workspaceName": "北区",
    }
    assert messages[-1] == draft[-1] and facts.messages(messages) == messages
    previous = facts_for(CHAT, datetime.fromisoformat("2026-11-21T15:59:59Z"), environment)
    assert previous.local_date.isoformat() == "2026-11-21"


def test_source_zone_fallback_and_platform_scope():
    accepted = datetime.fromisoformat("2026-11-21T01:00:00Z")
    facts = facts_for(
        MANAGE,
        accepted,
        {"timezone": "Invalid/Zone"},
        {
            "knownAt": "2027-11-23T00:00:00Z",
            "timezone": "America/New_York",
        },
    )
    assert "2026-11-20T20:00:00-05:00" in facts.instructions and "2027" not in facts.instructions
    assert "租户 ID" not in facts.instructions and facts.organization is None
    assert "UTC" in facts_for(CHAT, accepted, {}).instructions
