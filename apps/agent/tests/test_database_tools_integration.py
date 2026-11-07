"""PostgreSQL acceptance: native/SQL, 0001 upgrade, roles, drift and restore."""

import importlib.util
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import LiteralString, cast
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from compass_agent.database import Base
from compass_agent.models.entities import Provider
from compass_agent.models.secrets import SecretStore
from cryptography.fernet import Fernet
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

SPEC = importlib.util.spec_from_file_location(
    "agent_database_acceptance", Path(__file__).parents[1] / "scripts/database.py"
)
assert SPEC is not None and SPEC.loader is not None
db = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(db)


@pytest.fixture
def targets(monkeypatch):
    names = ["AGENT_TEST_ADMIN_URL", "AGENT_TEST_DATABASE_URL", "AGENT_TEST_MIGRATION_DATABASE_URL"]
    values = [os.environ.get(name, "") for name in names]
    if not all(values):
        pytest.fail("Explicit AGENT_TEST_* PostgreSQL credentials required")
    admin, runtime, migration = map(urlsplit, values)
    if not runtime.path.startswith("/compass_agent_test_") or runtime.path != migration.path:
        pytest.fail("Dedicated Agent test databases required")
    created = []

    def make(label):
        name = "compass_agent_test_" + uuid4().hex[:10] + "_" + label
        created.append(name)
        run = urlunsplit(runtime._replace(path="/" + name))
        mig = urlunsplit(migration._replace(path="/" + name))
        monkeypatch.setenv("AGENT_PROVISION_DATABASE_URL", admin.geturl())
        monkeypatch.setenv("AGENT_MIGRATION_DATABASE_URL", mig)
        monkeypatch.setenv("DATABASE_URL", run)
        db.provision()
        return run, mig

    yield make
    with psycopg.connect(db.dsn(admin.geturl()), autocommit=True) as connection:
        for name in created:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def schema(url, *, restored=False):
    with psycopg.connect(db.dsn(url)) as connection:
        snapshot = [
            connection.execute(sql.SQL(cast(LiteralString, query))).fetchall()
            for query in [
                "SELECT table_name,column_name,data_type,is_nullable,column_default "
                "FROM information_schema.columns WHERE table_schema='public' "
                "ORDER BY table_name,ordinal_position",
                "SELECT c.relname,t.conname,pg_get_constraintdef(t.oid) FROM pg_constraint t "
                "JOIN pg_class c ON c.oid=t.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' ORDER BY 1,2",
                "SELECT tablename,indexname,indexdef FROM pg_indexes "
                "WHERE schemaname='public' ORDER BY 1,2",
                "SELECT table_name,grantee,privilege_type FROM information_schema.table_privileges "
                "WHERE table_schema='public' ORDER BY 1,2,3",
                "SELECT version_num FROM alembic_version",
            ]
        ]
        if restored:
            # pg_dump/reparse rewrites IN casts while preserving check semantics.
            # Compare identities/validation here and exercise CHECK behavior below.
            snapshot[1] = connection.execute(
                "SELECT c.relname,t.conname,t.contype,t.convalidated FROM pg_constraint t "
                "JOIN pg_class c ON c.oid=t.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' ORDER BY 1,2"
            ).fetchall()
        return snapshot


