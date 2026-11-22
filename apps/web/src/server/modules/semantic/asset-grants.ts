import { z } from "zod";
import { id, type AssetGrantDirectory, type AssetGrantMode } from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import type { Context } from "./access";
import { published } from "./catalog";

const batch = z.strictObject({
  mode: z.enum(["department", "member"]),
  target_ids: z.array(id).min(1),
  asset_ids: z.array(id).min(1),
  active: z.boolean(),
  request_key: id,
});

export async function assetGrantDirectory(c: Context): Promise<AssetGrantDirectory> {
  if (!c.tenantAdmin || !c.can("govern")) reject("forbidden");
  const departments = c.scopes
    .filter((s) => s.kind === "department")
    .map((s) => ({ id: s.id, name: s.label }))
    .sort((a, b) => a.name.localeCompare(b.name, "zh"));
  const { rows: members } = await c.db.query<{ id: string; name: string; login: string }>(
    `SELECT m.id, u.display_name AS name, u.login_name AS login FROM web.tenant_member m
     JOIN web.app_user u ON u.id=m.user_id
     WHERE m.tenant_id=$1 AND m.status='active' AND u.status='active' ORDER BY name, login`,
    [c.target.tenantId],
  );
  const available = (await published(c)).filter(
    (a) =>
      a.kind !== "term" && c.scopes.some((s) => s.id === a.usage_scope_id && s.kind !== "personal"),
  );
  const { rows } = await c.db.query<{
    mode: AssetGrantMode;
    target_id: string;
    asset_id: string;
    active: boolean;
  }>(
    `SELECT CASE WHEN member_id IS NULL THEN 'department' ELSE 'member' END AS mode,
       coalesce(department_id,member_id) AS target_id, asset_id, active
     FROM web.semantic_asset_grants WHERE tenant_id=$1 AND workspace_id=$2 AND asset_id=ANY($3::uuid[])`,
    [c.target.tenantId, c.target.workspaceId, available.map((a) => a.asset_id)],
  );
  // A definition published directly to a department starts with that department's grant.
  const grants = new Map<string, AssetGrantDirectory["grants"][number]>();
  const departmentIds = new Set(departments.map((d) => d.id));
  for (const a of available) {
    if (departmentIds.has(a.usage_scope_id))
      grants.set("department/" + a.usage_scope_id + "/" + a.asset_id, {
        mode: "department",
        target_id: a.usage_scope_id,
        asset_id: a.asset_id,
      });
  }
  for (const { active, ...grant } of rows) {
    const key = [grant.mode, grant.target_id, grant.asset_id].join("/");
    if (active) grants.set(key, grant);
    else grants.delete(key);
  }
  return {
    departments,
    members,
    grants: [...grants.values()],
    assets: available
      .map((a) => ({ id: a.asset_id, name: a.name, code: a.code, kind: a.kind }))
      .sort((a, b) => a.name.localeCompare(b.name, "zh") || a.code.localeCompare(b.code)),
  };
}

export async function changeAssetGrants(c: Context, raw: unknown) {
  const fields = batch.parse(raw);
  const directory = await assetGrantDirectory(c);
  const targets = [...new Set(fields.target_ids)].sort();
  const assets = [...new Set(fields.asset_ids)].sort();
  const availableTargets = new Set(
    (fields.mode === "department" ? directory.departments : directory.members).map((t) => t.id),
  );
  const availableAssets = new Set(directory.assets.map((a) => a.id));
  if (
    targets.some((t) => !availableTargets.has(t)) ||
    assets.some((a) => !availableAssets.has(a))
  ) {
    reject("validation_failed", "所选范围或资产已不可用，请重新打开授权抽屉。");
  }
  await c.db.query(
    `INSERT INTO web.semantic_asset_grants(tenant_id, workspace_id, asset_id, department_id, member_id, active)
     SELECT $1,$2,a.id,CASE WHEN $3::boolean THEN t.id END,CASE WHEN NOT $3 THEN t.id END,$6
     FROM unnest($4::uuid[]) AS t(id) CROSS JOIN unnest($5::uuid[]) AS a(id) ORDER BY t.id,a.id
     ON CONFLICT (workspace_id,asset_id,department_id,member_id) DO UPDATE SET active=excluded.active`,
    [
      c.target.tenantId,
      c.target.workspaceId,
      fields.mode === "department",
      targets,
      assets,
      fields.active,
    ],
  );
  return { mode: fields.mode, active: fields.active, target_ids: targets, asset_ids: assets };
}
