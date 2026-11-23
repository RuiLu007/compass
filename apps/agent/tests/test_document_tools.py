"""Transport failures, persisted source limits and catalog eligibility for document tools."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from compass_agent.context_runtime import source_access
from compass_agent.documents.service import DocumentTools, reference_id
from compass_agent.models.errors import ModelError
from compass_agent.semantic.client import SourceUnavailable
from compass_agent.tools.business.search_documents import SPEC
from compass_agent.tools.catalog import definitions
from compass_agent.tools.catalog_contracts import CatalogContext
from compass_agent.tools.contracts import InProcessBinding


def test_protected_source_limit_persists_across_new_journal_writes(monkeypatch):
    run = SimpleNamespace(
        id=uuid4(),
        config_snapshot={
            "contextRuntime": {"schemaVersion": 1},
            "searchSnapshot": {"tenantId": "t", "workspaceId": "w"},
        },
    )
    message = SimpleNamespace(source_refs=[])
    store = SimpleNamespace(require_epoch=lambda *a: None)
    monkeypatch.setattr(source_access, "assistant_message", lambda *a: message)
    refs = [{"kind": "document", "sectionId": str(i)} for i in range(65)]
    source_access.record_sources_locked(store, None, run, refs[:64], uuid4())
    assert message.source_refs == refs[:64]
    source_access.record_sources_locked(store, None, run, refs, uuid4())
    assert message.source_refs == refs
    monkeypatch.setattr(source_access, "MAX_SEARCH_REFS", 65)
    source_access.record_sources_locked(
        store, None, run, [{"kind": "document", "sectionId": "65"}], uuid4()
    )
    source_access.record_sources_locked(store, None, run, [{"kind": "transient"}], uuid4())
    assert message.source_refs == [{"kind": "source_set_blocked"}]
    assert reference_id(run.id, refs[0]) != reference_id(uuid4(), refs[0])


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "code", "retry_after"),
    [
        (SourceUnavailable(429, "60"), "rate_limited", 60),
        (SourceUnavailable(503), "document_service_unavailable", None),
        (ModelError("forbidden", 403), "document_unavailable", None),
    ],
)
async def test_admitted_read_returns_a_settleable_error_and_preserves_retry_after(
    failure, code, retry_after
):
    from .test_tool_dispatcher import context

    catalog = CatalogContext(
        frozenset({"documents"}),
        documents=cast(DocumentTools, SimpleNamespace(run=AsyncMock(side_effect=failure))),
    )
    assert {d.function.name for d in definitions(catalog)} == {"read_document", "search_documents"}
    assert not definitions(CatalogContext(frozenset({"documents"})))
    tool = SPEC.register(catalog, lambda _: None)
    assert isinstance(tool.binding, InProcessBinding)
    result = await tool.binding.handler({"query": "policy"}, context("search_documents"))
    assert (result.reason_code, result.cleanup) == (code, "confirmed")
    assert isinstance(result.payload, dict)
    assert result.payload["retryable"] is isinstance(failure, SourceUnavailable)
    assert result.payload.get("retryAfterSeconds") == retry_after


def test_revalidation_pages_share_owner_limits_and_fail_closed_on_late_denial(monkeypatch):
    from compass_agent.documents import client
    from compass_agent.documents.service import verify_documents

    tenant, workspace, run = map(str, (uuid4(), uuid4(), uuid4()))
    base = dict(kind="document", tenant=tenant, workspace=workspace, runId=run)
    refs = [{**base, "sectionId": str(i)} for i in range(65)]
    scope = SimpleNamespace(tenant_id=tenant, workspace_id=workspace)
    checks = []

    def current(settings, context, run_id, *, refs, kinds):
        checks.append([r["sectionId"] for r in refs])
        return "signature" if refs[-1]["sectionId"] != "64" else None

    monkeypatch.setattr(client, "verified_access", current)
    assert not verify_documents(
        SimpleNamespace(source_settings=None), SimpleNamespace(scope=scope), refs
    )
    assert list(map(len, checks)) == [64, 1] and checks[1] == ["64"]
    signatures = iter(("old", "new"))
    monkeypatch.setattr(client, "verified_access", lambda *args, **kwargs: next(signatures))
    assert not verify_documents(
        SimpleNamespace(source_settings=None), SimpleNamespace(scope=scope), refs
    )


@pytest.mark.anyio
async def test_in_flight_document_read_releases_http_socket_on_cancellation():
    import asyncio

    from compass_agent.semantic.client import read_document_operation
    from compass_agent.tools.contracts import PhaseCanceled

    from .test_agent_run import CHAT

    dispatched, released = asyncio.Event(), asyncio.Event()

    async def handle(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.splitlines()
                if line.lower().startswith(b"content-length:")
            )
            await reader.readexactly(length)
            dispatched.set()
            assert await reader.read() == b""
            released.set()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        settings = SimpleNamespace(
            compass_web_authz_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            compass_agent_service_token="acceptance-token",
        )
        reading = asyncio.create_task(
            read_document_operation(settings, CHAT, uuid4(), "search", {})
        )
        await asyncio.wait_for(dispatched.wait(), 2)
        reading.cancel()
        with pytest.raises(PhaseCanceled) as stopped:
            await reading
        assert stopped.value.cleanup_confirmed
        await asyncio.wait_for(released.wait(), 2)


@pytest.mark.anyio
async def test_default_documents_pin_once_and_skip_embedding_for_empty_library(monkeypatch):
    import asyncio

    from compass_agent.documents import client, service

    from .test_tool_dispatcher import context

    execution = context("search_documents")
    assert execution.trusted.scope.kind == "tenant"
    pin = {
        "protocol": 2,
        "tenantId": str(execution.trusted.scope.tenant_id),
        "workspaceId": str(execution.trusted.scope.workspace_id),
        "knownAt": "2025-01-01T00:00:00+00:00",
        "collectionDigest": "a" * 64,
        "emptyReason": "no_library",
        "vectorIndex": service.INDEX,
    }
    calls, cache = [], {}

    def accept(*args, **kwargs):
        calls.append(kwargs)
        return pin

    async def local_read(key, args, read, **kwargs):
        if key not in cache:
            cache[key] = await read()
        return cache[key]

    journal = SimpleNamespace(
        store=SimpleNamespace(source_settings=None),
        search_snapshot=None,
        run_id=execution.run_id,
        context_binding={"answerProtocol": 2},
        local_read=local_read,
    )
    response = {**pin, "status": "empty", "sections": [], "reason": "no_library"}
    owner = AsyncMock(return_value=response)
    embedding = AsyncMock(side_effect=AssertionError("empty library needs no embedding"))
    monkeypatch.setattr(client, "accept", accept)
    monkeypatch.setattr(service, "read_document_operation", owner)
    monkeypatch.setattr(service, "embed", embedding)
    monkeypatch.setattr(service, "record_sources", lambda *args: None)
    first, second = (DocumentTools(journal, execution.trusted) for _ in range(2))
    results = await asyncio.gather(
        first.run("search", {"query": "规则"}, execution),
        second.run("search", {"query": "另一个问题"}, execution),
    )
    assert calls == [{"protocol": 2}]
    assert all(r["status"] == "empty" and r["reason"] == "no_library" for r in results)
    assert owner.await_count == 2 and embedding.await_count == 0
