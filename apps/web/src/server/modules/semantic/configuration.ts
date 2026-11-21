import { z } from "zod";
import { id, text, semanticCapability } from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import { scope, type Context } from "./access";
import { latest, published, targetPartition, version } from "./catalog";
import { sources } from "./drafts";
import { advance, lockHead } from "./governance";

const capability = z.enum(["member", "manage", "share", "business", "data", "release"]);
const policyBody = z.strictObject({
  pinned_asset_ids: z.array(id).max(24),
  defaults: z.array(z.strictObject({ term_ref: id, target_metric_ref: id })).max(32),
  page_size: z.number().int().min(1).max(50),
  context_max_items: z.number().int().min(0).max(16),
  max_search_rounds: z.number().int().min(1).max(16),
  disabled_capabilities: z.array(semanticCapability).max(4).optional(),
});
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
  return { scopes: scopes.rows, members: members.rows };
}
export async function publishPolicy(c: Context, raw: unknown) {
  const f = z
    .strictObject({
      request_key: id,
      scope_id: id,
      source_id: id,
      expected_generation: z.number().int().nonnegative(),
      note: text,
      policy: policyBody,
    })
    .parse(raw);
  const s = scope(c, f.scope_id);
  if (
    !s.release ||
    !(await sources(c)).some(
      (v) => v.source_id === f.source_id && (!v.owner_scope_id || v.owner_scope_id === s.id),
    )
  )
    reject("forbidden");
  const partition = await targetPartition(c, s.id, false, true);
  const head = await lockHead(c, partition.id, f.expected_generation);
  const {
    rows: [current],
  } = await c.db.query("SELECT manifest FROM web.semantic_release WHERE release_id=$1", [
    head.stable_id,
  ]);
  const manifest = current?.manifest ?? { assets: {}, policies: {} };
  const visible = await published(c);
  const valid = (key: string, kind?: string) =>
    visible.some(
      (v) =>
        v.asset_id === key &&
        (!kind || v.kind === kind) &&
        (s.kind !== "platform" ||
          c.scopes.some((x) => x.id === v.usage_scope_id && x.kind === "platform")),
    );
  if (
    f.policy.pinned_asset_ids.some((key) => !valid(key)) ||
    f.policy.defaults.some(
      (d) => !valid(d.term_ref, "term") || !valid(d.target_metric_ref, "metric"),
    )
  )
    reject("validation_failed", "置顶项和默认映射须引用当前可见的已发布定义。");
  const {
    rows: [r],
  } = await c.db.query(
    `INSERT INTO web.semantic_policy(scope_id,revision,source_id,policy)
    SELECT $1,COALESCE(max(revision),0)+1,$2,$3 FROM web.semantic_policy WHERE scope_id=$1 RETURNING revision`,
    [s.id, f.source_id, f.policy],
  );
  manifest.policies[s.id] = r.revision;
  return {
    ...(await advance(c, partition.id, head.generation + 1, manifest, null)),
    scope_id: s.id,
    policy_revision: r.revision,
  };
}
export async function linkFeedback(c: Context, key: string, raw: unknown) {
  const f = z.strictObject({ request_key: id, revision_id: id }).parse(raw);
  const v = await version(c, f.revision_id);
  if (
    !scope(c, v.owner_scope_id).manage ||
    (await latest(c, v.asset_id, true)).version_id !== v.version_id
  )
    reject("forbidden");
  const {
    rows: [item],
  } = await c.db.query(
    `UPDATE web.semantic_feedback f SET revision_id=$3 FROM web.semantic_versions old
    WHERE f.id=$1 AND f.workspace_id=$2 AND old.version_id=f.version_id AND old.asset_id=$4 AND old.version_no<$5
      AND (f.revision_id IS NULL OR f.revision_id=$3) RETURNING f.id`,
    [key, c.target.workspaceId, v.version_id, v.asset_id, v.version_no],
  );
  if (!item) reject("conflict", "请指定同一资产的后续修订。");
  return { ...item, version_id: v.version_id };
}
