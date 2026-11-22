import "server-only";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import { execute } from "../composition";
import type { SqlClient } from "../platform/db";
import type { TrustedModelContext } from "../modules/models/public";
import { retrieveKnowledge } from "../modules/documents/retrieval";
import { knowledgeCatalog } from "../modules/documents/library";
import { hash } from "../modules/documents/contracts";
import { parseFile, vectors } from "../modules/documents/indexing";
import { ApplicationError, reject } from "../platform/errors";
import { readJson, jsonResponse, privateHeaders } from "./responses";
import { requireCsrf, requireSameOrigin } from "./security";

const name = z
  .string()
  .trim()
  .min(1)
  .max(180)
  .regex(/^[^/\\\u0000-\u001f]+$/);
const revision = z.number().int().positive();
const baseInput = z.object({
  name: name.max(100),
  description: z.string().trim().max(500).default(""),
});
const fileFields =
  "id,name,format,octet_length(original) AS bytes,char_count AS chars,created_at AS uploaded,status,error,revision";
const chunkFields = "id,ordinal,heading,body,enabled,revision";
const id = z.uuid();
type Work = <T>(
  write: boolean,
  fn: (db: SqlClient, c: TrustedModelContext) => Promise<T>,
) => Promise<{ value: T; viewRevision: string }>;
async function indexFile(
  run: Work,
  base: string,
  file: string,
  target: { tenantId: string; workspaceId: string },
) {
  const attempt = randomUUID();
  const claimed = await run(true, async (db, context) => {
    const r = await db.query(
      `UPDATE web.knowledge_file SET attempt=$3,started_at=now(),status='processing',error='' WHERE base_id=$1 AND id=$2 AND NOT deleted AND status<>'ready' AND (attempt IS NULL OR started_at<now()-interval '6 minutes') RETURNING original,format,name`,
      [base, file, attempt],
    );
    if (!r.rowCount) reject("conflict", "文件已完成索引或正在处理中，请刷新列表。");
    return { row: r.rows[0], context };
  });
  try {
    const { row, context } = claimed.value;
    const { chars, chunks } = await parseFile(row.format, row.original);
    const embedded = await vectors(
      context,
      chunks.map((s) => [row.name, s.heading, s.body].join("\n")),
    );
    return await run(true, async (db) => {
      const r = await db.query(
        "UPDATE web.knowledge_file SET status='ready',attempt=NULL,char_count=$3,revision=revision+1 WHERE base_id=$1 AND id=$2 AND NOT deleted AND attempt=$4 RETURNING id",
        [base, file, chars, attempt],
      );
      if (!r.rowCount) reject("conflict");
      const rows = chunks.map((c, i) => ({
        ...c,
        ordinal: i + 1,
        vec: JSON.stringify(embedded[i]),
      }));
      await db.query(
        `INSERT INTO web.knowledge_chunk(file_id,ordinal,section_id,heading,body,section_hash,embedding) SELECT $1,ordinal,'s'||ordinal,heading,body,hash,vec::public.vector FROM jsonb_to_recordset($2::jsonb) AS x(ordinal int,heading text,body text,hash text,vec text)`,
        [file, JSON.stringify(rows)],
      );
      // Serialize admissions across libraries before publishing the workspace catalog.
      await db.query("SELECT pg_advisory_xact_lock(hashtextextended($1, 23))", [
        target.workspaceId,
      ]);
      await knowledgeCatalog(db, { tenant_id: target.tenantId, workspace_id: target.workspaceId });
      return { id: file };
    });
  } catch (e) {
    const message = e instanceof ApplicationError ? e.message : "处理失败，请重新索引。";
    await run(true, async (db) => {
      await db.query(
        "UPDATE web.knowledge_file SET status='failed',error=$3,attempt=NULL,revision=revision+1 WHERE base_id=$1 AND id=$2 AND attempt=$4",
        [base, file, message, attempt],
      );
      return {};
    });
    throw e;
  }
}

