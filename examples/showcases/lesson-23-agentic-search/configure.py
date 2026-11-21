"""Install the classroom viewer, sale department and reviewer grants in an isolated teaching DB."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from compass_agent.data_connections.connections import DataConnections
from compass_agent.schemas import RetailConnectionSave, TenantScope
from compass_agent.settings import Settings
from materials import OFFER_GROUP, TENANT, WORKSPACE, publish
from sqlalchemy import create_engine


def fixture_db():
    url = os.environ["WEB_TEST_MIGRATION_DATABASE_URL"]
    if not urlsplit(url).path.startswith("/compass_web_test_"):
        raise ValueError("Supply an isolated compass_web_test_ classroom database")
    return psycopg.connect(url)


def configure(args):
    with fixture_db() as conn:
        conn.execute(
            """INSERT INTO web.workspace(id,tenant_id,name,description,slug)
            VALUES(%s,%s,'华东渠道报价可阅','课堂报价的显式受众','media-offer')
            ON CONFLICT (id) DO NOTHING""",
            (OFFER_GROUP, TENANT),
        )
        conn.execute(
            """INSERT INTO web.workspace_member(
                id,tenant_id,workspace_id,tenant_member_id,role_key,permission_keys)
            SELECT gen_random_uuid(),tenant_id,%s,id,'viewer',ARRAY['module.analysis']
            FROM web.tenant_member
            WHERE tenant_id=%s AND user_id='99500000-0000-4000-8000-000000000003'
            ON CONFLICT(tenant_id,workspace_id,tenant_member_id) DO NOTHING""",
            (OFFER_GROUP, TENANT),
        )
        for suffix, capabilities in [
            (31, ["semantics.read", "semantics.query", "models.invoke"]),
            (32, ["semantics.read", "semantics.govern"]),
            (33, ["semantics.read", "semantics.govern"]),
        ]:
            member = "99500000-0000-4000-8000-" + str(suffix).zfill(12)
            conn.execute(
                """UPDATE web.workspace_member SET permission_keys=ARRAY(
                    SELECT DISTINCT unnest(permission_keys||%s::text[]) ORDER BY 1)
                WHERE tenant_member_id=%s AND workspace_id=%s""",
                (capabilities, member, WORKSPACE),
            )
        conn.execute(
            """INSERT INTO web.semantic_scope_memberships(tenant_id,scope_id,tenant_member_id)
            SELECT tenant_id,scope_id,m FROM web.semantic_scopes CROSS JOIN unnest(ARRAY[
                '99500000-0000-4000-8000-000000000031',
                '99500000-0000-4000-8000-000000000030']::uuid[]) m
            WHERE tenant_id=%s AND department_key='sales' ON CONFLICT DO NOTHING""",
            (TENANT,),
        )
        for member, duty in [
            (30, "manage"),
            (30, "share"),
            (30, "release"),
            (32, "business"),
            (33, "data"),
        ]:
            conn.execute(
                """INSERT INTO web.semantic_scope_grants(
                    tenant_id,scope_id,grantee_member_id,capability,granted_by)
                SELECT tenant_id,scope_id,%s,%s,'99500000-0000-4000-8000-000000000002'
                FROM web.semantic_scopes
                WHERE tenant_id=%s AND (kind='tenant' OR department_key='sales')
                ON CONFLICT DO NOTHING""",
                ("99500000-0000-4000-8000-" + str(member).zfill(12), duty, TENANT),
            )
    settings = Settings()
    if not urlsplit(settings.database_url).path.startswith("/compass_agent_test_"):
        raise ValueError("Supply an isolated compass_agent_test_ classroom database")
    scope = TenantScope(kind="tenant", tenant_id=UUID(TENANT), workspace_id=UUID(WORKSPACE))
    manager = DataConnections(create_engine(settings.database_url), settings)
    details = json.loads((args.frozen / "connection.json").read_text())
    details.pop("engine")
    existing = manager.status(scope).connection
    if existing and any(getattr(existing, k) != v for k, v in details.items() if k != "password"):
        raise ValueError("Classroom workspace already has a different source")
    if existing:
        _, saved_password = manager.test_connection_target(scope, scope.workspace_id)
        if not saved_password or saved_password.get_secret_value() != details["password"]:
            raise ValueError("Classroom endpoint belongs to a different frozen database identity")
    revision = (
        existing.revision
        if existing
        else manager.save(
            scope, RetailConnectionSave.model_validate({**details, "revision": 0})
        ).revision
    )
    print(
        "Bound release", publish(args.documents, revision=revision), "connection revision", revision
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("frozen", type=Path)
    p.add_argument("documents", type=Path)
    configure(p.parse_args())
