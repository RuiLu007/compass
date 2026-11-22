import { z } from "zod";
import { id } from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import type { Context } from "./access";

const capability = z.enum(["member", "manage", "share", "business", "data", "release"]);
function administrator(c: Context) {
  if (!c.tenantAdmin || !c.can("govern")) reject("forbidden");
}
export async function administer(c: Context, path: string, raw: unknown) {
  administrator(c);
  if (path === "scopes") {
    const f = z
      .strictObject({
        request_key: id,
        kind: z.enum(["tenant", "workspace", "department", "job"]),
        name: z.string().trim().max(160).optional(),
      })
      .parse(raw);
    const org = f.kind === "department" || f.kind === "job";
    if (org && !f.name) reject("validation_failed", "请填写部门或岗位名称。");
    const key = f.kind === "workspace" ? c.target.workspaceId : org ? f.name : null;
    await c.db.query(
      "INSERT INTO web.semantic_scopes(kind,tenant_id,workspace_id,department_key) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
      [f.kind, c.target.tenantId, f.kind === "workspace" ? c.target.workspaceId : null, key],
    );
    const {
      rows: [s],
    } = await c.db.query(
      "SELECT scope_id FROM web.semantic_scopes WHERE kind=$1 AND tenant_id=$2 AND department_key IS NOT DISTINCT FROM $3",
      [f.kind, c.target.tenantId, key],
    );
    return s;
  }
  const f = z
    .strictObject({ request_key: id, scope_id: id, member_id: id, capability, active: z.boolean() })
    .parse(raw);
  const { rowCount } = await c.db.query(
    `SELECT FROM web.semantic_scopes s JOIN web.tenant_member m ON m.tenant_id=s.tenant_id
     WHERE s.scope_id=$1 AND m.id=$2 AND s.tenant_id=$3 AND s.kind IN ('department','job','workspace','tenant')
       AND (s.workspace_id IS NULL OR s.workspace_id=$4) AND m.status='active'`,
    [f.scope_id, f.member_id, c.target.tenantId, c.target.workspaceId],
  );
  if (!rowCount) reject("not_found");
  if (f.capability === "member") {
    await c.db.query(
      `INSERT INTO web.semantic_scope_memberships(tenant_id,scope_id,tenant_member_id,revoked_at)
       VALUES ($1,$2,$3,CASE WHEN $4::bool THEN NULL ELSE now() END)
       ON CONFLICT(scope_id,tenant_member_id) DO UPDATE SET revoked_at=EXCLUDED.revoked_at`,
      [c.target.tenantId, f.scope_id, f.member_id, f.active],
    );
  } else {
    await c.db.query(
      `INSERT INTO web.semantic_scope_grants(tenant_id,scope_id,grantee_member_id,capability,granted_by,revoked_at)
       VALUES ($1,$2,$3,$4,$5,CASE WHEN $6::bool THEN NULL ELSE now() END)
       ON CONFLICT(scope_id,grantee_member_id,capability) DO UPDATE SET revoked_at=EXCLUDED.revoked_at,granted_by=EXCLUDED.granted_by`,
      [c.target.tenantId, f.scope_id, f.member_id, f.capability, c.actor.userId, f.active],
    );
  }
  return { scope_id: f.scope_id, active: f.active };
}
export async function organization(c: Context) {
  administrator(c);
  const scopes = await c.db.query(
    "SELECT scope_id,kind,department_key AS name FROM web.semantic_scopes WHERE tenant_id=$1 AND kind<>'personal' AND (workspace_id IS NULL OR workspace_id=$2) ORDER BY kind,name",
    [c.target.tenantId, c.target.workspaceId],
  );
  const members = await c.db.query(
    "SELECT m.id,u.display_name AS name FROM web.tenant_member m JOIN web.app_user u ON u.id=m.user_id WHERE m.tenant_id=$1 AND m.status='active' AND u.status='active' ORDER BY u.display_name,m.id",
    [c.target.tenantId],
  );
  const { rows: grants } = await c.db.query(
    `SELECT scope_id, tenant_member_id AS member_id,'member' AS capability FROM web.semantic_scope_memberships WHERE revoked_at IS NULL AND valid_from<=now() AND scope_id=ANY($1::uuid[])
    UNION ALL SELECT scope_id,grantee_member_id,capability FROM web.semantic_scope_grants WHERE revoked_at IS NULL AND scope_id=ANY($1::uuid[])`,
    [scopes.rows.map((s) => s.scope_id)],
  );
  return { scopes: scopes.rows, members: members.rows, grants };
}
