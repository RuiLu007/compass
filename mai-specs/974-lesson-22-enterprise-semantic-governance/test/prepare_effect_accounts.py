"""Install the L22 evaluation logins into a dedicated, seeded Web teaching fixture."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg


def teaching_id(n):
    return f"99500000-0000-4000-8000-{n:012}"


def roster():
    for tenant, workspace, suffix in ((10, 20, ""), (11, 22, "102")):
        for name in ("operator", "expert", "steward"):
            yield name + suffix, tenant, workspace, ["sales", "finance", "marketing"], True, True
    for name in ("operator", "expert", "steward"):
        yield "platform_" + name, 10, 20, [], True, True
    for name, depts in (
        ("101.sales", ["sales"]),
        ("101.finance", ["finance"]),
        ("101.sales_finance", ["sales", "finance"]),
        ("101.marketing", ["marketing"]),
        ("101.no_query", ["sales"]),
    ):
        yield name, 10, 20, depts, False, name != "101.no_query"
    yield "102.sales", 11, 22, ["sales"], False, True


def install(c, name, tenant, workspace, departments, reviewer, query):
    handle = "l22." + name
    tenant, workspace = teaching_id(tenant), teaching_id(workspace)
    permissions = [
        "module.analysis",
        "models.invoke",
        "module.admin.data",
        "semantics.read",
        "semantics.draft",
    ]
    if query:
        permissions.append("semantics.query")
    user = c.execute(
        """INSERT INTO web.app_user(id,login_name,display_name,password_hash,is_platform_admin)
           SELECT %s,%s,%s,password_hash,%s FROM web.app_user WHERE login_name='teaching.admin'
           ON CONFLICT(login_name) DO UPDATE SET is_platform_admin=excluded.is_platform_admin
           RETURNING id""",
        (uuid4(), handle, handle, name.startswith("platform_")),
    ).fetchone()
    if user is None:
        raise ValueError("install the teaching fixture first")
    member = c.execute(
        """INSERT INTO web.tenant_member(id,tenant_id,user_id,role_key,permission_keys)
           VALUES(%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,user_id) DO NOTHING RETURNING id""",
        (
            uuid4(),
            tenant,
            user[0],
            "admin" if reviewer else "member",
            [] if reviewer else permissions,
        ),
    ).fetchone()
    if not member:
        member = c.execute(
            "SELECT id FROM web.tenant_member WHERE tenant_id=%s AND user_id=%s", (tenant, user[0])
        ).fetchone()
    c.execute(
        """INSERT INTO web.workspace_member
           (id,tenant_id,workspace_id,tenant_member_id,role_key,permission_keys)
           VALUES(%s,%s,%s,%s,'analyst',%s) ON CONFLICT DO NOTHING""",
        (uuid4(), tenant, workspace, member[0], permissions),
    )
    c.execute(
        """INSERT INTO web.semantic_scope_memberships(tenant_id,scope_id,tenant_member_id)
           SELECT %s,scope_id,%s FROM web.semantic_scopes
           WHERE tenant_id=%s AND department_key=ANY(%s)
           ON CONFLICT DO NOTHING""",
        (tenant, member[0], tenant, departments),
    )
    if reviewer:
        c.execute(
            """INSERT INTO web.semantic_scope_grants
               (tenant_id,scope_id,grantee_member_id,capability,granted_by)
               SELECT %s,scope_id,%s,cap::text,%s FROM web.semantic_scopes
               CROSS JOIN unnest(ARRAY['manage','share','business','data','release']) cap
               WHERE tenant_id=%s AND department_key=ANY(%s) ON CONFLICT DO NOTHING""",
            (tenant, member[0], teaching_id(1), tenant, departments),
        )
    return {"handle": handle, "actor": str(user[0]), "tenantId": tenant, "workspaceId": workspace}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="local JSON of test login names and scope IDs")
    target = parser.parse_args().output
    dsn = os.environ["WEB_TEST_MIGRATION_DATABASE_URL"]
    if not urlsplit(dsn).path.startswith("/compass_web_test_"):
        parser.error("use a dedicated Web test database")
    with psycopg.connect(dsn) as conn:
        conn.execute("SET LOCAL ROLE compass_web_owner")
        identities = {entry[0]: install(conn, *entry) for entry in roster()}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(identities, ensure_ascii=False, indent=2) + "\n")
    print("Evaluation accounts:", len(identities))


if __name__ == "__main__":
    main()
