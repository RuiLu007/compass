"""Explicit test host: real Agent API/database and controlled upstream HTTP only.

Run from Compass: PYTHONPATH=apps/agent/tests uv run uvicorn e2e_app:app --port 8013
The caller must inject dedicated test database, service token and encryption settings.
"""

import os

from compass_agent.api.models import get_model_service
from compass_agent.database import get_engine
from compass_agent.main import app
from compass_agent.models.repository import ModelRepository
from compass_agent.models.secrets import SecretStore
from compass_agent.models.service import ModelService, create_runtime
from compass_agent.settings import get_settings
from fastapi import Request
from provider_fixture import ProviderFixture

fixture = ProviderFixture()
settings = get_settings()
repository = ModelRepository(
    get_engine(),
    SecretStore(settings.compass_model_encryption_key),
    settings.compass_model_allowed_origins,
)


def controlled_service(request: Request):
    return ModelService(
        repository,
        create_runtime(repository.secrets, fixture.transport),
        gate=request.app.state.execution_gate,
    )


app.dependency_overrides[get_model_service] = controlled_service

if os.environ.get("COMPASS_WORKFLOW_E2E_RETAIL_DSN"):
    from apps.agent.tests import workflow_e2e_app  # noqa: F401
