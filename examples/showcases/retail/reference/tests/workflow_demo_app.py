"""Manual Lesson 17 fixture with a browser-visible controlled model."""

from types import SimpleNamespace

from compass_agent.api.models import get_model_service
from compass_agent.schemas import AvailableModel, AvailableModels
from fastapi import Request

from .test_agent_run import selected_route
from .workflow_e2e_app import app


def demo_model_service(request: Request):
    if not request.url.path.endswith("/models/available"):
        return get_model_service(request)
    provider = selected_route().provider
    catalog = AvailableModels(
        items=[
            AvailableModel(
                id=provider.id,
                alias="Retail Pro · 测试模型",
                api_model="lesson-17-fixture",
                kind="llm",
                capabilities=provider.capabilities,
            )
        ]
    )
    return SimpleNamespace(repository=SimpleNamespace(available=lambda: catalog))


app.dependency_overrides[get_model_service] = demo_model_service
