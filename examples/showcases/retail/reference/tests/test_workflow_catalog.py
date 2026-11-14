"""Asset discovery and dispatch stay bounded and tied to the server-owned binding."""

import asyncio
import json
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from compass_agent.harness.autonomy import AutonomyLimits
from compass_agent.harness.verification import VerificationRejected
from compass_agent.models.errors import ModelError
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.schemas import (
    ChatMessage,
    GenerateInput,
    ToolCall,
    ToolCallFunction,
    ToolDefinition,
    ToolFunction,
    WorkflowAssetList,
    WorkflowAssetView,
)
from compass_agent.tools.builtin.find_workflows.catalog import RegisteredWorkflow
from compass_agent.tools.business.retail_sales.session import RetailSession
from compass_agent.workflows.script import WorkflowError
from compass_agent.workflows.service import HybridWorkflows, ReuseScript
from pydantic import ValidationError

from .retail_fixtures import DEFAULT_PERIODS
from .test_agent_run import CHAT


class Assets:
    def __init__(self):
        self.items = [
            WorkflowAssetView(
                assetId=uuid4(),
                name=f"归因 {index}",
                status="active",
                definitionVersion=1,
                metadataRevision=1,
                entryContract="channel_attribution@1",
                scriptSource="private-source-marker",
            )
            for index in range(21)
        ]
        self.disabled = False
        self.lookups = []

    def list(self, context, *, name=None, cursor=None, active=False):
        assert context == CHAT and active
        items = [item for item in self.items if name is None or item.name == name]
        offset = int(cursor or "0")
        return WorkflowAssetList(
            items=items[offset : offset + 20],
            nextCursor="20" if len(items) - offset > 20 else None,
        )

    def get(self, context, asset_id, *, active=False):
        assert context == CHAT and active
        self.lookups.append(asset_id)
        if self.disabled:
            raise ModelError("not_found", 404)
        return next(item for item in self.items if item.asset_id == asset_id)


def hybrid(assets):
    child = AsyncMock()
    return HybridWorkflows(
        cast(Any, assets),
        CHAT,
        RunRegistry().register(OwnerKey.from_context(CHAT)),
        RetailSession("渠道归因", (), AutonomyLimits()),
        lambda _: None,
        child,
        AsyncMock(),
    ), child


def call(name, arguments):
    return ToolCall(
        id="test-call",
        function=ToolCallFunction(name=name, arguments=json.dumps(arguments)),
    )


def test_catalog_is_visible_without_source_and_discovery_promotes_overflow():
    async def scenario():
        assets = Assets()
        flow, _ = hybrid(assets)
        prompt = await flow.load_catalog()
        assert "归因 19" in prompt and '"nextCursor":"20"' in prompt
        assert "private-source-marker" not in prompt
        base = [ToolDefinition(function=ToolFunction(name=f"base_{i}")) for i in range(5)]
        request = GenerateInput(
            messages=[ChatMessage(role="system", content=prompt)],
            tools=[*base, *flow.tool_definitions()],
        )
        assert len(request.tools) == 13
        old_id = assets.items[0].asset_id
        original = RegisteredWorkflow.from_asset(assets.items[0]).tool_name
        renamed = assets.items[0].model_copy(update={"name": "新的显示名", "metadata_revision": 2})
        assert RegisteredWorkflow.from_asset(renamed).tool_name == original

        result = await flow.tool(call("find_workflows", {"cursor": "20"}))
        overflow = RegisteredWorkflow.from_asset(assets.items[-1]).tool_name
        assert result["items"][0]["toolName"] == overflow
        assert overflow in {item.function.name for item in flow.tool_definitions()}
        assert flow.registered[original].asset_id == old_id
        assert len(flow.tool_definitions()) == 8
        assert not flow.owns_tool(f"workflow_{uuid4().hex}_v1")

    asyncio.run(scenario())


def test_registered_tool_binds_identity_and_rejects_model_supplied_asset_id(monkeypatch):
    async def scenario():
        assets = Assets()
        flow, _ = hybrid(assets)
        await flow.load_catalog()
        asset = RegisteredWorkflow.from_asset(assets.items[0])
        execute = AsyncMock(return_value={"executionId": str(uuid4()), "status": "ok"})
        monkeypatch.setattr(flow, "dynamic", execute)
        result = await flow.tool(call(asset.tool_name, DEFAULT_PERIODS.model_dump()))
        assert execute.await_args is not None
        bound = execute.await_args.args[0]
        assert bound.asset_id == asset.asset_id and bound.definition_version == 1
        assert bound.args == DEFAULT_PERIODS
        assert result["workflow"]["toolName"] == asset.tool_name
        with pytest.raises(ValidationError):
            await flow.tool(
                call(asset.tool_name, {**DEFAULT_PERIODS.model_dump(), "assetId": str(uuid4())})
            )
        assert execute.await_count == 1

    asyncio.run(scenario())


def test_disable_after_discovery_is_checked_before_running_children():
    async def scenario():
        assets = Assets()
        flow, child = hybrid(assets)
        await flow.load_catalog()
        asset = RegisteredWorkflow.from_asset(assets.items[0])
        assets.disabled = True
        with pytest.raises(ModelError):
            await flow.tool(call(asset.tool_name, DEFAULT_PERIODS.model_dump()))
        assert assets.lookups == [asset.asset_id]
        child.assert_not_awaited()

    asyncio.run(scenario())


def test_workflow_verifiers_use_contract_and_asset_and_gate_receipts(monkeypatch):
    async def scenario():
        assets = Assets()
        flow, _ = hybrid(assets)
        asset = assets.items[0]
        checks = []

        async def verify_contract(receipt, context):
            assert context.target == asset.entry_contract
            assert receipt.result["status"] == "needs_data"
            receipt.result["status"] = "mutated"
            checks.append("contract")

        async def verify_asset(receipt, _context):
            assert receipt.result["status"] == "needs_data"
            checks.append("asset")
            raise VerificationRejected()

        flow.verifiers = {
            asset.entry_contract: (verify_contract,),
            asset.asset_id: (verify_asset,),
        }
        monkeypatch.setattr(
            "compass_agent.workflows.service.execute_script",
            AsyncMock(return_value={"status": "needs_data", "evidenceRefs": []}),
        )
        with pytest.raises(WorkflowError, match="workflow_verification_rejected"):
            await flow.dynamic(
                ReuseScript(assetId=asset.asset_id, definitionVersion=1, args=DEFAULT_PERIODS)
            )
        assert checks == ["contract", "asset"]
        assert flow.save_offer is None

    asyncio.run(scenario())
