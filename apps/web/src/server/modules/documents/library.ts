import "server-only";
import { reject } from "../../platform/errors";
import type { SqlClient } from "../../platform/db";
import { collection, hash, type Collection, type Document } from "./contracts";
import { currentBinding, loadCollection, type Subject } from "./store";

const key = (d: Pick<Document, "docId" | "version">) => JSON.stringify([d.docId, d.version]);
type ManagedRow = {
  source_id: string;
  source_version: string;
  origin_digest: string | null;
  name: string;
  uploaded: string;
  active: boolean;
  sections: Document["sections"];
};
export async function knowledgeCatalog(
  db: SqlClient,
  s: Pick<Subject, "tenant_id" | "workspace_id">,
) {
  const rows = (
    await db.query<ManagedRow>(
      `SELECT f.source_id,f.source_version,f.origin_digest,f.name,f.created_at AS uploaded,
    NOT b.deleted AND NOT f.deleted AND f.status='ready' AS active,
    coalesce(jsonb_agg(jsonb_build_object('sectionId',c.section_id,'heading',c.heading,'text',c.body,'sectionHash',c.section_hash) ORDER BY c.ordinal)
    FILTER (WHERE c.id IS NOT NULL),'[]') AS sections
    FROM web.knowledge_base b JOIN web.knowledge_file f ON f.base_id=b.id
    LEFT JOIN web.knowledge_chunk c ON c.file_id=f.id AND c.enabled AND NOT f.deleted AND NOT b.deleted
    WHERE b.tenant_id=$1 AND b.workspace_id=$2 GROUP BY b.id,f.id ORDER BY f.id`,
      [s.tenant_id, s.workspace_id],
    )
  ).rows;
  let base = await currentBinding(s);
  let original: Collection;
  const standalone = !base;
  if (base) original = await loadCollection(base);
  else {
    if (!rows.some((r) => !r.origin_digest)) reject("unavailable");
    // A workspace without a retail binding can expose documents on their own.
    original = { schemaVersion: 1, tenantId: s.tenant_id, documents: [], aliases: {} };
    base = {
      tenantId: s.tenant_id,
      workspaceId: s.workspace_id,
      knownAt: new Date().toISOString(),
      timezone: "UTC",
      dataRelease: "workspace-documents",
      collectionDigest: hash(JSON.stringify(original)),
      kinds: ["knowledge"],
      skillEnabled: false,
      policies: {},
    };
  }

  const managed = new Map(rows.map((r) => [JSON.stringify([r.source_id, r.source_version]), r]));
  let changed = standalone;
  const docs: Document[] = [];
  for (const d of original.documents) {
    const r = managed.get(key(d));
    if (!r || !r.origin_digest) {
      docs.push(d);
      continue;
    }
    const same =
      r.active &&
      r.sections.length === d.sections.length &&
      r.sections.every(
        (p, i) =>
          p.sectionHash === d.sections[i].sectionHash && p.sectionId === d.sections[i].sectionId,
      );
    if (same) {
      docs.push(d);
      continue;
    }
    changed = true;
    if (r.active && r.sections.length) docs.push({ ...d, sections: r.sections });
  }
  for (const r of rows) {
    if (r.origin_digest || !r.active || !r.sections.length) continue;
    const uploaded = new Date(r.uploaded).toISOString();
    docs.push({
      kind: "knowledge",
      docId: r.source_id,
      version: r.source_version,
      title: r.name,
      issuer: "工作区知识库",
      workspaceReference: true,
      publishedAt: uploaded,
      approvedAt: null,
      requiresApproval: false,
      validFrom: "0001-01-01",
      validTo: null,
      audienceWorkspaceId: null,
      aliases: [],
      sections: r.sections,
    });
    base.policies[r.source_id] = { enabled: true, audienceWorkspaceId: null };
    changed = true;
  }
  // Reserve the slot even while all of a file's sections are switched off.
  const inactiveSections = rows.filter(
    (r) =>
      r.active &&
      !r.sections.length &&
      (!r.origin_digest ||
        original.documents.some((d) => d.docId === r.source_id && d.version === r.source_version)),
  ).length;
  if (docs.length + inactiveSections > 512)
    reject("validation_failed", "工作区最多可发布 512 份检索文档，请移除旧文档后重试。");
  const live = changed ? collection.parse({ ...original, documents: docs }) : original;
  const originDigest = base.collectionDigest;
  if (changed) {
    base = {
      ...base,
      collectionDigest: hash(JSON.stringify(live)),
      vectorIndex: "text-embedding-3-small:1024:v1",
    };
  }
  const currentSections = new Map(
    live.documents.map((d) => [
      key(d),
      new Set(d.sections.map((p) => JSON.stringify([p.sectionId, p.sectionHash]))),
    ]),
  );
  function authorizedSnapshot(pinned: Collection): Collection {
    return {
      ...pinned,
      documents: pinned.documents.flatMap((d) => {
        if (!managed.has(key(d))) return [d];
        const visible = currentSections.get(key(d));
        const sections = d.sections.filter((p) =>
          visible?.has(JSON.stringify([p.sectionId, p.sectionHash])),
        );
        return sections.length ? [{ ...d, sections }] : [];
      }),
    };
  }
  return { binding: base, originDigest, live, changed, authorizedSnapshot };
}
