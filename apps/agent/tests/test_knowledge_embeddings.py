import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from compass_agent.api import models
from compass_agent.models.errors import ModelError
from compass_agent.schemas import KnowledgeEmbeddingBatch, TrustedContext


@pytest.mark.parametrize(
    ("scope", "purpose", "allowed"),
    [
        ({"kind": "platform"}, "manage", True),
        ({"kind": "platform"}, "test", False),
        (
            {"kind": "tenant", "tenant_id": str(uuid4()), "workspace_id": str(uuid4())},
            "knowledge_manage",
            True,
        ),
        (
            {"kind": "tenant", "tenant_id": str(uuid4()), "workspace_id": str(uuid4())},
            "chat",
            False,
        ),
    ],
)
def test_embedding_authorizes_management_scope_before_calling_provider(
    monkeypatch, scope, purpose, allowed
):
    embed = AsyncMock(return_value=[[1.0] * 1024])
    monkeypatch.setattr(models, "embed", embed)
    monkeypatch.setattr(models, "get_engine", lambda: None)
    monkeypatch.setattr(models, "get_settings", lambda: None)
    context = TrustedContext(actor_user_id=uuid4(), scope=scope, purpose=purpose)
    command = KnowledgeEmbeddingBatch(texts=["优惠券叠加"])
    if allowed:
        response = asyncio.run(models.knowledge_embeddings(command, context))
        assert len(response.vectors[0]) == 1024
        embed.assert_awaited_once_with(None, None, ["优惠券叠加"])
    else:
        with pytest.raises(ModelError, match="forbidden"):
            asyncio.run(models.knowledge_embeddings(command, context))
        embed.assert_not_awaited()
