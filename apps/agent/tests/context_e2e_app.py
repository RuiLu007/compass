"""Browser acceptance host with real application services and a loopback model upstream."""

import os

import httpx
from compass_agent import main
from compass_agent.api import models
from compass_agent.models.service import create_runtime

port = int(os.environ["COMPASS_E2E_MODEL_PORT"])


class LocalModelTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        # Only substitute the upstream network destination. Preserve SDK request/response bytes.
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=port)
        request.headers["host"] = f"127.0.0.1:{port}"
        return await super().handle_async_request(request)


production_service = models.create_model_service


def fixture_model_service(gate):
    service = production_service(gate)
    service.runtime = create_runtime(service.repository.secrets, LocalModelTransport)
    return service


# HTTP dependencies and the independent durable worker use the same model transport.
models.create_model_service = fixture_model_service
main.create_model_service = fixture_model_service
app = main.app
