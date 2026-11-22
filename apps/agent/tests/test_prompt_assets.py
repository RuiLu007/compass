"""Structured asset publication, immutable tool contracts and legacy data upgrade."""

import copy
import json
from uuid import uuid4

import pytest
from compass_agent.prompt_assets import factory_text, use_prompts
from compass_agent.promptops import catalog
from compass_agent.promptops.entities import PromptFile, PromptGeneration, PromptHead, PromptVersion
from compass_agent.promptops.layout import LEGACY_POLICY_DIGESTS, MEMBERS, dumps, extract
from compass_agent.promptops.reader import PromptReader
from compass_agent.promptops.structured import schema_structure
from compass_agent.promptops.sync import synchronize
from compass_agent.routing.dictionary import descriptions
from compass_agent.tools.catalog import definitions
from compass_agent.tools.catalog_contracts import CatalogContext
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .test_promptops import first_release, import_bundled, publish, save
from .test_promptops import system as system


def rejected_save(client, path, body):
    detail = client.get("file", params={"path": path}).json()
    response = client.post(
        "file-draft",
        json={
            "path": path,
            "body": dumps(body),
            "expected_edit_rev": detail["edit_rev"],
            "base_epoch": detail["active_epoch"],
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 422, response.text


def reader(tx, generation):
    return lambda path: PromptReader.load_exact(tx, generation, [path])[0].body


def test_tool_description_examples_and_parameter_annotations_reach_the_actual_declaration(system):
    client, engine = system
    initial = first_release(client)
    path = "tools/python_analysis.json"
    original = json.loads(catalog.bundled()[path])
    candidate = copy.deepcopy(original)
    candidate["function"]["description"] = "按当前授权数据执行 Python，显式保存结果。"
    candidate["function"]["parameters"]["properties"]["code"]["description"] = (
        "将分析结果赋给 result"
    )
    candidate["examples"] = [
        {
            "description": "读取查询结果的第一行",
            "arguments": {"source_query_id": "current-query-result", "code": "result = data[0]"},
        }
    ]
    save(client, path, dumps(candidate))
    context = CatalogContext(enabled=frozenset({"python"}), sandbox=True)
    with Session(engine) as tx, use_prompts(reader(tx, initial["generation"])):
        assert definitions(context)[0].function.description == original["function"][
            "description"
        ].rstrip("\n")
    released = publish(client, "file", path=path)
    with Session(engine) as tx, use_prompts(reader(tx, released["generation"])):
        actual = definitions(context)[0].function
        assert "显式保存结果" in actual.description
        assert "current-query-result" in actual.description
        assert actual.parameters["properties"]["code"]["description"] == "将分析结果赋给 result"
        assert schema_structure(actual.parameters) == schema_structure(
            original["function"]["parameters"]
        )
    detail = client.get("file", params={"path": path}).json()
    assert detail["kind"] == "tool" and len(detail["contract_sha256"]) == 64


@pytest.mark.parametrize(
    "change",
    [
        "type",
        "required",
        "unknown",
        "name",
        "invalid_example",
        "description_type",
        "description_limit",
        "combined_limit",
        "schema_bytes",
    ],
)
def test_tool_contract_changes_are_rejected_on_the_server(system, change):
    client, _ = system
    first_release(client)
    path = "tools/python_analysis.json"
    candidate = json.loads(catalog.bundled()[path])
    parameters = candidate["function"]["parameters"]
    if change == "type":
        parameters["properties"]["code"]["type"] = "number"
    elif change == "required":
        parameters["required"] = []
    elif change == "unknown":
        parameters["properties"]["script"] = {"type": "string"}
    elif change == "name":
        candidate["function"]["name"] = "different_executor"
    elif change == "description_type":
        parameters["properties"]["code"]["description"] = {"type": "number"}
    elif change == "description_limit":
        candidate["function"]["description"] = "说明" * 1001
    elif change == "combined_limit":
        candidate["examples"] = [
            {
                "description": "读取已有授权结果",
                "arguments": {"source_query_id": "ref", "code": "x" * 2000},
            }
        ]
    elif change == "schema_bytes":
        parameters["properties"]["code"]["description"] = "说明" * 9000
    else:
        candidate["examples"] = [{"description": "缺少代码", "arguments": {"source_query_id": "x"}}]
    rejected_save(client, path, candidate)


def test_scene_catalog_publishes_descriptions_and_examples_as_one_asset(system):
    client, engine = system
    initial = first_release(client)
    path = "support/routing/scenes.json"
    candidate = json.loads(catalog.bundled()[path])
    candidate["scenes"]["SC-01"]["description"] = "按授权指标查询一个经营数值"
    candidate["scenes"]["SC-01"]["examples"] = ["昨天的销售额是多少？"]
    candidate["scenes"]["SC-01"]["sop"] = "新版工作方法仅在已路由场景使用。"
    save(client, path, dumps(candidate))
    released = publish(client, "file", path=path)
    with Session(engine) as tx, use_prompts(reader(tx, released["generation"])):
        assert "昨天的销售额是多少" in descriptions()["SC-01"]
        assert "新版工作方法" not in descriptions()["SC-01"]
        assert "新版工作方法" in reader(tx, released["generation"])(path)
        assert len(descriptions()) == 40
        member = PromptReader.load_exact(
            tx, released["generation"], ["support/routing/scenes/sc-01.md"]
        )[0]
        asset = PromptReader.load_exact(tx, released["generation"], [path])[0]
        assert member.receipt()["assetPath"] == path
        assert member.receipt()["assetSha256"] == asset.sha256
    with Session(engine) as tx, use_prompts(reader(tx, initial["generation"])):
        assert "昨天的销售额是多少" not in descriptions()["SC-01"]
        assert "新版工作方法" not in reader(tx, initial["generation"])(path)
    candidate["scenes"]["SC-01"]["response_mode"] = "dashboard"
    rejected_save(client, path, candidate)


def test_upgrade_retains_legacy_publications_online_edits_pending_drafts_and_is_idempotent(
    system, monkeypatch
):
    client, engine = system
    assets = catalog.bundled()
    legacy_assets = {
        path: extract(path, assets[member["path"]]) for path, member in MEMBERS.items()
    }
    legacy_assets.update({p: body for p, body in assets.items() if p.endswith(".md")})
    tool = "shared/tools/definitions/python_analysis.md"
    pending = "support/starters/analysis/sales-growth.md"
    with monkeypatch.context() as old:
        old.setattr(catalog, "bundled", lambda: legacy_assets)

        def old_usages():
            refs, allowed = catalog.member_usages()
            return (
                {p: value for p, value in refs.items() if p in legacy_assets},
                {p: value for p, value in allowed.items() if p in legacy_assets},
            )

        old.setattr(catalog, "usages", old_usages)
        old.setattr(catalog, "retired", lambda _: False)
        old.setattr(
            catalog, "current_policy", lambda: ("context-v1", next(iter(LEGACY_POLICY_DIGESTS)))
        )
        import_bundled(client)
        first = publish(client, "all")
        save(client, tool, "这是一份已在线发布的真实工具描述")
        published = publish(client, "file", path=tool)
        save(client, pending, "待审核的在线示例问题")
    with Session(engine) as tx, tx.begin():
        old_row = tx.get(PromptGeneration, published["generation"])
        assert old_row is not None
        manifest = old_row.manifest.copy()
        result = synchronize(tx)
        assert result["assets"] == len(assets)
        assert {"tools/read_document.json", "tools/search_documents.json"} <= assets.keys()
        assert result["drafts"] == ["support/starters/analysis.json"]
        preserved = tx.get(PromptGeneration, published["generation"])
        assert preserved is not None and preserved.manifest == manifest
        assert (
            PromptReader.load_exact(tx, first["generation"], [tool])[0].body == legacy_assets[tool]
        )
        assert (
            PromptReader.load_exact(tx, published["generation"], [tool])[0].body
            == "这是一份已在线发布的真实工具描述"
        )
        current = PromptReader.load_exact(tx, result["generation"], [tool, pending])
        assert current[0].body == "这是一份已在线发布的真实工具描述"
        assert current[1].body != "待审核的在线示例问题"
        draft = tx.get(PromptFile, "support/starters/analysis.json")
        assert draft is not None and draft.draft_body is not None
        assert "待审核的在线示例问题" in draft.draft_body
        count = tx.scalar(select(func.count()).select_from(PromptVersion))
        repeated = synchronize(tx)
        assert not repeated["created"] and repeated["generation"] == result["generation"]
        assert tx.scalar(select(func.count()).select_from(PromptVersion)) == count
        head = tx.get(PromptHead, 1)
        assert head is not None and head.last_snapshot_no == result["snapshot_no"]
    tree = client.get("tree", params={"limit": 200}).json()
    assert len(tree["items"]) == len(catalog.bundled()) and not any(
        row["path"] in MEMBERS for row in tree["items"]
    )
    assert client.post("release-preview", json={"action": "file", "path": tool}).status_code == 422
    restored = publish(client, "restore", snapshot_no=first["snapshot_no"])
    with Session(engine) as tx:
        row = tx.get(PromptGeneration, restored["generation"])
        assert row is not None and len(row.manifest) == len(catalog.bundled())
        assert not set(row.manifest).intersection(MEMBERS)
        assert PromptReader.load_exact(tx, row.id, [tool])[0].body == legacy_assets[tool]
        draft = tx.get(PromptFile, "support/starters/analysis.json")
        assert draft is not None and draft.draft_body is not None


def test_schema_field_named_description_stays_in_the_code_contract():
    code = {
        "type": "object",
        "properties": {"description": {"type": "string", "description": "说明"}},
    }
    assert schema_structure(code)["properties"]["description"] == {"type": "string"}
    assert factory_text("shared/stages/query.md")


def test_current_layout_snapshot_from_supported_previous_policy_can_restore(system, monkeypatch):
    client, engine = system
    with monkeypatch.context() as previous:
        previous.setattr(
            catalog, "current_policy", lambda: ("context-v1", next(iter(LEGACY_POLICY_DIGESTS)))
        )
        initial = first_release(client)
    with Session(engine) as tx, tx.begin():
        synchronize(tx)
    restored = publish(client, "restore", snapshot_no=initial["snapshot_no"])
    with Session(engine) as tx:
        row = tx.get(PromptGeneration, restored["generation"])
        assert row is not None
        assert (row.policy_version, row.policy_digest) == catalog.current_policy()


def test_tool_examples_validate_uuid_formats(system):
    client, _ = system
    first_release(client)
    path = "tools/query_metric.json"
    candidate = json.loads(catalog.bundled()[path])
    candidate["examples"] = [
        {"description": "查询已解析指标", "arguments": {"query_ref": "not-a-uuid"}}
    ]
    rejected_save(client, path, candidate)
