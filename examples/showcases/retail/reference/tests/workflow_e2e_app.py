"""Real Agent HTTP, Retail and storage with controlled model responses for Lesson 17."""

import os

from compass_agent.api.runs import RunContext, get_agent_executor
from compass_agent.api.worker_runs import WorkerContext, get_worker_executor
from compass_agent.database import get_engine
from compass_agent.main import app
from compass_agent.schemas import TenantScope
from compass_agent.workflows.assets import WorkflowAssets
from fastapi import Request

from .workflow_fixture import make_executor, setup_retail


async def executor(context: RunContext, request: Request):
    assert isinstance(context.scope, TenantScope)
    return make_executor(
        WorkflowAssets(get_engine()),
        os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"],
        context.scope,
        workflow_drafts=request.app.state.workflow_drafts,
        legacy_approval=bool(os.environ.get("COMPASS_TEST_CRASH_BOUNDARY")),
    )[0]


app.dependency_overrides[get_agent_executor] = executor


if __name__ == "__main__":
    setup_retail(os.environ["AGENT_TEST_ADMIN_URL"], os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"])


async def worker_executor(request: Request, context: WorkerContext):
    return await executor(context, request)


app.dependency_overrides[get_worker_executor] = worker_executor
