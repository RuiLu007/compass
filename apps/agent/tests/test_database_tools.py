"""Offline SQL delivery and fail-closed CLI input checks."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "agent_database_tools", Path(__file__).parents[1] / "scripts/database.py"
)
assert SPEC is not None and SPEC.loader is not None
db = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(db)


@pytest.mark.parametrize(
    "url", ["", "sqlite:///file", "postgresql://x/y", "postgresql://x/compass_web"]
)
def test_refuses_missing_or_foreign_target(monkeypatch, url):
    monkeypatch.setenv("AGENT_MIGRATION_DATABASE_URL", url)
    with pytest.raises(db.DatabaseError):
        db.connection_url("AGENT_MIGRATION_DATABASE_URL")


def test_rejects_unknown_revision_before_connecting():
    with pytest.raises(db.DatabaseError, match="range"):
        db.migrate(target="head")


def test_sql_delivery_is_offline_deterministic_and_detects_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SQL_ROOT", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPASS_MODEL_ENCRYPTION_KEY", raising=False)
    db.write_sql()
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    db.write_sql()
    assert before == {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    db.write_sql(check=True)
    bootstrap = (tmp_path / "bootstrap.sql").read_text()
    upgrade = (tmp_path / "upgrades/0001__0002.sql").read_text()
    assert all(line == line.rstrip() for line in bootstrap.splitlines())
    assert "CREATE TABLE demo_daily_sales" in bootstrap
    assert "CREATE TABLE demo_daily_sales" not in upgrade
    assert "CREATE TABLE model_provider" in upgrade
    assert "UPDATE alembic_version" in upgrade
    assert "compass_agent_runtime" in upgrade
    assert bootstrap.count("BEGIN;") == bootstrap.count("COMMIT;") == 1
    (tmp_path / "bootstrap.sql").write_text(bootstrap + "-- unexpected\n")
    with pytest.raises(db.DatabaseError, match="drift"):
        db.write_sql(check=True)
