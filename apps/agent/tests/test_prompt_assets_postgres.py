"""Opt-in PostgreSQL acceptance for the asset data upgrade and real HTTP commands."""

import os
from uuid import uuid4

import psycopg
import pytest
from compass_agent.api import promptops
from compass_agent.database import Base
from compass_agent.main import app
from compass_agent.promptops.entities import PROMPT_TABLES, PromptHead
from compass_agent.settings import get_settings
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from . import test_prompt_assets as cases
from .test_promptops import headers

pytestmark = pytest.mark.skipif(
    os.environ.get("COMPASS_PROMPTOPS_POSTGRES_TEST") != "true",
    reason="Requires explicit PostgreSQL test opt-in and administrator credentials",
)


@pytest.fixture
def postgres_system(monkeypatch):
    dsn = os.environ.get("AGENT_TEST_ADMIN_URL")
    if not dsn:
        pytest.fail("AGENT_TEST_ADMIN_URL is required")
    admin = make_url(dsn).set(drivername="postgresql").render_as_string(hide_password=False)
    name = "compass_agent_test_promptops_" + uuid4().hex[:12]
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = create_engine(make_url(dsn).set(drivername="postgresql+psycopg", database=name))
    try:
        Base.metadata.create_all(engine, tables=[Base.metadata.tables[n] for n in PROMPT_TABLES])
        with Session(engine) as tx, tx.begin():
            tx.add(PromptHead(id=1, generation=None, last_snapshot_no=0))
        monkeypatch.setattr(promptops, "get_engine", lambda: engine)
        monkeypatch.setattr(
            get_settings(), "compass_agent_service_token", "promptops-test-credential"
        )
        with TestClient(app, base_url="http://testserver/v1/promptops/") as client:
            client.headers.update(headers(uuid4()))
            yield client, engine
    finally:
        engine.dispose()
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def test_postgres_upgrade_keeps_published_history_and_pending_drafts(postgres_system, monkeypatch):
    cases.test_upgrade_retains_legacy_publications_online_edits_pending_drafts_and_is_idempotent(
        postgres_system, monkeypatch
    )


def test_postgres_tool_publication_reaches_model_definition(postgres_system):
    cases.test_tool_description_examples_and_parameter_annotations_reach_the_actual_declaration(
        postgres_system
    )


def test_postgres_scene_publication_keeps_previous_generation(postgres_system):
    cases.test_scene_catalog_publishes_descriptions_and_examples_as_one_asset(postgres_system)


@pytest.mark.parametrize("change", ["type", "required", "unknown", "name", "invalid_example"])
def test_postgres_rejects_schema_tampering(postgres_system, change):
    cases.test_tool_contract_changes_are_rejected_on_the_server(postgres_system, change)
