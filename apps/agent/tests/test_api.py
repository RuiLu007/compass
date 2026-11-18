from collections.abc import Iterator
from unittest.mock import patch

import pytest
from compass_agent.database import Base, get_session
from compass_agent.main import app
from compass_agent.seed import seed
from compass_agent.settings import Settings
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client() -> Iterator[tuple[TestClient, Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:

        def override_session() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_session] = override_session
        with patch(
            "compass_agent.main.get_settings", return_value=Settings(compass_demo_enabled=True)
        ):
            with TestClient(app) as test_client:
                yield test_client, session
    app.dependency_overrides.clear()
    engine.dispose()


def test_liveness_does_not_require_database() -> None:
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok", "service": "compass-agent"}


def test_seed_is_idempotent_and_report_uses_database(client: tuple[TestClient, Session]) -> None:
    http, session = client
    seed(session)
    seed(session)
    response = http.get("/v1/demo/overview")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema_version": "1",
        "source": "synthetic-fixture",
        "currency": "CNY",
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "revenue_minor": 4000000,
        "orders": 397,
        "days": 3,
    }


def test_unseeded_database_is_not_reported_as_success(client: tuple[TestClient, Session]) -> None:
    assert client[0].get("/v1/demo/overview").status_code == 503


def test_demo_is_disabled_by_default(client: tuple[TestClient, Session]) -> None:
    with patch(
        "compass_agent.main.get_settings", return_value=Settings(compass_demo_enabled=False)
    ):
        assert client[0].get("/v1/demo/overview").status_code == 404


def test_readiness_fails_without_database() -> None:
    failure = OperationalError("SELECT", {}, Exception("unavailable"))
    with patch("compass_agent.main.get_engine", side_effect=failure), TestClient(app) as client:
        assert client.get("/readyz").status_code == 503


@pytest.fixture
def readiness_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0015')"))
    yield engine
    engine.dispose()


def test_readiness_accepts_current_empty_schema_without_model_key(readiness_engine):
    with (
        patch("compass_agent.main.get_engine", return_value=readiness_engine),
        patch(
            "compass_agent.main.get_settings",
            return_value=Settings(compass_runtime_state_key=SecretStr("")),
        ),
        TestClient(app) as client,
    ):
        app.state.durable_runtime.store.ready = True
        assert client.get("/readyz").status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE alembic_version SET version_num='0001'",
        "UPDATE alembic_version SET version_num='0002'",
        "UPDATE alembic_version SET version_num='0004'",
        "UPDATE alembic_version SET version_num='0005'",
        "DELETE FROM alembic_version",
        "DROP TABLE alembic_version",
        "ALTER TABLE model_provider DROP COLUMN api_key_ciphertext",
        "ALTER TABLE model_call DROP COLUMN usage",
        "ALTER TABLE model_change DROP COLUMN request_mac",
        "ALTER TABLE data_connection DROP COLUMN password_ciphertext",
    ],
)
def test_readiness_rejects_old_revision_or_missing_required_columns(readiness_engine, change):
    with readiness_engine.begin() as connection:
        connection.execute(text(change))
    with (
        patch("compass_agent.main.get_engine", return_value=readiness_engine),
        TestClient(app) as client,
    ):
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"detail": "Database or migration unavailable"}


def test_production_runtime_read_routes_are_registered():
    paths = app.openapi()["paths"]
    assert "get" in paths["/v1/analysis/runtime/usage"]
    assert "get" in paths["/v1/analysis/runs/{run_id}/timeline"]
    assert "post" in paths["/internal/scheduler/triggers/{trigger_id}/submit"]
    assert "post" in paths["/internal/scheduler/triggers/{trigger_id}/reconcile"]
    assert "/internal/runtime/jobs/advance" not in paths
