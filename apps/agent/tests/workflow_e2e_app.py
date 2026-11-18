"""Explicit test composition; historical filename retained for process fixtures."""

import os

from compass_agent import main
from compass_agent.api.runs import get_agent_executor
from compass_agent.schemas import TenantScope

from .test_agent_run import CHAT
from .workflow_fixture import make_executor


def executor():
    assert isinstance(CHAT.scope, TenantScope)
    return make_executor(None, os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"], CHAT.scope)[0]


app = main.app
main.build_agent_executor = lambda *args, **kwargs: executor()
app.dependency_overrides[get_agent_executor] = executor


if __name__ == "__main__":
    from .workflow_fixture import setup_legacy_readonly_retail, setup_retail

    admin = os.environ["AGENT_TEST_ADMIN_URL"]
    setup_retail(admin, os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"])
    setup_legacy_readonly_retail(admin, os.environ["AGENT_RETAIL_TEST_DATABASE_URL"])
