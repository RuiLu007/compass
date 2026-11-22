"""Check the prompts sent to the model, including real publication and composition."""

import json
import re
from datetime import date
from uuid import uuid4

from compass_agent.context_runtime.contracts import ContextRequest
from compass_agent.context_runtime.pipeline import prepare_input
from compass_agent.context_runtime.profiles import PHASES, Task
from compass_agent.harness.context import AgentDefinition, build_messages
from compass_agent.prompt_assets import factory_text
from compass_agent.promptops import catalog
from compass_agent.promptops.reader import PromptReader
from compass_agent.promptops.template import validate_template
from compass_agent.routing.intent import flash_input
from compass_agent.schemas import (
    AnalysisRunInput,
    ChatMessage,
    GenerateInput,
    MainModelSelection,
    ModelTokenLimits,
    TenantScope,
    ToolDefinition,
    ToolFunction,
    TrustedContext,
)
from sqlalchemy.orm import Session

from .test_promptops import first_release
from .test_promptops import system as system


def assert_list_body(body):
    """Instruction paragraphs must render as lists; headings and variable tables are allowed."""
    assert re.search(r"^- ", body, re.MULTILINE), body
    for line in body.splitlines():
        if not line.strip():
            continue
        assert re.match(r"^(?:#{1,6} |\*\*.+\*\*$|[-|] |\||\d+\. )", line) or line.startswith(
            "  "
        ), line


def test_published_composition_has_one_root_and_flat_list_fragments(system, monkeypatch):
    client, engine = system
    generation = first_release(client)["generation"]
    # Data authorization is orthogonal to prompt composition; all prompt reads use real storage.
    monkeypatch.setattr(
        "compass_agent.context_runtime.pipeline.sources", lambda _tx, _scope: {"fixture": 1}
    )
    owner = TrustedContext(
        actor_user_id=uuid4(),
        purpose="chat",
        scope=TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
    )
    tasks: dict[Task, str] = {
        "business_qa": "SC-01",
        "data_analysis": "SC-02",
        "dashboard": "SC-25",
        "business_report": "SC-29",
    }
    tools = [
        ToolDefinition(function=ToolFunction(name=name))
        for name in ("todo_write", "database_query", "python_analysis", "create_workflow")
    ]
    samples = 0
    with Session(engine) as tx:
        info = PromptReader.describe(tx, generation)
        for scope in ("platform", "operations", "finance"):
            binding = {
                "generation": generation,
                "policyVersion": info.policy_version,
                "policyDigest": info.policy_digest,
                "manifestDigest": info.manifest_digest,
                "scopeProfile": scope,
                "registry": True,
            }
            for task, scene in tasks.items():
                for phase in PHASES:
                    for child in (False, True):
                        support = (
                            ["support/subagent/analysis.md", "shared/fragments/tool-limit.md"]
                            if child
                            else ["support/routing/business.md"]
                        )
                        request = ContextRequest(
                            str(uuid4()),
                            "model:1",
                            "tenant",
                            "workspace",
                            scope,
                            task,
                            scene,
                            phase,
                            generation,
                            1,
                        )
                        compiled = prepare_input(
                            tx,
                            request,
                            binding,
                            GenerateInput(
                                messages=[ChatMessage(role="user", content="核对本周指标")],
                                tools=tools,
                            ),
                            ModelTokenLimits(max_input_tokens=98304, max_output_tokens=4096),
                            owner,
                            {"analysis": True, "model": True, "plugins": True},
                            None,
                            variables={
                                "period": "本周",
                                "unit": "元",
                                "audience": "经营负责人",
                                "tool_limit": "4",
                            },
                            as_of=date(2026, 11, 22),
                            support_paths=support,
                        )
                        body = compiled.request.messages[0].content or ""
                        assert re.findall(r"^# Role: .+$", body, re.MULTILINE) == [
                            "# Role: Compass Agent"
                        ]
                        assert re.findall(r"^## .+$", body, re.MULTILINE) == [
                            "## Goals",
                            "## Workflow",
                            "## Rules",
                        ]
                        assert "{{" not in body
                        assert_list_body(body)
                        samples += 1
    assert samples == 120


def test_list_fragments_and_output_protocols_keep_their_data_contracts():
    assets = catalog.bundled()
    for path, body in assets.items():
        validate_template(path, body)
        if path.startswith("tools/"):
            description = json.loads(body)["function"]["description"]
            assert not re.search(r"^#{1,6} ", description, re.MULTILINE)
            assert_list_body(description)
    for scene in json.loads(assets["support/routing/scenes.json"])["scenes"].values():
        assert not re.search(r"^#{1,6} ", scene["sop"], re.MULTILINE)
        assert_list_body(scene["sop"])
    protocol = factory_text("shared/fragments/search-protocol.md")
    examples = re.findall(r'\{"(?:targets|key)".*?\}。', protocol)
    assert len(examples) == 2
    for example in examples:
        json.loads(example.removesuffix("。"))


def test_legacy_and_classifier_composition_preserve_fragment_boundaries():
    fragment = factory_text("support/routing/business.md")
    command = AnalysisRunInput(
        requestId=uuid4(), question="核对本周销售额", selection=MainModelSelection(mode="auto")
    )
    messages = build_messages(AgentDefinition(), command, fragment)
    body = messages[0].content or ""
    assert "\n\n**业务请求处理**\n\n- " in body
    assert len(re.findall(r"^# Role:", body, re.MULTILINE)) == 1
    for managed in (False, True):
        result = flash_input(
            GenerateInput(messages=[ChatMessage(role="user", content="核对本周指标")]),
            managed=managed,
        )
        text = result.messages[0].content or ""
        assert len(re.findall(r"^# Role:", text, re.MULTILINE)) == 1
        assert result.output_format == "schema"
        assert result.output_schema is not None