def test_native_and_sql_upgrade_preserve_rows_and_runtime_permissions(targets, monkeypatch):
    db.write_sql(check=True)
    snapshots = []
    for mode in ["native", "sql"]:
        runtime, migration = targets("upgrade_" + mode)
        assert db.migrate(target="0001") == "applied"
        with psycopg.connect(db.dsn(runtime)) as connection:
            connection.execute("INSERT INTO demo_daily_sales VALUES ('2026-10-01',12345,7)")
        assert db.migrate(mode=mode, start="0001") == "applied"
        assert db.migrate(mode=mode) == "already_applied"
        with psycopg.connect(db.dsn(runtime)) as connection:
            assert connection.execute("SELECT revenue_minor FROM demo_daily_sales").fetchone() == (
                12345,
            )
            assert connection.execute("SELECT count(*) FROM model_provider").fetchone() == (0,)
            for action in [
                "CREATE TABLE forbidden_table(id int)",
                "DELETE FROM model_change",
                "UPDATE alembic_version SET version_num='0001'",
            ]:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute(sql.SQL(cast(LiteralString, action)))
        snapshots.append(schema(migration))
    assert snapshots[0] == snapshots[1]
    for mode in ["native", "sql"]:
        _, migration = targets("empty_" + mode)
        assert db.migrate(mode=mode, start="empty") == "applied"
        assert schema(migration) == snapshots[0]
    _, migration = targets("wrong")
    with pytest.raises(db.DatabaseError, match="starting"):
        db.migrate(start="0001")
    with pytest.raises(db.DatabaseError, match="range"):
        db.migrate(target="9999")
    db.migrate()
    engine = create_engine(db.dsn(migration).replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.begin() as connection:
            connection.execute(text("SET ROLE compass_agent_owner"))
            connection.execute(text("COMMENT ON TABLE alembic_version IS 'bad-checksum'"))
        with pytest.raises(db.DatabaseError, match="checksum"):
            db.migrate()
    finally:
        engine.dispose()


def test_legacy_0001_adoption_and_unknown_object_refusal(targets):
    runtime, migration = targets("legacy")
    db.migrate(target="0001")
    with psycopg.connect(db.dsn(migration)) as connection:
        connection.execute("SET ROLE compass_agent_owner")
        connection.execute("INSERT INTO demo_daily_sales VALUES ('2026-10-01',100,2)")
        connection.execute("COMMENT ON TABLE alembic_version IS NULL")
        connection.execute(
            sql.SQL("COMMENT ON DATABASE {} IS NULL").format(
                sql.Identifier(urlsplit(migration).path[1:])
            )
        )
    assert db.provision() == "provisioned"
    assert db.migrate(start="0001") == "applied"
    with psycopg.connect(db.dsn(runtime)) as connection:
        assert connection.execute("SELECT revenue_minor FROM demo_daily_sales").fetchone() == (100,)
    _, unknown = targets("unknown")
    with psycopg.connect(db.dsn(unknown)) as connection:
        connection.execute("SET ROLE compass_agent_owner")
        connection.execute("CREATE TABLE private_history (id int)")
    with pytest.raises(db.DatabaseError, match="ledger"):
        db.provision()
    with psycopg.connect(db.dsn(unknown)) as connection:
        assert connection.execute("SELECT to_regclass('private_history')").fetchone() == (
            "private_history",
        )


def test_migration_matches_runtime_metadata(targets):
    _, migration = targets("metadata")
    db.migrate()
    engine = create_engine(db.dsn(migration).replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        engine.dispose()


def test_unknown_sequence_refused_before_adopting_database(targets):
    _, migration = targets("sequence")
    with psycopg.connect(db.dsn(migration)) as connection:
        connection.execute("SET ROLE compass_agent_owner")
        connection.execute("CREATE SEQUENCE unrelated_sequence START 42")
        connection.execute(
            sql.SQL("COMMENT ON DATABASE {} IS NULL").format(
                sql.Identifier(urlsplit(migration).path[1:])
            )
        )
        before = connection.execute(
            "SELECT pg_get_userbyid(datdba),datacl,shobj_description(oid,'pg_database') "
            "FROM pg_database WHERE datname=current_database()"
        ).fetchone()
    with pytest.raises(db.DatabaseError, match="Unknown database objects"):
        db.provision()
    with psycopg.connect(db.dsn(migration)) as connection:
        assert (
            connection.execute(
                "SELECT pg_get_userbyid(datdba),datacl,shobj_description(oid,'pg_database') "
                "FROM pg_database WHERE datname=current_database()"
            ).fetchone()
            == before
        )
        assert connection.execute("SELECT last_value FROM unrelated_sequence").fetchone() == (42,)


@pytest.mark.parametrize(
    "mutation",
    [
        "ALTER TABLE model_provider DROP COLUMN api_key_ciphertext",
        "ALTER TABLE model_provider ALTER COLUMN api_key_ciphertext TYPE integer USING 0",
        "ALTER TABLE model_provider DROP CONSTRAINT ck_model_provider_chat_kind",
        "ALTER TABLE model_call DROP CONSTRAINT model_call_pkey",
        "ALTER TABLE model_provider DROP CONSTRAINT ck_model_provider_adapter; "
        "ALTER TABLE model_provider ADD CONSTRAINT ck_model_provider_adapter "
        "CHECK (adapter IN ('litellm_chat','aliyun_decision'))",
    ],
)
def test_rejects_current_schema_drift_before_reporting_noop(targets, mutation):
    _, migration = targets("drift")
    db.migrate()
    with psycopg.connect(db.dsn(migration)) as connection:
        connection.execute("SET ROLE compass_agent_owner")
        connection.execute(sql.SQL(cast(LiteralString, mutation)))
    with pytest.raises(db.DatabaseError, match="drift"):
        db.migrate()


def pg_tool(tool, url, data=None):
    parsed = urlsplit(db.dsn(url))
    container = os.environ.get("AGENT_TEST_PG_CONTAINER")
    settings = {
        "PGHOST": "127.0.0.1" if container else (parsed.hostname or "localhost"),
        "PGPORT": "5432" if container else str(parsed.port or 5432),
        "PGUSER": unquote(parsed.username or ""),
        "PGPASSWORD": unquote(parsed.password or ""),
        "PGDATABASE": parsed.path[1:],
    }
    args = (
        ["--format=custom"]
        if tool == "pg_dump"
        else [
            "--exit-on-error",
            "--no-owner",
            "--role=compass_agent_owner",
            "--dbname=" + parsed.path[1:],
        ]
    )
    if container:
        command = ["docker", "exec", "-i"]
        for key in settings:
            command += ["-e", key]
        command += [container, tool, *args]
    else:
        command = [tool, *args]
    result = subprocess.run(
        command, input=data, capture_output=True, env={**os.environ, **settings}
    )
    assert result.returncode == 0, "PostgreSQL backup/restore tool failed"
    return result.stdout


def test_restores_data_and_encrypted_values_into_new_owned_database(targets):
    runtime, source = targets("backup")
    db.migrate()
    provider_id = uuid4()
    secrets = SecretStore(Fernet.generate_key().decode())
    now = datetime.now(UTC)
    engine = create_engine(db.dsn(runtime).replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with Session(engine) as session:
            session.add(
                Provider(
                    id=provider_id,
                    catalog_id="deepseek/deepseek-v4-pro",
                    vendor="deepseek",
                    api_model="deepseek-v4-pro",
                    sdk_model="deepseek/deepseek-v4-pro",
                    alias="restore fixture",
                    kind="llm",
                    adapter="litellm_chat",
                    base_url="https://api.deepseek.com",
                    api_key_ciphertext=secrets.seal_key(provider_id, "fixture-restore-key"),
                    enabled=False,
                    chat_visible=False,
                    version=1,
                    binding_version=1,
                    created_at=now,
                    updated_at=now,
                    updated_by=uuid4(),
                )
            )
            session.commit()
    finally:
        engine.dispose()
    with psycopg.connect(db.dsn(source)) as connection:
        connection.execute("SET ROLE compass_agent_owner")
        connection.execute("INSERT INTO demo_daily_sales VALUES ('2026-10-01',9999,9)")
    backup = pg_tool("pg_dump", source)
    assert len(backup) > 1000
    _, destination = targets("restore")
    pg_tool("pg_restore", destination, backup)
    assert schema(source, restored=True) == schema(destination, restored=True)
    assert db.migrate() == "already_applied"
    with psycopg.connect(db.dsn(destination)) as connection:
        assert connection.execute("SELECT revenue_minor FROM demo_daily_sales").fetchone() == (
            9999,
        )
        restored = connection.execute(
            "SELECT api_key_ciphertext FROM model_provider WHERE id=%s", (provider_id,)
        ).fetchone()
        assert restored is not None
        assert secrets.open_key(provider_id, restored[0]) == "fixture-restore-key"
        for statement in [
            "UPDATE model_provider SET kind='unknown'",
            "UPDATE model_provider SET adapter='unknown'",
            "UPDATE model_provider SET kind='decision',chat_visible=true",
            "UPDATE model_provider SET version=0",
        ]:
            with pytest.raises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    connection.execute(sql.SQL(cast(LiteralString, statement)))


@pytest.mark.parametrize("mode", ["native", "sql"])
def test_upgrades_0002_decision_preserving_provider_secrets_and_history(targets, mode):
    import asyncio

    from compass_agent.llm.admission import ExecutionGate
    from compass_agent.models.repository import ModelRepository
    from compass_agent.models.service import ModelService, create_runtime
    from compass_agent.schemas import DecisionInput, DecisionQuestion

    from .provider_fixture import ProviderFixture
    from .test_models import ORIGINS, TEST, create, invoke

    runtime, migration = targets("legacydecision")
    assert db.migrate(target="0002") == "applied"
    engine = create_engine(db.dsn(runtime).replace("postgresql://", "postgresql+psycopg://", 1))
    secrets = SecretStore(Fernet.generate_key().decode())
    repo = ModelRepository(engine, secrets, ORIGINS)
    upstream = ProviderFixture()
    service = ModelService(repo, create_runtime(secrets, upstream.transport), gate=ExecutionGate())
    try:
        body, saved = create(repo)
        # Seed the historical layout with its own column list before upgrading.
        legacy_call_id = uuid4()
        with psycopg.connect(db.dsn(migration)) as connection:
            connection.execute("SET ROLE compass_agent_owner")
            connection.execute(
                "INSERT INTO model_call (call_id,provider_id,config_version,binding_version,"
                "requested_model,purpose,actor_user_id,capability_summary,status,started_at,"
                "deadline_at,upstream_outcome_unknown) VALUES (%s,%s,1,1,%s,'test',%s,'[]',"
                "'completed',now(),now()+interval '180 seconds',false)",
                (legacy_call_id, saved.provider_id, body.api_model, TEST.actor_user_id),
            )
        with psycopg.connect(db.dsn(migration)) as connection:
            connection.execute("SET ROLE compass_agent_owner")
            connection.execute(
                "UPDATE model_provider SET kind='decision',adapter='aliyun_decision', "
                "catalog_id='aliyun/decision-model-preview',vendor='aliyun', "
                "api_model='decision-model-preview',sdk_model=NULL,chat_visible=false, "
                "base_url='https://decision.example.com/compatible-mode/v1' WHERE id=%s",
                (saved.provider_id,),
            )
            before = connection.execute(
                "SELECT * FROM model_provider WHERE id=%s", (saved.provider_id,)
            ).fetchone()
        assert db.migrate(mode=mode, start="0002") == "applied"
        assert db.migrate(mode=mode) == "already_applied"
        with Session(engine) as session:
            row = session.get(Provider, saved.provider_id)
            assert row is not None and row.adapter == "systemone_decision"
            assert row.version == row.binding_version == 1
            assert secrets.open_key(row.id, row.api_key_ciphertext) == body.api_key
        with psycopg.connect(db.dsn(migration)) as connection:
            after = connection.execute(
                "SELECT * FROM model_provider WHERE id=%s", (saved.provider_id,)
            ).fetchone()
        assert before is not None and after is not None
        assert ["systemone_decision" if v == "aliyun_decision" else v for v in before] == list(
            after
        )
        assert repo.operation(body.operation_id).provider_id == saved.provider_id
        assert repo.call(legacy_call_id, TEST).status == "completed"
        result = asyncio.run(
            invoke(
                service,
                saved.provider_id,
                DecisionInput(
                    state="保留配置测试",
                    questions={"ok": DecisionQuestion(type="noul", instructions="是否可用？")},
                ),
            )
        )
        assert result.status == "completed" and result.answers["ok"].noul == 0.8
    finally:
        engine.dispose()


@pytest.mark.parametrize("mode", ["native", "sql"])
def test_0003_upgrade_preserves_call_history_and_adds_nullable_execution(targets, mode):
    from compass_agent.models.repository import ModelRepository

    from .test_models import ORIGINS, TEST, create

    runtime, migration = targets("execution")
    assert db.migrate(target="0003") == "applied"
    engine = create_engine(db.dsn(runtime).replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        secrets = SecretStore(Fernet.generate_key().decode())
        repo = ModelRepository(engine, secrets, ORIGINS)
        body, saved = create(repo)
        call_id = uuid4()
        with psycopg.connect(db.dsn(migration)) as connection:
            connection.execute("SET ROLE compass_agent_owner")
            connection.execute(
                "INSERT INTO model_call (call_id,provider_id,config_version,binding_version,"
                "requested_model,purpose,actor_user_id,capability_summary,status,started_at,"
                "deadline_at,upstream_outcome_unknown) VALUES (%s,%s,1,1,%s,'test',%s,'[]',"
                "'completed',now(),now()+interval '180 seconds',false)",
                (call_id, saved.provider_id, body.api_model, TEST.actor_user_id),
            )
        assert db.migrate(mode=mode, start="0003") == "applied"
        assert repo.call(call_id, TEST).execution is None
        assert repo.call(call_id, TEST).status == "completed"
        assert repo.operation(body.operation_id).provider_id == saved.provider_id
        with psycopg.connect(db.dsn(runtime)) as connection:
            connection.execute(
                "UPDATE model_call SET execution=%s WHERE call_id=%s", ('{"version": 1}', call_id)
            )
            assert connection.execute(
                "SELECT execution->>'version' FROM model_call WHERE call_id=%s", (call_id,)
            ).fetchone() == ("1",)
    finally:
        engine.dispose()