export async function handleKnowledge(req: Request, token: string, path: string[]) {
  if (!token) reject("unauthenticated");
  const url = new URL(req.url),
    method = req.method;
  const target = z
    .object({ tenantId: id, workspaceId: id })
    .parse(Object.fromEntries(url.searchParams));
  if (method !== "GET") {
    requireSameOrigin(req);
    requireCsrf(req, token);
  }
  const [root, b, files, f, operation, c] = path;
  const search = path.length === 2 && b === "search" && method === "POST";
  const platformCatalog = root === "catalog" && (method === "GET" || search);
  const searchInput = search
    ? z
        .strictObject({
          query: z.string().trim().min(1).max(1000),
          baseId: id.nullable().default(null),
        })
        .parse(await readJson(req))
    : null;
  if (
    (root !== "bases" && !platformCatalog) ||
    path.length > 6 ||
    (path.length >= 3 && files !== "files") ||
    (path.length === 6 && operation !== "chunks")
  )
    reject("not_found");
  const base = searchInput ? searchInput.baseId : b ? id.parse(b) : null,
    file = f ? id.parse(f) : null,
    chunk = c ? id.parse(c) : null;
  const scope = [target.tenantId, target.workspaceId];
  const run: Work = (write, fn) =>
    execute(token, write ? "command" : "query", async ({ db, models }) => {
      const context = platformCatalog
        ? await models.authorize(token, { kind: "platform", purpose: "manage" })
        : await models.authorize(token, { kind: "tenant", purpose: "knowledge_manage", ...target });
      if (base) {
        const found = await db.query(
          "SELECT id FROM web.knowledge_base WHERE id=$1 AND NOT deleted" +
            (platformCatalog ? "" : " AND tenant_id=$2 AND workspace_id=$3") +
            (write ? " FOR UPDATE" : ""),
          platformCatalog ? [base] : [base, ...scope],
        );
        if (!found.rowCount) reject("not_found");
      }
      if (
        file &&
        !(
          await db.query(
            "SELECT id FROM web.knowledge_file WHERE id=$1 AND base_id=$2 AND NOT deleted",
            [file, base],
          )
        ).rowCount
      )
        reject("not_found");
      return fn(db, context);
    });
  if (searchInput) {
    const started = Date.now();
    const { value: context } = await run(false, async (_db, authorized) => authorized);
    const [vector] = await vectors(context, [searchInput.query]);
    const result = await run(false, (db, authorized) =>
      retrieveKnowledge(db, vector, authorized, searchInput.baseId),
    );
    return jsonResponse(
      {
        query: searchInput.query,
        matches: result.value,
        elapsedMs: Date.now() - started,
        model: "text-embedding-3-small",
      },
      result.viewRevision,
    );
  }
  if (method === "GET" && operation === "original" && file) {
    const { value } = await run(
      false,
      async (db) =>
        (await db.query("SELECT original,name,format FROM web.knowledge_file WHERE id=$1", [file]))
          .rows[0],
    );
    return new Response(new Uint8Array(value.original), {
      headers: {
        ...privateHeaders,
        "Content-Type": value.format === "pdf" ? "application/pdf" : "text/plain; charset=utf-8",
        "Content-Disposition": "inline; filename*=UTF-8''" + encodeURIComponent(value.name),
        "Content-Security-Policy": "sandbox; default-src 'none'",
      },
    });
  }
  if (method === "POST" && operation === "index" && file && base) {
    const r = await indexFile(run, base, file, target);
    return jsonResponse(r.value, r.viewRevision);
  }
  const upload = method === "POST" && !!base && !!files && !file;
  if (upload) await run(false, async () => null);
  const input = method !== "GET" ? await readJson(req, upload ? 7 * 1024 * 1024 : 65536, true) : {};
  let embedding: number[] | undefined;
  const change =
    chunk && method === "PATCH"
      ? z
          .union([
            z.strictObject({ revision, body: z.string().trim().min(1).max(8192) }),
            z.strictObject({ revision, enabled: z.boolean() }),
          ])
          .parse(input)
      : null;
  if (change && "body" in change) {
    const { value } = await run(false, async (db, context) => {
      const r = await db.query(
        "SELECT c.heading,f.name FROM web.knowledge_chunk c JOIN web.knowledge_file f ON f.id=c.file_id WHERE c.id=$1 AND f.id=$2 AND c.revision=$3",
        [chunk, file, change.revision],
      );
      if (!r.rowCount) reject("conflict");
      return { context, ...r.rows[0] };
    });
    embedding = (
      await vectors(value.context, [
        [value.name, value.heading, change.body.normalize("NFC")].join("\n"),
      ])
    )[0];
  }
  const result = await run(method !== "GET", async (db, context) => {
    if (method === "GET") {
      if (!base) {
        const fields = `b.id,b.name,b.description,b.revision,count(f.id)::int AS files,
          coalesce(sum(octet_length(f.original)),0)::float8 AS bytes`;
        if (platformCatalog) {
          const actorId = context.actor_user_id;
          // The platform catalog is read-only. Editing still uses the owner's workspace authorization.
          const editable = `t.status='active' AND w.status='active' AND EXISTS (
            SELECT 1 FROM web.tenant_member m WHERE m.tenant_id=t.id AND m.user_id=$1 AND m.status='active' AND m.role_key='admin')`;
          const joins = "FROM web.workspace w JOIN web.tenant t ON t.id=w.tenant_id";
          const { rows } = await db.query(
            `SELECT ${fields},t.id AS "tenantId",t.name AS "tenantName",
            w.id AS "workspaceId",w.name AS "workspaceName",(${editable}) AS "canManage"
            ${joins} JOIN web.knowledge_base b ON b.workspace_id=w.id AND b.tenant_id=t.id
            LEFT JOIN web.knowledge_file f ON f.base_id=b.id AND NOT f.deleted WHERE NOT b.deleted
            GROUP BY t.id,w.id,b.id ORDER BY t.name,w.name,b.created_at,b.id`,
            [actorId],
          );
          const creation = await db.query(
            `SELECT (${editable}) AS allowed ${joins}
            WHERE t.id=$2 AND w.id=$3`,
            [actorId, ...scope],
          );
          return { bases: rows, canCreate: creation.rows[0]?.allowed === true };
        }
        return (
          await db.query(
            `SELECT ${fields} FROM web.knowledge_base b LEFT JOIN web.knowledge_file f
          ON f.base_id=b.id AND NOT f.deleted WHERE b.tenant_id=$1 AND b.workspace_id=$2
          AND NOT b.deleted GROUP BY b.id ORDER BY b.created_at,b.id`,
            scope,
          )
        ).rows;
      }
      if (path.length === 3)
        return (
          await db.query(
            `SELECT ${fileFields} FROM web.knowledge_file WHERE base_id=$1 AND NOT deleted ORDER BY created_at DESC,id`,
            [base],
          )
        ).rows;
      if (file && operation === "chunks" && !chunk)
        return (
          await db.query(
            `SELECT ${chunkFields} FROM web.knowledge_chunk WHERE file_id=$1 ORDER BY ordinal`,
            [file],
          )
        ).rows;
      reject("not_found");
    }
    if (!base && method === "POST") {
      const d = baseInput.parse(input);
      return (
        await db.query(
          "INSERT INTO web.knowledge_base(tenant_id,workspace_id,name,description) VALUES($1,$2,$3,$4) RETURNING id",
          [...scope, d.name, d.description],
        )
      ).rows[0];
    }
    if (upload) {
      const d = z.object({ id, name, content: z.string().min(4).max(6990508) }).parse(input);
      const format = d.name.split(".").pop()?.toLowerCase(),
        data = Buffer.from(d.content, "base64");
      if (
        !format ||
        !["md", "txt", "pdf"].includes(format) ||
        !data.length ||
        data.length > 5 * 1024 * 1024 ||
        data.toString("base64") !== d.content
      )
        reject("validation_failed", "请上传 5 MB 以内的 PDF、Markdown 或 TXT 文件。");
      const r = await db.query(
        `INSERT INTO web.knowledge_file(id,base_id,name,original,format,status,source_id) VALUES($1::uuid,$2,$3,$4,$5,'processing',$1::uuid::text) ON CONFLICT(id) DO NOTHING RETURNING id`,
        [d.id, base, d.name, data, format],
      );
      if (!r.rowCount) reject("conflict", "文件已提交，请刷新列表。");
      return r.rows[0];
    }
    if (path.length === 2 || path.length === 4) {
      const { revision: rev } = z.object({ revision }).parse(input);
      const table = file ? "knowledge_file" : "knowledge_base",
        key = file ?? base;
      if (method === "DELETE") {
        const r = await db.query(
          `UPDATE web.${table} SET deleted=true,revision=revision+1 WHERE id=$1 AND revision=$2 RETURNING id`,
          [key, rev],
        );
        if (!r.rowCount) reject("conflict");
        return r.rows[0];
      }
      if (method === "PATCH") {
        const d = file
          ? { name: name.parse((input as { name: unknown }).name), description: "" }
          : baseInput.parse(input);
        const r = await db.query(
          `UPDATE web.${table} SET name=$3,revision=revision+1${file ? "" : ",description=$4"} WHERE id=$1 AND revision=$2 RETURNING id`,
          file ? [key, rev, d.name] : [key, rev, d.name, d.description],
        );
        if (!r.rowCount) reject("conflict");
        return r.rows[0];
      }
    }
    if (change && chunk) {
      const edit = "body" in change;
      const text = edit ? change.body.normalize("NFC") : "";
      const r = await db.query(
        `UPDATE web.knowledge_chunk SET ${edit ? "body=$4,section_hash=$5,embedding=$6::public.vector" : "enabled=$4"},revision=revision+1 WHERE id=$1 AND file_id=$2 AND revision=$3 RETURNING id`,
        edit
          ? [chunk, file, change.revision, text, hash(text), JSON.stringify(embedding)]
          : [chunk, file, change.revision, change.enabled],
      );
      if (!r.rowCount) reject("conflict");
      return r.rows[0];
    }
    reject("not_found");
  });
  return jsonResponse(result.value, result.viewRevision);
}
