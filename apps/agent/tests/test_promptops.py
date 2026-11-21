"""Verify publication, read isolation and import/edit races through the actual Agent routes."""

import base64
from typing import Literal
from uuid import uuid4

import pytest
from compass_agent.api import promptops
from compass_agent.database import Base
from compass_agent.main import app
from compass_agent.promptops import catalog, starter_catalog
from compass_agent.promptops.entities import PROMPT_TABLES, PromptHead
from compass_agent.promptops.errors import PromptError
from compass_agent.promptops.reader import PromptReader
from compass_agent.schemas import PlatformScope, TrustedContext
from compass_agent.settings import get_settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from .test_models import CHAT

FINANCE = "tasks/business_report/scenes/weekly/finance.md"
REPORT = "tasks/business_report/scenes/weekly/report.md"


def headers(actor, purpose: Literal["promptops_manage", "manage"] = "promptops_manage"):
    context = TrustedContext(
        actor_user_id=actor, purpose=purpose, scope=PlatformScope(kind="platform")
    )
    encoded = base64.urlsafe_b64encode(context.model_dump_json().encode()).decode()
    return {"Authorization": "Bearer promptops-test-credential", "X-Compass-Context": encoded}


@pytest.fixture
def system(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    tables = [Base.metadata.tables[name] for name in PROMPT_TABLES]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as tx, tx.begin():
        tx.add(PromptHead(id=1, generation=None, last_snapshot_no=0))
    monkeypatch.setattr(promptops, "get_engine", lambda: engine)
    monkeypatch.setattr(get_settings(), "compass_agent_service_token", "promptops-test-credential")
    client = TestClient(app, base_url="http://testserver/v1/promptops/")
    client.headers.update(headers(uuid4()))
    yield client, engine
    client.close()
    engine.dispose()


def post(client, route, **data):
    value = client.post(route, json=data)
    assert value.status_code == 200, value.text[:900]
    return value.json()


def publish(client, action, **data):
    preview = post(client, "release-preview", action=action, **data)
    assert preview["errors"] == []
    cmd = dict(
        action=action,
        **data,
        confirmation_hash=preview["confirmation_hash"],
        note="已核对",
        request_id=str(uuid4()),
    )
    first = post(client, "releases", **cmd)
    assert post(client, "releases", **cmd) == first
    return first


def save(client, path, body):
    detail = client.get("file", params={"path": path}).json()
    return post(
        client,
        "file-draft",
        path=path,
        body=body,
        expected_edit_rev=detail["edit_rev"],
        base_epoch=detail["active_epoch"],
        request_id=str(uuid4()),
    )


def import_bundled(client):
    preview = post(client, "import-preview")
    return post(
        client,
        "import",
        request_id=str(uuid4()),
        package_digest=preview["package_digest"],
        confirmation_hash=preview["confirmation_hash"],
    )


def first_release(client):
    assert import_bundled(client)["adopted"] == len(catalog.bundled())
    return publish(client, "all")


def test_grouped_showcases_follow_publication_and_snapshot_restore(system):
    import json

    client, engine = system
    path = "support/starters/analysis.json"
    first = first_release(client)

    def groups():
        with Session(engine) as tx:
            cards = starter_catalog.starter_cards(tx, CHAT)
            return {g.title: [s.question for s in g.suggestions] for g in cards.analysis}

    original = groups()
    body = json.loads(catalog.bundled()[path])
    body["questions"]["pos-first-report-brief"] = "在线调整后的经营报告问题"
    save(client, path, json.dumps(body, ensure_ascii=False))
    assert groups() == original
    published = publish(client, "file", path=path)
    assert "在线调整后的经营报告问题" in str(groups())
    assert client.get("file", params={"path": path}).json()["kind"] == "json"
    publish(client, "restore", snapshot_no=1)
    assert groups() == original
    with Session(engine) as tx:
        assert (
            PromptReader.load_exact(tx, first["generation"], [path])[0].body
            == catalog.bundled()[path]
        )
        assert (
            "在线调整后的经营报告问题"
            in PromptReader.load_exact(tx, published["generation"], [path])[0].body
        )


def test_published_generations_scope_template_and_restore(system):
    client, engine = system
    with Session(engine) as tx, pytest.raises(PromptError, match="baseline_unavailable"):
        PromptReader.pin_current(tx)
    initial = first_release(client)
    assert initial["snapshot_no"] == 1
    all_files = client.get("tree", params={"limit": 200}).json()
    assert sorted(catalog.bundled()) == [r["path"] for r in all_files["items"]]
    assert client.get("tree", params={"q": "周报"}).json()["items"]
    for path in (FINANCE, REPORT):
        save(client, path, "该期报表使用 {{period}}，单位为 {{unit}}。")
    second = publish(client, "file", path=FINANCE)
    assert second["version"] == 2 and second["snapshot_no"] is None
    assert client.get("file", params={"path": REPORT}).json()["has_draft"] is True
    third = publish(client, "all")
    restored = publish(client, "restore", snapshot_no=1)
    assert [third["snapshot_no"], restored["snapshot_no"]] == [2, 3]
    with Session(engine) as tx:
        assert PromptReader.pin_current(tx) == restored["generation"]
        latest = PromptReader.load_exact(tx, restored["generation"], [FINANCE, REPORT])
        assert [r.version for r in latest] == [1, 1]
        assert [
            r.version
            for r in PromptReader.load_exact(tx, second["generation"], [REPORT, FINANCE, FINANCE])
        ] == [1, 2, 2]
        assert [
            r.version for r in PromptReader.load_exact(tx, third["generation"], [FINANCE, REPORT])
        ] == [2, 2]
    assert (
        client.get("file", params={"path": FINANCE, "version": 2})
        .json()["body"]
        .startswith("该期报表")
    )
    assert client.get("snapshot", params={"snapshot_no": 2}).json()["snapshot_no"] == 2
    assert (
        post(
            client,
            "render-preview",
            path=FINANCE,
            body="{{period}}",
            variables={"period": "{{literal}}"},
        )["rendered"]
        == "{{literal}}"
    )


def test_optimistic_save_isolated_file_publication_and_atomic_invalid_draft(system):
    client, _ = system
    first_release(client)
    saved = save(client, FINANCE, "财务版")
    stale = client.post(
        "file-draft",
        json={
            "path": FINANCE,
            "body": "过期改动",
            "base_epoch": 1,
            "expected_edit_rev": saved["edit_rev"] - 1,
            "request_id": str(uuid4()),
        },
    )
    assert stale.status_code == 409
    preview = post(client, "release-preview", action="file", path=FINANCE)
    save(client, REPORT, "独立文件")
    publish(client, "file", path=REPORT)
    cmd = dict(
        action="file",
        path=FINANCE,
        confirmation_hash=preview["confirmation_hash"],
        request_id=str(uuid4()),
        note="定向发布",
    )
    result = post(client, "releases", **cmd)
    assert result["version"] == 2
    assert client.post("releases", json={**cmd, "note": "改变命令"}).status_code == 409
    save(client, REPORT, "不完整 {{ ")
    save(client, FINANCE, "新草稿")
    batch = post(client, "release-preview", action="all")
    assert batch["errors"]
    failed = client.post(
        "releases",
        json={
            "action": "all",
            "confirmation_hash": batch["confirmation_hash"],
            "request_id": str(uuid4()),
            "note": "整体发布",
        },
    )
    assert failed.status_code == 422
    assert client.get("tree").json()["generation"] == result["generation"]
    assert client.get("file", params={"path": FINANCE}).json()["draft_body"] == "新草稿"


def test_strict_admin_boundary_and_conflicting_import(system, monkeypatch):
    client, _ = system
    assert client.get("tree", headers=headers(uuid4(), "manage")).status_code == 403
    assert client.get("tree", headers={"Authorization": "Bearer untrusted"}).status_code == 401
    first_release(client)
    save(client, REPORT, "在线编辑")
    source = dict(catalog.bundled())
    source[REPORT] = "包内新修改"
    monkeypatch.setattr(catalog, "bundled", lambda: source)
    check = post(client, "import-preview", path=REPORT)
    assert (
        check["items"][0]["state"] == "conflict" and check["items"][0]["online_body"] == "在线编辑"
    )
    cmd = {
        "request_id": str(uuid4()),
        "package_digest": check["package_digest"],
        "confirmation_hash": check["confirmation_hash"],
    }
    assert client.post("import", json=cmd).status_code == 409
    post(client, "import", **cmd, choices=[{"path": REPORT, "choice": "online"}])
    assert client.get("file", params={"path": REPORT}).json()["draft_body"] == "在线编辑"
    assert post(client, "import-preview", path=REPORT)["items"][0]["state"] == "online"
    new_path = "自定义/复核.md"
    new = {
        "path": new_path,
        "expected_edit_rev": 0,
        "base_epoch": 0,
        "body": "{{unit}}",
        "request_id": str(uuid4()),
    }
    post(client, "file-draft", **new)
    assert client.get("file", params={"path": new_path}).json()["used"] is False
    publish(client, "file", path=new_path)
    assert (
        client.post(
            "file-draft", json={**new, "path": "../x.md", "request_id": str(uuid4())}
        ).status_code
        == 422
    )
    assert (
        client.get("file", params={"path": new_path}).headers["cache-control"]
        == "private, no-store"
    )
