"""Claim a real Agent Run on the dedicated acceptance store, without a competing Worker."""

import os
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from compass_agent.database import get_engine
from compass_agent.runtime.control import OwnerKey, RunRegistry
from compass_agent.runtime.journal import ExecutionJournal
from compass_agent.runtime.store import RuntimeStore
from compass_agent.schemas import SessionSubmit, TenantScope, TrustedContext
from compass_agent.semantic.service import RegistryTools
from compass_agent.settings import get_settings


@contextmanager
def running_registry(persona, question):
    pg = urlsplit(os.environ["DATABASE_URL"])
    if pg.hostname not in {"localhost", "127.0.0.1"} or "_test_l22_tools_" not in pg.path:
        raise ValueError("Use a dedicated Agent test store for this claimed Run.")
    with psycopg.connect(pg._replace(scheme="postgresql").geturl()) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0017",)
    settings = get_settings()
    store = RuntimeStore(
        get_engine(), settings.compass_runtime_state_key.get_secret_value(), settings=settings
    )
    store.ready = True
    trusted = TrustedContext(
        actor_user_id=UUID(persona["actor"]),
        purpose="chat",
        scope=TenantScope(
            kind="tenant",
            tenant_id=UUID(persona["tenantId"]),
            workspace_id=UUID(persona["workspaceId"]),
        ),
    )
    command = SessionSubmit.model_validate(
        {"requestId": str(uuid4()), "question": question, "selection": {"mode": "auto"}}
    )
    run_id = UUID(store.submit(command, trusted)["runId"])
    journal = ExecutionJournal(store, None, run_id, store.claim(run_id, uuid4()))
    tools = RegistryTools(journal, trusted)
    try:
        yield tools
    finally:
        store.finish(run_id, journal.epoch, "canceled", reason="user_canceled")
        store.engine.dispose()


async def invoke(service, operation, arguments, *, invocation=None):
    control = RunRegistry().register(
        OwnerKey.from_context(service.trusted), run_id=service.journal.run_id, lifetime_s=60
    )
    step, identifier = invocation or (uuid4(), str(uuid4()))
    call = SimpleNamespace(call_id=identifier, step_id=step, control=control)
    return await service.run(operation, arguments, call)
