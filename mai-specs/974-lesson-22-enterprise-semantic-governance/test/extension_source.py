"""Create a local, private RLS source for the configurable metric acceptance test."""

import secrets
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql


def prepare_source(admin_dsn):
    host = urlsplit(admin_dsn)
    if host.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("A dedicated local PostgreSQL administrator is required.")
    tag = uuid4().hex[:12]
    database = "compass_source_test_l22_" + tag
    role = "l22_crates_" + tag
    password = secrets.token_urlsafe(24)
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        c.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        c.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(role)
            )
        )
    owner_dsn = host._replace(path="/" + database).geturl()
    with psycopg.connect(owner_dsn) as c:
        c.execute("CREATE SCHEMA kiln_ops")
        c.execute("""CREATE TABLE kiln_ops.shard_moves(
            holder text, kiln text, lane text, received integer, inspected integer,
            fired_at timestamptz, known_at timestamptz)""")
        c.execute("ALTER TABLE kiln_ops.shard_moves ENABLE ROW LEVEL SECURITY")
        c.execute(
            "CREATE POLICY owner_read ON kiln_ops.shard_moves FOR SELECT "
            "USING (holder = current_user)"
        )
        for grant in (
            "GRANT USAGE ON SCHEMA kiln_ops TO {}",
            "GRANT SELECT ON kiln_ops.shard_moves TO {}",
        ):
            c.execute(sql.SQL(grant).format(sql.Identifier(role)))
        with c.cursor() as cur:
            cur.executemany(
                "INSERT INTO kiln_ops.shard_moves VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [
                    (role, "north", "amber", 6, 2, "2026-05-02 12:00Z", "2026-05-02 16:00Z"),
                    (role, "north", "blue", 9, 4, "2026-05-03 09:00Z", "2026-05-03 15:00Z"),
                    (role, "north", "amber", 8, 5, "2026-05-04 12:00Z", "2026-05-05 07:00Z"),
                    (role, "north", "blue", 5, 3, "2026-05-06 12:00Z", "2026-05-06 16:00Z"),
                    (role, "south", "green", 3, 1, "2026-05-06 12:00Z", "2026-05-06 16:00Z"),
                    (role, "north", "amber", 500, 500, "2026-05-06 12:00Z", "2026-07-04 15:00Z"),
                    (
                        "separate-owner",
                        "north",
                        "amber",
                        9000,
                        8000,
                        "2026-05-04 12:00Z",
                        "2026-05-04 15:00Z",
                    ),
                ],
            )
        policy = c.execute(
            "SELECT pg_get_expr(polqual,polrelid) FROM pg_policy "
            "WHERE polrelid='kiln_ops.shard_moves'::regclass"
        ).fetchone()[0]
    return (
        {
            "name": "窑区周转 " + tag,
            "engine": "postgresql",
            "host": host.hostname,
            "port": host.port or 5432,
            "database_name": database,
            "role": role,
            "password": password,
            "sslmode": "disable",
            "revision": 0,
        },
        policy,
        owner_dsn,
    )
