import "server-only";
import { timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { bindAccess, allowed } from "../modules/access/public";
import { bindIdentity } from "../modules/identity/infrastructure/pg-identity";
import { withTransaction } from "../platform/db";
import { reject } from "../platform/errors";
import { readJson, jsonResponse, errorResponse } from "./responses";
import { acceptPin, readPin, loadCollection } from "../modules/documents/store";
import { knowledgeCatalog } from "../modules/documents/library";
import { hash, sourceRef, searchArgs } from "../modules/documents/contracts";
import { searchDocuments, readDocument, visibleDocuments } from "../modules/documents/search";

const envelope = z.strictObject({
  actor_user_id: z.uuid(),
  tenant_id: z.uuid(),
  workspace_id: z.uuid(),
  run_id: z.uuid(),
  args: z.record(z.string(), z.unknown()).default({}),
});
const readArgs = z.strictObject({ sourceRef, offset: z.number().int().min(-1).max(1).default(0) });
const verifyArgs = z.strictObject({
  refs: z.array(sourceRef).max(64).default([]),
  kinds: z
    .array(z.enum(["semantic", "detail", "knowledge", "history", "external"]))
    .max(5)
    .default([]),
});
export async function internalDocuments(request: Request, operation: string) {
  try {
    const token = process.env.COMPASS_AGENT_SERVICE_TOKEN;
    const expected = Buffer.from("Bearer " + token),
      actual = Buffer.from(request.headers.get("authorization") ?? "");
    if (!token || expected.length !== actual.length || !timingSafeEqual(expected, actual))
      reject("forbidden");
    const op = z.enum(["pin", "search", "read", "verify"]).parse(operation);
    const s = envelope.parse(await readJson(request));
    return jsonResponse(
      await withTransaction("query", async (db) => {
        request.signal.throwIfAborted();
        const user = await bindIdentity(db).user(s.actor_user_id);
        if (user.status !== "active") reject("forbidden");
        const service = bindAccess(db);
        // Every reader, including a platform administrator, needs an active tenant membership.
        const access = await service.current(
          { userId: user.id, isPlatformAdmin: false, expiresAt: "", sessionId: "runtime" },
          s.tenant_id,
        );
        const workspace = await service.workspace(s.tenant_id, s.workspace_id);
        if (
          workspace.status !== "active" ||
          !access.memberId ||
          !allowed(access, "module.analysis", {
            tenantId: s.tenant_id,
            workspaceId: s.workspace_id,
          })
        )
          reject("forbidden");
        request.signal.throwIfAborted();
        const catalog = await knowledgeCatalog(db, s);
        const current = catalog.binding;
        if (op === "pin") {
          z.strictObject({}).parse(s.args);
          return acceptPin(s, current, catalog.changed ? catalog.live : undefined);
        }
        const pin = await readPin(s);
        const verification = op === "verify" ? verifyArgs.parse(s.args) : undefined;
        const active = await db.query<{ id: string }>(
          "SELECT id FROM web.workspace WHERE tenant_id=$1 AND status='active' AND id=ANY($2::uuid[])",
          [s.tenant_id, [...access.workspacePermissions.keys()]],
        );
        const activeIds = new Set(active.rows.map((r) => r.id));
        let accessDigest: string | undefined;
        if (verification) {
          if (!verification.kinds.every((k) => current.kinds.includes(k) && pin.kinds.includes(k)))
            return { allowed: false };
          accessDigest = hash(
            JSON.stringify([
              current.collectionDigest,
              access.authzVersion,
              access.memberId,
              access.role,
              [...access.tenantPermissions].sort(),
              [...activeIds]
                .sort()
                .map((id) => [id, [...(access.workspacePermissions.get(id) ?? [])].sort()]),
              [...current.kinds].sort(),
              Object.entries(current.policies).sort(([a], [b]) => a.localeCompare(b)),
            ]),
          );
          if (!verification.refs.length) return { allowed: true, accessDigest };
        }
        const c = {
          access: {
            ...access,
            workspacePermissions: new Map(
              [...access.workspacePermissions].filter(([id]) => activeIds.has(id)),
            ),
          },
          current,
          pin,
          collection: catalog.authorizedSnapshot(
            pin.collectionDigest === current.collectionDigest
              ? catalog.live
              : await loadCollection(pin, request.signal),
          ),
        };
        if (verification)
          return {
            allowed: verification.refs.every((ref) => readDocument(c, ref).length === 1),
            accessDigest,
          };
        const args = op === "search" ? searchArgs.parse(s.args) : null;
        let semantic: string[] = [];
        if (args?.queryVector && pin.vectorIndex) {
          const visible = visibleDocuments(c, args).flatMap((d) =>
            d.sections.map((p) => ({
              doc_id: d.docId,
              version: d.version,
              section_id: p.sectionId,
              section_hash: p.sectionHash,
            })),
          );
          const ranked = await db.query<{
            doc_id: string;
            version: string;
            section_id: string;
            section_hash: string;
          }>(
            `
            WITH eligible AS (SELECT * FROM jsonb_to_recordset($3::jsonb) AS p(doc_id text,version text,section_id text,section_hash text)),
            vectors AS (
              SELECT f.source_id AS doc_id,f.source_version AS version,c.section_id,c.section_hash,c.embedding
              FROM web.knowledge_chunk c JOIN web.knowledge_file f ON f.id=c.file_id JOIN web.knowledge_base b ON b.id=f.base_id
              WHERE b.tenant_id=$1 AND b.workspace_id=$6 AND NOT b.deleted AND NOT f.deleted AND c.enabled AND f.status='ready'
              UNION ALL
              SELECT v.doc_id,v.version,v.section_id,v.section_hash,v.embedding FROM web.document_vectors v WHERE v.tenant_id=$1 AND v.collection_digest=$2 AND v.index_profile=$5
            ), ranked AS (SELECT doc_id,version,section_id,section_hash,min(embedding OPERATOR(public.<=>) $4::public.vector) AS distance
              FROM vectors JOIN eligible USING(doc_id,version,section_id,section_hash) GROUP BY doc_id,version,section_id,section_hash)
            SELECT * FROM ranked ORDER BY distance,doc_id,version,section_id LIMIT 10`,
            [
              pin.tenantId,
              catalog.changed ? catalog.originDigest : pin.collectionDigest,
              JSON.stringify(visible),
              JSON.stringify(args.queryVector),
              pin.vectorIndex,
              pin.workspaceId,
            ],
          );
          semantic = ranked.rows.map((r) =>
            JSON.stringify([r.doc_id, r.version, r.section_id, r.section_hash]),
          );
        }
        const sections = args
          ? searchDocuments(c, args, semantic)
          : (() => {
              const a = readArgs.parse(s.args);
              return readDocument(c, a.sourceRef, a.offset);
            })();
        return {
          ...(args ? { retrievalMode: semantic.length ? "hybrid" : "lexical" } : {}),
          status: sections.length ? "ok" : "empty",
          knownAt: pin.knownAt,
          collectionDigest: pin.collectionDigest,
          sections,
        };
      }),
    );
  } catch (error) {
    return errorResponse(error);
  }
}
