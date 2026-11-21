import { createHash } from "node:crypto";
import type { MetricView, SemanticCapability } from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import type { Context } from "./access";

export function digest(value: unknown): string {
  // JSON object order is immaterial to approvals and idempotency.
  const normalized = JSON.stringify(value, (_, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(
          Object.keys(item)
            .sort()
            .map((key) => [key, item[key]]),
        )
      : item,
  );
  return createHash("sha256").update(normalized).digest("hex");
}
export type Entry = {
  version: string;
  binding?: string;
  hash?: string;
  attachment?: boolean;
  dependencies?: Record<string, string>;
};
export type Manifest = { assets: Record<string, Entry>; policies: Record<string, number> };
export function policyPartitionKind(kind: string) {
  return kind === "platform" || kind === "workspace" ? kind : "tenant";
}
export type Release = {
  id: string;
  partition: string;
  generation: number;
  digest: string;
  manifest: Manifest;
};
export async function releases(c: Context, pins?: string[]): Promise<Release[]> {
  const scopes = c.scopes.filter((s) => s.use).map((s) => s.id);
  const partitions = c.scopes
    .filter((s) => s.use && ["platform", "tenant", "workspace"].includes(s.kind))
    .map((s) => s.id);
  const { rows } = await c.db.query<Release>(
    `SELECT r.release_id AS id,r.partition_id AS partition,r.generation,r.digest,r.manifest
    FROM web.semantic_release r JOIN web.semantic_release_head h ON h.partition_id=r.partition_id
    LEFT JOIN web.semantic_revocation x ON x.release_id=r.release_id
    WHERE r.partition_id=ANY($1::uuid[]) AND x.release_id IS NULL AND (r.audience_id IS NULL OR r.audience_id=ANY($2::uuid[]))
    AND (CASE WHEN $3::uuid[] IS NULL THEN r.release_id=CASE WHEN h.audience_id=ANY($2::uuid[]) THEN h.gray_id ELSE h.stable_id END
      ELSE r.release_id=ANY($3::uuid[]) END) ORDER BY r.partition_id`,
    [partitions, scopes, pins ?? null],
  );
  if (
    pins &&
    (new Set(rows.map((r) => r.partition)).size !== rows.length || rows.length !== pins.length)
  )
    reject("forbidden", "本次发布或使用权限已变化。");
  return rows;
}
export const versionSelect = `SELECT a.asset_id,a.owner_scope_id,a.code,a.domain,a.kind,a.responsible_ref AS responsible,a.lifecycle,
  a.creator_user_id AS creator,v.editor_user_id AS editor,v.version_id,v.draft_id,v.usage_scope_id,v.version_no,
  v.name,v.summary,v.grain,v.unit,v.digest,v.definition,v.source_id,v.source_position,
  ARRAY(SELECT alias.display_alias FROM web.semantic_aliases alias WHERE alias.origin_version_id=v.version_id AND alias.scope_id=ANY($1::uuid[])) AS aliases,
  ARRAY(SELECT rel.to_asset_id FROM web.semantic_relations rel WHERE rel.from_version_id=v.version_id AND rel.kind IN ('depends_on','derived_from') AND rel.scope_id=ANY($1::uuid[])) AS dependencies
  FROM web.semantic_versions v JOIN web.semantic_assets a ON a.asset_id=v.asset_id`;
export type Version = Omit<
  MetricView,
  "binding_id" | "editable" | "state" | "actions" | "generation" | "release_id"
> & { lifecycle: string };
export async function version(c: Context, id: string): Promise<Version> {
  const { rows } = await c.db.query<Version>(versionSelect + " WHERE v.version_id=$2", [
    c.scopes.map((s) => s.id),
    id,
  ]);
  if (!rows[0]) reject("not_found");
  return rows[0];
}
export async function latest(c: Context, assetId: string, lock = false): Promise<Version> {
  if (lock) {
    // Transaction-scoped lock also protects seed assets without an open draft pointer.
    await c.db.query("SELECT pg_advisory_xact_lock(hashtextextended($1,1))", [assetId]);
  }
  const { rows } = await c.db.query<Version>(
    versionSelect + " WHERE a.asset_id=$2 ORDER BY v.version_no DESC LIMIT 1",
    [c.scopes.map((s) => s.id), assetId],
  );
  if (!rows[0]) reject("not_found");
  return rows[0];
}
export async function targetPartition(
  c: Context,
  ownerScope: string,
  localBinding = false,
  writing = false,
) {
  const owner = c.scopes.find((s) => s.id === ownerScope);
  if (!owner) reject("not_found");
  const kind = localBinding
    ? "workspace"
    : owner.kind === "platform"
      ? "platform"
      : owner.kind === "workspace"
        ? "workspace"
        : "tenant";
  let s = c.scopes.find((s) => s.kind === kind);
  if (!s && kind !== "platform" && writing) {
    await c.db.query(
      "INSERT INTO web.semantic_scopes(kind,tenant_id,workspace_id,department_key) VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING",
      [
        kind,
        c.target.tenantId,
        kind === "workspace" ? c.target.workspaceId : null,
        kind === "workspace" ? c.target.workspaceId : null,
      ],
    );
    const { semanticAccess } = await import("./access");
    c.scopes = (await semanticAccess(c.db, c.actor, c.target)).scopes;
    s = c.scopes.find((s) => s.kind === kind);
  }
  if (!s && kind === "workspace") {
    const tenant = c.scopes.find((s) => s.kind === "tenant");
    if (tenant)
      return { ...tenant, id: c.target.workspaceId, kind: "workspace", label: "本工作区" };
  }
  if (!s) reject("not_found");
  return s;
}
function definitions(c: Context, list: Release[]) {
  const workspace = c.scopes.find((s) => s.kind === "workspace")?.id;
  const map = new Map<string, { entry: Entry; release: Release }>();
  // Workspace records for global/tenant assets attach a local binding to the exact public version.
  for (const r of list.filter((r) => r.partition !== workspace))
    for (const [id, entry] of Object.entries(r.manifest.assets)) map.set(id, { entry, release: r });
  for (const r of list.filter((r) => r.partition === workspace))
    for (const [id, entry] of Object.entries(r.manifest.assets)) {
      const original = map.get(id);
      if ((!entry.attachment && !original) || original?.entry.version === entry.version)
        map.set(id, { entry, release: r });
    }
  return map;
}
export async function published(c: Context, pins?: string[]) {
  if (!c.can("read")) return [];
  const current = await releases(c);
  const active = definitions(c, current);
  const fixed = pins ? definitions(c, await releases(c, pins)) : active;
  const use = c.scopes.filter((s) => s.use).map((s) => s.id);
  const ids = [...fixed.values(), ...active.values()].map((v) => v.entry.version);
  const versions = await c.db.query<Version>(
    versionSelect + " WHERE v.version_id=ANY($2::uuid[]) AND v.usage_scope_id=ANY($1::uuid[])",
    [use, ids],
  );
  const allowed = new Map(versions.rows.map((v) => [v.version_id, v]));
  const { rows: local } = await c.db.query<{ binding_id: string }>(
    "SELECT binding_id FROM web.execution_bindings WHERE workspace_id=$1 AND tenant_id=$2 AND version_id=ANY($3::uuid[])",
    [c.target.workspaceId, c.target.tenantId, ids],
  );
  const localIds = new Set(local.map((b) => b.binding_id));
  const out: MetricView[] = [];
  for (const [asset, { entry, release }] of fixed) {
    const live = active.get(asset);
    const v = allowed.get(entry.version);
    if (!v || !live || !allowed.has(live.entry.version)) continue;
    if (
      Object.entries(entry.dependencies ?? {}).some(
        ([key, value]) =>
          v.dependencies.includes(key) &&
          (fixed.get(key)?.entry.version !== value || !allowed.has(value)),
      )
    )
      continue;
    const binding =
      entry.binding &&
      localIds.has(entry.binding) &&
      c.can("query") &&
      live.entry.binding &&
      localIds.has(live.entry.binding)
        ? entry.binding
        : null;
    const editable = c.scopes.some((s) => s.id === v.owner_scope_id && s.manage);
    const { lifecycle, ...definition } = v;
    void lifecycle;
    out.push({
      ...definition,
      source_id: editable ? v.source_id : null,
      source_position: editable ? v.source_position : "",
      editable,
      actions: editable ? ["revise"] : [],
      state: "published",
      binding_id: binding,
      generation: release.generation,
      release_id: release.id,
    });
  }
  const visibleAssets = new Set(out.map((v) => v.asset_id));
  return out.map((v) => ({
    ...v,
    dependencies: v.dependencies.filter((asset) => visibleAssets.has(asset)),
  }));
}
export async function policies(c: Context, list: Release[]) {
  const revisions = list.flatMap((r) => {
    const partition = c.scopes.find((s) => s.id === r.partition);
    return Object.entries(r.manifest.policies).filter(([id]) => {
      const owner = c.scopes.find((s) => s.id === id);
      return owner && partition?.kind === policyPartitionKind(owner.kind);
    });
  });
  const visible = c.scopes.filter((s) => s.use).map((s) => s.id);
  const { rows } = await c.db.query<{
    id: string;
    revision: number;
    policy: {
      pinned_asset_ids?: string[];
      defaults?: { term_ref: string; target_metric_ref: string }[];
      page_size?: number;
      context_max_items?: number;
      max_search_rounds?: number;
      disabled_capabilities?: SemanticCapability[];
    };
  }>(
    "SELECT scope_id AS id,revision,policy FROM web.semantic_policy WHERE scope_id=ANY($1::uuid[]) ORDER BY scope_id",
    [visible],
  );
  return rows.filter((p) => revisions.some(([id, rev]) => id === p.id && rev === p.revision));
}
