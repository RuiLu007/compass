"""Test-only HTTP process with explicit durable crash boundaries and safe call audit."""

import json
import os
from contextlib import asynccontextmanager

import compass_agent.main as agent_main
from compass_agent.runtime.durable_service import CurrentAccess, DurableRuntime

if os.environ.get("COMPASS_TEST_REAL_MODEL"):
    from .e2e_app import app
else:
    from .workflow_e2e_app import app

original_lifespan = app.router.lifespan_context

# Keep the pre-queue model crash and partial-text coverage on the original execution path.
# Replica fault tests exercise the separate post-queue protocol.


class LegacyFixtureRuntime(DurableRuntime):
    def __init__(self, *args, **kwargs):
        kwargs["schedule_jobs"] = False
        kwargs["distributed"] = False
        super().__init__(*args, **kwargs)


agent_main.DurableRuntime = LegacyFixtureRuntime


def audit(kind, **fields):
    target = os.environ.get("COMPASS_TEST_CALL_AUDIT")
    if target:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (json.dumps({"kind": kind, **fields}) + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


if os.environ.get("COMPASS_TEST_CALL_AUDIT") and not os.environ.get("COMPASS_TEST_REAL_MODEL"):
    from compass_agent.execution_context import current_execution
    from compass_agent.tools.builtin.database_query import handler

    from .workflow_fixture import WorkflowModels

    original_model_run = WorkflowModels.run
    original_retail_query = handler.query_database

    async def audited_model(self, prepared, value, **kwargs):
        phase = (
            "child"
            if {tool.function.name for tool in value.tools} == {"retail_sales"}
            else "parent"
        )
        audit("model", callId=str(prepared.call_id), phase=phase, state="started")
        result = await original_model_run(self, prepared, value, **kwargs)
        audit("model", callId=str(prepared.call_id), phase=phase, state="completed")
        return result

    async def audited_retail(prepared, control, **kwargs):
        journal = current_execution.get()
        fields = {
            "runId": str(journal.run_id) if journal else None,
            "attemptId": str(journal.active_attempt.get()) if journal else None,
            "callId": prepared.id,
        }
        audit("retail", **fields, state="started")
        result = await original_retail_query(prepared, control, **kwargs)
        audit("retail", **fields, state="completed")
        return result

    WorkflowModels.run = audited_model
    handler.query_database = audited_retail


async def allowed(self, context, **kwargs):
    return None


async def visibility(self, context):
    return {"details": True, "revision": "durable-test-fixture"}


async def capabilities(self, context):
    from .context_fixture import capability_grants

    return await capability_grants(context)


CurrentAccess.capabilities = capabilities
CurrentAccess.check = allowed
CurrentAccess.visibility = visibility


@asynccontextmanager
async def lifespan(application):
    async with original_lifespan(application):
        host_results = {}

        async def fault(boundary, identifier):
            if boundary == os.environ.get("COMPASS_TEST_CRASH_BOUNDARY"):
                if boundary == "host_result_committed":
                    host_results[identifier] = host_results.get(identifier, 0) + 1
                    if host_results[identifier] < int(
                        os.environ.get("COMPASS_TEST_CRASH_HOST_RESULTS", "1")
                    ):
                        return
                    audit("boundary", runId=str(identifier), hostResults=host_results[identifier])
                os._exit(86)

        application.state.durable_runtime.fault = fault
        yield


app.router.lifespan_context = lifespan
