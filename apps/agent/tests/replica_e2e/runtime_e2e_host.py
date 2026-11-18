"""Production HTTP Agent host with deterministic upstreams and wait boundaries for CI."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from compass_agent.execution_context import current_execution
from compass_agent.schemas import AnalysisFileNone, RunCanceled, RunCompleted
from compass_agent.tools.contracts import PhaseCanceled, StopRequested
from fastapi import FastAPI

DIRECTORY = Path(os.environ["COMPASS_TEST_RUNTIME_DIRECTORY"])
support = FastAPI()


@support.post("/authorize")
def authorize():
    return {"allowed": True, "visibility": {"details": True, "revision": "ci-runtime"}}


def record(kind, **fields):
    payload = (json.dumps({"kind": kind, "pid": os.getpid(), **fields}) + "\n").encode()
    descriptor = os.open(
        DIRECTORY / "execution.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ControlledExecutor:
    def __init__(self):
        self.models = SimpleNamespace()

    async def run(self, command, _context, control, _emit):
        journal = current_execution.get()
        assert journal is not None
        identifier = str(journal.run_id)

        async def calculate():
            record("calculated", runId=identifier)
            return {"sum": sum(range(20)), "cleanup": "confirmed"}

        async def pause_at_checkpoint():
            record("held", runId=identifier)
            try:
                while not (DIRECTORY / f"release-{identifier}").exists():
                    await asyncio.sleep(0.025)
            except asyncio.CancelledError:
                raise PhaseCanceled(cleanup_confirmed=True) from None

        async def finish():
            record("finished", runId=identifier)
            return {"total": first["sum"] * 2, "cleanup": "confirmed"}

        try:
            first = await journal.call("tool", "ci-aggregate", {}, calculate, dict)
            if command.question == "lesson20-ci:hold":
                await control.perform(pause_at_checkpoint)
            output = await journal.call("tool", "ci-finish", {}, finish, dict)
            return RunCompleted(
                status="completed",
                finalAnswer=f"验收汇总金额 {output['total']} 分。",
                file=AnalysisFileNone(status="none"),
            )
        except StopRequested:
            return RunCanceled(status="canceled", reason="user", cleanup="confirmed")


def create_agent():
    import compass_agent.main as main
    from compass_agent.api import models, runs
    from compass_agent.database import get_engine
    from compass_agent.models.repository import ModelRepository
    from compass_agent.models.secrets import SecretStore
    from compass_agent.models.service import ModelService, create_runtime
    from compass_agent.settings import get_settings

    from apps.agent.tests.provider_fixture import ProviderFixture

    upstream = ProviderFixture()

    def controlled_models(gate):
        settings = get_settings()
        repository = ModelRepository(
            get_engine(),
            SecretStore(settings.compass_model_encryption_key),
            settings.compass_model_allowed_origins,
        )
        return ModelService(
            repository, create_runtime(repository.secrets, upstream.transport), gate=gate
        )

    def executor(*_args, **_kwargs):
        return ControlledExecutor()

    def request_executor():
        return ControlledExecutor()

    patch.object(main, "create_model_service", controlled_models).start()
    patch.object(models, "create_model_service", controlled_models).start()
    patch.object(main, "build_agent_executor", executor).start()
    main.app.dependency_overrides[runs.get_agent_executor] = request_executor
    return main.app
