"""Real failed/canceled SQL and Sandbox paths for the durable history matrix."""

import asyncio
import json
import os
import subprocess
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from compass_agent.runtime.commands import control
from compass_agent.runtime.context import compass_definition
from compass_agent.runtime.entities import AgentArtifact
from compass_agent.runtime.service import RunService
from compass_agent.schemas import ControlCommand
from compass_agent.tools.loader import ToolLoader
from compass_agent.tools.sandbox.allocations import Allocations
from compass_agent.tools.sandbox.client import SandboxBackend
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from .query_fixtures import provider_for
from .test_agent_run import CHAT, FakeModels, FakeRouting, model_result, selected_route
from .test_agent_run_integration import (
    _required_target,
    _wait_for_backend_exit,
    _wait_for_blocked_backend,
)
from .test_durable_runtime_integration import command, execute
from .test_run_autonomy import output, product, tool
from .test_tool_runtime_integration import release_test_volume


class MatrixModels(FakeModels):
    async def run(self, prepared, value, **kwargs):
        item = self.results.pop(0)
        return item(value) if callable(item) else item


def observation(request, call_id):
    return next(
        json.loads(message.content)
        for message in request.messages
        if message.role == "tool" and message.tool_call_id == call_id
    )


async def failed_sql(runtime):
    models = MatrixModels(
        [
            output(
                tool(
                    "database_query",
                    "sql-failure",
                    op="query",
                    sql="SELECT 1 / 0 AS invalid FROM core.spu LIMIT 1",
                )
            ),
            model_result(text="只读 SQL 执行失败，未产生查询结果。"),
        ]
    )
    binding = _required_target().binding
    worker = RunService(
        cast(Any, models),
        cast(Any, FakeRouting(selected_route())),
        tools_provider=provider_for(lambda _: binding),
        definition=compass_definition(),
    )
    run = await execute(runtime, command(question="执行只读 SQL，查询商品数据"), worker)
    assert len(models.requests) == 2
    received = observation(models.requests[-1][1], "sql-failure")
    assert received["status"] == "error", received
    assert "query_failed" in json.dumps(received), received
    return run


async def canceled_sql(runtime):
    target = _required_target()
    models = MatrixModels([output(product())])
    worker = RunService(
        cast(Any, models),
        cast(Any, FakeRouting(selected_route())),
        tools_provider=provider_for(lambda _: target.binding),
        definition=compass_definition(),
    )
    blocker = await psycopg.AsyncConnection.connect(
        target.admin_dsn.get_secret_value(), autocommit=False, connect_timeout=5
    )
    observer = await psycopg.AsyncConnection.connect(
        target.admin_dsn.get_secret_value(), autocommit=True, connect_timeout=5
    )
    identifier = None
    try:
        await blocker.execute("SET LOCAL lock_timeout = '2s'")
        await blocker.execute("LOCK TABLE core.sku IN ACCESS EXCLUSIVE MODE")
        receipt = runtime.store.submit(command(question="查询 POS 商品实付金额"), CHAT)
        identifier = UUID(receipt["runId"])
        runtime.schedule(identifier, worker, CHAT)
        pid = await _wait_for_blocked_backend(observer, f"compass-run-{identifier}")
        current = runtime.store.read_run(identifier)
        control(
            runtime.store,
            identifier,
            "cancel",
            ControlCommand(requestId=uuid4(), expectedVersion=current.version),
            CHAT,
        )
        runtime.controls[identifier].request_stop("user")
        await asyncio.wait_for(runtime.tasks[identifier], 15)
        await _wait_for_backend_exit(observer, pid)
        run = runtime.store.read_run(identifier)
        assert run.status == "canceled" and run.cleanup_status == "confirmed"
        assert len(models.requests) == 1
        return run
    finally:
        await blocker.rollback()
        await blocker.close()
        await observer.close()
        if identifier and not runtime.tasks[identifier].done():
            runtime.controls[identifier].request_stop("user")
            await asyncio.wait_for(runtime.tasks[identifier], 15)


async def failed_python(runtime, *, missing_nas_file=False):
    # Share the existing lease registry, so this test never chooses a retained user's slot.
    allocation_engine = create_engine(os.environ["AGENT_TEST_DATABASE_URL"])
    backend = SandboxBackend(
        Allocations(allocation_engine, "lesson18-local"),
        domain=os.environ.get("COMPASS_SANDBOX_DOMAIN", "127.0.0.1:8112"),
        api_key=os.environ["COMPASS_SANDBOX_API_KEY"],
        image=os.environ.get("COMPASS_SANDBOX_IMAGE", "compass-sandbox-python:lesson18"),
    )
    allocations = []
    create = backend._create
    collect = backend._collect

    async def observed_create(allocation, **kwargs):
        allocations.append(allocation)
        return await create(allocation, **kwargs)

    async def lose_result(allocation, remaining, *, clean=False):
        if not clean:
            # Compute deletion has already been confirmed. Remove only this newly owned
            # NAS result, then let the production collector perform the actual failed read.
            current = backend.allocations.get(allocation.id, allocation.owner)
            assert current.sandbox_id is None and current.phase == "collect"
            name = "compass-sandbox-nas-1"
            info = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
            assert info["Config"]["Labels"]["com.docker.compose.project"] == "compass-sandbox"
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "exec", name, "rm", f"/exports/slot{current.slot:03d}/result.json"],
                check=True,
                capture_output=True,
            )
        return await collect(allocation, remaining, clean=clean)

    backend._create = observed_create
    if missing_nas_file:
        backend._collect = lose_result
    code = "result = {'value': 17}" if missing_nas_file else "raise ValueError('matrix failure')"
    models = MatrixModels(
        [
            output(product()),
            lambda request: output(
                tool(
                    "python_analysis",
                    "python",
                    source_query_id=observation(request, "p")["queryId"],
                    code=code,
                )
            ),
            model_result(text="Python 分析未完成，计算或结果取件失败。"),
        ]
    )
    worker = RunService(
        cast(Any, models),
        cast(Any, FakeRouting(selected_route())),
        tools_provider=provider_for(lambda _: _required_target().binding),
        tool_loader=ToolLoader(sandbox=backend),
        definition=compass_definition(),
    )
    try:
        run = await execute(runtime, command(question="查询商品并用 Python 分析"), worker)
        assert len(models.requests) == 3
        received = observation(models.requests[-1][1], "python")
        assert received["status"] == "error", received
        expected = "sandbox_collection_failed" if missing_nas_file else "sandbox_execution_failed"
        assert expected in json.dumps(received), received
        assert run.cleanup_status == "confirmed"
        with Session(runtime.store.engine) as tx:
            assert not tx.scalar(
                select(func.count())
                .select_from(AgentArtifact)
                .where(AgentArtifact.run_id == run.id, AgentArtifact.media_type == "image/png")
            )
        return run
    finally:
        backend._collect = collect
        for allocation in {row.id: row for row in allocations}.values():
            await release_test_volume(
                allocation_engine, backend, str(allocation.id), allocation.owner
            )
        allocation_engine.dispose()
