"""Install the isolated course ledger and register its read-only workspace connection."""

import argparse
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from compass_agent.data_connections.connections import DataConnections
from compass_agent.database import get_engine
from compass_agent.schemas import DataConnectionSave, TenantScope
from compass_agent.settings import get_settings
from psycopg import sql
from publish import TENANT, WORKSPACE, atomic, sha

DATABASE = "compass_class_east_gmv_202510"
READER = "compass_class_east_gmv_reader"
SNAPSHOT = Path(__file__).parent / "data/snapshot.sql"
MARKER = "Compass lesson 23 east GMV " + sha(SNAPSHOT.read_bytes())


def install(private):
    admin = os.environ["SHOWCASE_DATABASE_ADMIN_URL"]
    addr = urlsplit(admin)
    if addr.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("A dedicated local classroom PostgreSQL administrator is required")
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    credentials = private / "reader.json"
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute("SELECT pg_advisory_lock(hashtext(%s))", (DATABASE,))
        row = db.execute(
            "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",
            (DATABASE,),
        ).fetchone()
        role = db.execute(
            "SELECT shobj_description(oid,'pg_authid'),rolsuper,rolcreaterole,rolcreatedb,"
            "rolbypassrls FROM pg_roles WHERE rolname=%s",
            (READER,),
        ).fetchone()
        if (row and row[0] != MARKER) or (
            role and (role != (MARKER, False, False, False, False) or not credentials.is_file())
        ):
            raise ValueError("Existing classroom target has another identity")
        if not credentials.exists():
            atomic(credentials, json.dumps({"password": secrets.token_urlsafe(32)}).encode())
        password = json.loads(credentials.read_bytes())["password"]
        if not role:
            with db.transaction():
                db.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(READER), sql.Literal(password)
                    )
                )
                db.execute(
                    sql.SQL("COMMENT ON ROLE {} IS {}").format(
                        sql.Identifier(READER), sql.Literal(MARKER)
                    )
                )
        if not row:
            db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
            db.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(DATABASE), sql.Literal(MARKER)
                )
            )
        db.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DATABASE))
        )
        db.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(DATABASE), sql.Identifier(READER)
            )
        )
        with psycopg.connect(admin, dbname=DATABASE) as book:
            if not book.execute("SELECT to_regclass('public.sales_day')").fetchone()[0]:
                book.execute(SNAPSHOT.read_text())
            book.execute((SNAPSHOT.parent / "postgres-comments.sql").read_text())
            book.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            book.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(READER))
            )
            book.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                    sql.Identifier(READER)
                )
            )
    scope = TenantScope(kind="tenant", tenant_id=UUID(TENANT), workspace_id=UUID(WORKSPACE))
    manager = DataConnections(get_engine(), get_settings())
    config = DataConnectionSave(
        name="华东经营周报",
        engine="postgresql",
        host=addr.hostname,
        port=addr.port or 5432,
        database_name=DATABASE,
        role=READER,
        sslmode="disable",
        password=password,
        revision=0,
    )
    matches = [
        item
        for item in manager.list(scope).items
        if (item.host, item.port, item.database_name, item.role)
        == (config.host, config.port, DATABASE, READER)
    ]
    if len(matches) > 1:
        raise ValueError("Multiple registered connections for the classroom ledger")
    item = matches[0] if matches else manager.save_connection(scope, config, None)
    print(json.dumps({"connection": str(item.id), "revision": item.revision}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("private", type=Path)
    install(parser.parse_args().private)
