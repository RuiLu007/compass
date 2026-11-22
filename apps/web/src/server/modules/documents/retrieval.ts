import "server-only";
import type { SqlClient } from "../../platform/db";
import type { TrustedModelContext } from "../models/public";

export type KnowledgeMatch = {
  baseId: string;
  baseName: string;
  tenantName: string;
  workspaceName: string;
  fileId: string;
  fileName: string;
  chunkId: string;
  ordinal: number;
  heading: string;
  body: string;
  similarity: number;
};

// Use the same pgvector cosine distance and active chunks as Agentic Search.
export async function retrieveKnowledge(
  db: SqlClient,
  vector: number[],
  context: TrustedModelContext,
  baseId: string | null,
) {
  const scope = context.scope;
  const { rows } = await db.query<KnowledgeMatch>(
    `SELECT b.id AS "baseId",b.name AS "baseName",t.name AS "tenantName",
      w.name AS "workspaceName",f.id AS "fileId",f.name AS "fileName",
      c.id AS "chunkId",c.ordinal,c.heading,c.body,
      1 - (c.embedding OPERATOR(public.<=>) $1::public.vector) AS similarity
    FROM web.knowledge_chunk c
    JOIN web.knowledge_file f ON f.id=c.file_id
    JOIN web.knowledge_base b ON b.id=f.base_id
    JOIN web.tenant t ON t.id=b.tenant_id
    JOIN web.workspace w ON w.id=b.workspace_id AND w.tenant_id=b.tenant_id
    WHERE NOT b.deleted AND NOT f.deleted AND f.status='ready' AND c.enabled
      AND ($2::uuid IS NULL OR b.tenant_id=$2)
      AND ($3::uuid IS NULL OR b.workspace_id=$3)
      AND ($4::uuid IS NULL OR b.id=$4)
    ORDER BY c.embedding OPERATOR(public.<=>) $1::public.vector,c.id LIMIT 10`,
    [
      JSON.stringify(vector),
      scope.kind === "tenant" ? scope.tenant_id : null,
      scope.kind === "tenant" ? scope.workspace_id : null,
      baseId,
    ],
  );
  return rows;
}
