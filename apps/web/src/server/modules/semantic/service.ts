import { z } from "zod";
import { id, text, sourceBody, type MetricView } from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import { personal, scope, type Context } from "./access";
import {
  published,
  version,
  digest,
  versionSelect,
  targetPartition,
  type Version,
} from "./catalog";
import { discover, findings, idempotent, saveDraft, sources } from "./drafts";
import { addBinding } from "./bindings";
import { inspect, release, restore, review } from "./governance";
import { administer, linkFeedback, organization, publishPolicy } from "./configuration";

export async function list(c: Context): Promise<MetricView[]> {
  const publishedItems = await published(c);
  const readable = new Map(publishedItems.map((v) => [v.asset_id, v]));
  const manageable = c.scopes.filter((s) => s.manage).map((s) => s.id);
  const reviewers = (step: "business" | "data" | "release") =>
    c.scopes.filter((s) => s[step]).map((s) => s.id);
  const tenant = c.scopes.find((s) => s.kind === "tenant");
  const { rows } = await c.db.query<Version>(
    versionSelect +
      ` JOIN web.semantic_scopes owner ON owner.scope_id=a.owner_scope_id
      WHERE v.version_no=(SELECT max(version_no) FROM web.semantic_versions WHERE asset_id=a.asset_id)
      AND (a.owner_scope_id=ANY($2::uuid[]) OR
        (owner.tenant_id IS NOT DISTINCT FROM $3::uuid OR owner.kind='platform') AND
        CASE WHEN owner.kind='personal' THEN CASE WHEN owner.scope_id=v.usage_scope_id THEN $4::uuid ELSE v.usage_scope_id END ELSE owner.scope_id END
          =ANY(CASE a.lifecycle WHEN 'business' THEN $5::uuid[] WHEN 'data' THEN $6::uuid[] WHEN 'approved' THEN $7::uuid[] ELSE '{}'::uuid[] END)
        AND EXISTS (SELECT FROM web.semantic_reviews r WHERE r.version_id=v.version_id AND r.step='submit'))
      ORDER BY a.asset_id`,
    [
      c.scopes.map((s) => s.id),
      manageable,
      c.target.tenantId,
      tenant?.id ?? null,
      reviewers("business"),
      reviewers("data"),
      reviewers("release"),
    ],
  );
  for (const v of rows) {
    const pub = readable.get(v.asset_id);
    if (pub && (pub.version_id === v.version_id || v.lifecycle === "published")) continue;
    const editable = manageable.includes(v.owner_scope_id);
    readable.set(v.asset_id, {
      ...v,
      state: v.lifecycle === "published" ? "inactive" : v.lifecycle,
      editable,
      binding_id: null,
      actions: editable ? ["edit"] : [],
      generation: 0,
      release_id: null,
    });
  }
  return [...readable.values()].sort((a, b) => a.name.localeCompare(b.name, "zh"));
}
export async function read(c: Context, path: string[], params: Record<string, string>) {
  if (path[0] === "scopes")
    return {
      items: c.scopes,
      can_create_personal: !!c.memberId && c.can("draft"),
      can_configure: c.tenantAdmin && c.can("govern"),
    };
  if (path[0] === "organization") return organization(c);
  if (path[0] === "policies" && path.length === 2) {
    const s = scope(c, id.parse(path[1]));
    if (!s.release) reject("forbidden");
    const partition = await targetPartition(c, s.id);
    const {
      rows: [p],
    } = await c.db.query(
      `SELECT h.generation,p.policy,p.source_id FROM web.semantic_release_head h
      LEFT JOIN web.semantic_release r ON r.release_id=h.stable_id
      LEFT JOIN web.semantic_policy p ON p.scope_id=$2::uuid AND p.revision=(r.manifest->'policies'->>($2::uuid)::text)::int WHERE h.partition_id=$1`,
      [partition.id, s.id],
    );
    return {
      generation: p?.generation ?? 0,
      policy: p?.policy ?? null,
      source_id: p?.source_id ?? null,
    };
  }
  if (path[0] === "sources") return { items: await sources(c) };
  if (path[0] === "assets") {
    if (!path[1])
      return {
        items: (await (params.state === "published" ? published(c) : list(c))).filter(
          (m) =>
            (!params.q || [m.name, m.code, ...m.aliases].some((x) => x.includes(params.q))) &&
            (!params.domain || m.domain === params.domain) &&
            (!params.owner || m.owner_scope_id === params.owner) &&
            (!params.state || m.state === params.state),
        ),
      };
    const key = id.parse(path[1]);
    const m = (await list(c)).find((v) => v.asset_id === key);
    if (!m) reject("not_found");
    if (
      !params.binding_id &&
      !m.editable &&
      m.state === "published" &&
      c.scopes.find((s) => s.id === m.owner_scope_id)?.kind === "platform" &&
      c.scopes.some((s) => s.kind !== "platform" && (s.manage || s.data || s.business || s.release))
    ) {
      const { bindingQuery } = await import("./governance");
      const local = await c.db.query(bindingQuery, [
        m.version_id,
        c.target.tenantId,
        c.target.workspaceId,
      ]);
      return {
        ...m,
        bindings: local.rows.map((b) => ({
          binding_id: b.binding_id,
          mode: b.mode,
          source_ref: b.source_ref,
          target_ref: b.target_ref,
        })),
      };
    }
    if (!params.binding_id && !m.editable && m.state === "published") return m;
    const chosen = params.version_id
      ? await version(c, id.parse(params.version_id))
      : await version(c, m.version_id);
    if (chosen.asset_id !== key) reject("not_found");
    const detail = await inspect(
      c,
      chosen,
      params.binding_id ? id.parse(params.binding_id) : undefined,
    );
    const { rows: versions } = await c.db.query(
      "SELECT version_id,version_no,name,summary,digest,created_at FROM web.semantic_versions WHERE asset_id=$1 ORDER BY version_no DESC LIMIT 100",
      [key],
    );
    const feedback = detail.editable
      ? await c.db.query(
          "SELECT f.id,f.category,f.note,f.version_id,f.revision_id FROM web.semantic_feedback f JOIN web.semantic_versions v ON v.version_id=f.version_id WHERE f.workspace_id=$1 AND v.asset_id=$2 ORDER BY f.created_at DESC LIMIT 100",
          [c.target.workspaceId, key],
        )
      : { rows: [] };
    return {
      ...detail,
      ...(m.state === "inactive" && chosen.version_id === m.version_id && !params.binding_id
        ? { state: m.state }
        : {}),
      versions: m.editable ? versions : [],
      feedback: feedback.rows,
    };
  }
  if (path[0] === "releases") {
    const { rows } = await c.db.query(
      `SELECT r.release_id,r.partition_id,r.generation,r.created_at,h.generation AS current_generation,
       r.audience_id,x.release_id IS NOT NULL AS revoked,x.reason AS revoked_reason,
       CASE WHEN r.release_id=h.gray_id THEN 'gray' WHEN r.release_id=h.stable_id THEN 'stable' ELSE 'history' END AS channel,
       u.display_name AS publisher,e.note,e.receipt->>'restored_from' AS restored_from,
       e.receipt->>'previous_release_id' AS previous_release_id
      FROM web.semantic_release r JOIN web.semantic_release_head h USING(partition_id) LEFT JOIN web.semantic_revocation x USING(release_id)
      LEFT JOIN LATERAL (SELECT actor_id,note,receipt FROM web.semantic_event WHERE receipt->>'release_id'=r.release_id::text
        AND (action IN ('releases','policies') OR action LIKE 'releases/%/rollback') ORDER BY created_at LIMIT 1) e ON true
      LEFT JOIN web.app_user u ON u.id=e.actor_id
      WHERE r.partition_id=ANY($1::uuid[]) ORDER BY r.created_at DESC LIMIT 100`,
      [c.scopes.filter((s) => s.release).map((s) => s.id)],
    );
    return { items: rows };
  }
  reject("not_found");
}
export async function write(c: Context, path: string[], raw: unknown) {
  const key = z.object({ request_key: id }).parse(raw).request_key;
  if (path.length === 1 && path[0] === "discover") {
    const f = z
      .strictObject({
        request_key: id,
        source_refs: z.array(id).min(1).max(8),
        owner_scope_id: id,
        usage_scope_id: id.optional(),
      })
      .parse(raw);
    return {
      sources: await discover(
        c,
        f.source_refs,
        f.owner_scope_id,
        f.usage_scope_id ?? f.owner_scope_id,
      ),
    };
  }
  if (path[0] === "revisions" && path.length === 3 && path[2] === "validate") {
    const v = await version(c, id.parse(path[1]));
    if (!scope(c, v.owner_scope_id).manage) reject("not_found");
    return { findings: await findings(c, v) };
  }
  return idempotent(c, key, path.join("/"), raw, async () => {
    if (path.length === 1 && path[0] === "bindings") return addBinding(c, raw);
    if (path.length === 1 && ["scopes", "grants"].includes(path[0]))
      return administer(c, path[0], raw);
    if (path.length === 1 && path[0] === "policies") return publishPolicy(c, raw);
    if (path[0] === "feedback" && path.length === 3 && path[2] === "adopt")
      return linkFeedback(c, id.parse(path[1]), raw);
    if (path[0] === "drafts" && (!path[1] || path[2] === "revisions"))
      return saveDraft(c, raw, path[1] ? id.parse(path[1]) : undefined);
    if (path[0] === "revisions" && path.length === 3) {
      const v = await version(c, id.parse(path[1]));
      if (["submit", "business", "data", "return"].includes(path[2]))
        return review(c, v.version_id, path[2], raw);
    }
    if (path[0] === "releases") {
      if (path.length === 1) return release(c, raw);
      if (path[2] === "rollback" || path[2] === "revoke")
        return restore(c, id.parse(path[1]), path[2], raw);
    }
    if (path[0] === "sources") {
      const f = sourceBody.parse(raw);
      const owner = f.owner_scope_id ?? (await personal(c));
      if (!scope(c, owner).manage) reject("forbidden");
      const material = { excerpt: f.excerpt, priority: f.priority, responsible: f.responsible };
      const {
        rows: [v],
      } = await c.db.query(
        "INSERT INTO web.semantic_source(locator,source_version,digest,owner_scope_id,source_kind,material) VALUES ($1,$2,$3,$4,$5,$6) RETURNING source_id",
        [owner + "/" + f.locator, f.version, digest(material), owner, f.kind, material],
      );
      return v;
    }
    if (path[0] === "feedback") {
      const f = z
        .strictObject({ request_key: id, version_id: id, category: text, note: text })
        .parse(raw);
      if (!(await published(c)).some((p) => p.version_id === f.version_id)) reject("not_found");
      const {
        rows: [v],
      } = await c.db.query(
        "INSERT INTO web.semantic_feedback(workspace_id,version_id,actor_id,category,note) VALUES ($1,$2,$3,$4,$5) RETURNING id",
        [c.target.workspaceId, f.version_id, c.actor.userId, f.category, f.note],
      );
      return v;
    }
    reject("not_found");
  });
}
