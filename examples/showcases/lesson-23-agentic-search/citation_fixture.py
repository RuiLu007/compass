"""Create one isolated, source-backed report for browser citation/revocation acceptance."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from compass_agent.context_runtime.search_verification import verify_batch, verify_draft
from compass_agent.documents.service import DocumentTools
from compass_agent.runtime.journal import ExecutionJournal
from compass_agent.runtime.redis_stream import OutboxPublisher, RedisStream
from compass_agent.runtime.store import RuntimeStore
from compass_agent.schemas import SessionSubmit, ToolCall, ToolCallFunction, TrustedContext
from compass_agent.settings import Settings
from compass_agent.tools.contracts import ToolExecutionResult
from sqlalchemy import create_engine

TENANT = "99500000-0000-4000-8000-000000000010"
WORKSPACE = "99500000-0000-4000-8000-000000000020"


async def create_fixture():
    migration = os.environ["WEB_TEST_MIGRATION_DATABASE_URL"]
    if not urlsplit(migration).path.startswith("/compass_web_test_") or not urlsplit(
        os.environ["DATABASE_URL"]
    ).path.startswith("/compass_agent_test_"):
        raise ValueError("dedicated Web and Agent test databases are required")
    actor, member, group_grant = (uuid4() for _ in range(3))
    username = "citation." + actor.hex[:12]
    binding = (
        Path(os.environ["COMPASS_DOCUMENTS_ROOT"]) / "bindings" / TENANT / (WORKSPACE + ".json")
    )
    group = json.loads(binding.read_text())["policies"]["E-1009"]["audienceWorkspaceId"]
    with psycopg.connect(migration) as web:
        web.execute("SET LOCAL ROLE compass_web_owner")
        web.execute(
            "INSERT INTO web.app_user(id,login_name,display_name,password_hash) "
            "SELECT %s,%s,'引用验收',password_hash FROM web.app_user "
            "WHERE login_name='teaching.analyst'",
            (actor, username),
        )
        web.execute(
            "INSERT INTO web.tenant_member(id,tenant_id,user_id,role_key) "
            "VALUES(%s,%s,%s,'member')",
            (member, TENANT, actor),
        )
        for key, wid in ((uuid4(), WORKSPACE), (group_grant, group)):
            web.execute(
                "INSERT INTO web.workspace_member(id,tenant_id,workspace_id,tenant_member_id,"
                "role_key,permission_keys) "
                "SELECT %s,%s,%s,%s,'analyst',wm.permission_keys FROM web.workspace_member wm "
                "JOIN web.tenant_member tm ON tm.id=wm.tenant_member_id "
                "JOIN web.app_user u ON tm.user_id=u.id "
                "WHERE wm.workspace_id=%s AND u.login_name='teaching.analyst'",
                (key, TENANT, wid, member, wid),
            )
    context = TrustedContext.model_validate(
        {
            "actor_user_id": str(actor),
            "purpose": "chat",
            "scope": {"kind": "tenant", "tenant_id": TENANT, "workspace_id": WORKSPACE},
        }
    )
    settings = Settings()
    engine = create_engine(os.environ["DATABASE_URL"])
    streams = RedisStream(
        settings.compass_redis_url, settings.compass_redis_password.get_secret_value()
    )
    store = RuntimeStore(
        engine, settings.compass_runtime_state_key.get_secret_value(), settings=settings
    )
    publisher = OutboxPublisher(store, streams)
    store.ready = True
    store.distributed = True
    try:
        receipt = store.submit(
            SessionSubmit.model_validate(
                {
                    "requestId": str(uuid4()),
                    "question": "查阅本期跑步专场的完整采购档位。",
                    "selection": {"mode": "auto", "provider_id": None},
                }
            ),
            context,
        )
        run = UUID(receipt["runId"])
        await publisher.flush(run)
        epoch = store.claim(run, uuid4())
        journal = ExecutionJournal(store, publisher, run, epoch)
        docs = DocumentTools(journal, context)
        choices = {}

        async def admit(name, op, args, index):
            sid = uuid4()
            state = SimpleNamespace(
                id=sid,
                index=index,
                tool_calls=[
                    ToolCall(
                        id=op, function=ToolCallFunction(name=name, arguments=json.dumps(args))
                    )
                ],
            )
            execution = SimpleNamespace(
                run_id=run, trusted=context, require_running=lambda: None, call_id=op
            )
            raw = await docs.run(op, args, execution)
            options = await asyncio.to_thread(
                verify_batch,
                journal,
                state,
                [ToolExecutionResult(op, "ok", "confirmed", payload=raw)],
                context,
            )
            return raw, options

        search, _ = await admit(
            "search_documents", "search", {"kind": "external", "query": "E-1009 本期排期"}, 1
        )
        picked = next(
            s for s in search["sections"] if s["docId"] == "E-1009" and "排期" in s["heading"]
        )
        read, options = await admit("read_document", "read", {"ref": picked["ref"], "offset": 0}, 2)
        choices["price"] = options[0]["evidenceId"]
        quote = "6,000 元"
        assert quote in read["sections"][0]["text"]
        sid = uuid4()
        db_step, attempt, _ = journal.prepare("model", "root/model:" + str(sid), {}, {})
        journal.dispatched(attempt)
        journal.accept(db_step, attempt, {"result": {}, "budget": {}, "continuation": None})
        draft = {
            "targets": [{"key": "price", "required": True}],
            "claimProposals": [
                {"key": "price", "op": "measure", "quote": quote, "evidenceId": choices["price"]},
                {
                    "key": "second_price",
                    "op": "measure",
                    "quote": "15,000 元",
                    "evidenceId": choices["price"],
                },
                {"key": "options", "op": "series", "parents": ["price", "second_price"]},
            ],
            "answerBlocks": ["price", "options"],
        }
        result = await asyncio.to_thread(
            verify_draft,
            journal,
            SimpleNamespace(id=sid),
            json.dumps(draft),
            context,
            "business_report",
        )
        await asyncio.to_thread(
            store.finish,
            run,
            epoch,
            "completed",
            answer=result.final_answer,
            citations=result._citations,
            delivery=result._delivery,
        )
        await publisher.flush(run)
        return dict(
            tenantId=TENANT,
            workspaceId=WORKSPACE,
            runId=str(run),
            sessionId=receipt["sessionId"],
            loginName=username,
            groupGrant=str(group_grant),
            member=str(member),
            group=group,
        )
    finally:
        await streams.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(asyncio.run(create_fixture()), indent=2) + "\n")
