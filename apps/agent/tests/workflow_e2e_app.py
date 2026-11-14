"""Explicit test composition; historical filename retained for process fixtures."""

import os

from compass_agent.api.runs import get_agent_executor
from compass_agent.main import app
from compass_agent.schemas import TenantScope

from .test_agent_run import CHAT
from .workflow_fixture import make_executor


def executor():
    assert isinstance(CHAT.scope, TenantScope)
    return make_executor(None, os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"], CHAT.scope)[0]


app.dependency_overrides[get_agent_executor] = executor
