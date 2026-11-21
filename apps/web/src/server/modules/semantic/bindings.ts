import { createHash } from "node:crypto";
import { z } from "zod";
import { id, text } from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import { scope, type Context } from "./access";
import { latest, published, targetPartition, version, type Version } from "./catalog";

const ident = z.string().regex(/^[a-z_][a-z0-9_]{0,62}$/);
const mapping = z.strictObject({
  connection_id: id,
  schema: ident,
  relation: ident,
  measure: ident,
  event_time: ident,
  available_at: ident,
  aggregate: z.enum(["sum", "count", "min", "max", "avg"]),
  dimensions: z.record(ident, ident).default({}),
  filters: z.record(ident, ident).default({}),
  row_policies: z.array(text).min(1).max(12),
  source_release: text,
});
const input = z.strictObject({
  request_key: id,
  version_id: id,
  expected_digest: text,
  reuse_binding_id: id.optional(),
  mapping: mapping.optional(),
});
export function compileBinding(
  v: Pick<Version, "definition" | "unit">,
  m: z.infer<typeof mapping>,
) {
  const q = (s: string) => '"' + ident.parse(s) + '"';
  const literal = (s: string) => "'" + s.replaceAll("'", "''") + "'";
  const dimensions = Object.entries(m.dimensions),
    filters = Object.entries(m.filters);
  if (
    dimensions.length > 16 ||
    filters.length > 16 ||
    dimensions.some(([k]) => !(v.definition.dimensions as string[] | undefined)?.includes(k)) ||
    filters.some(([k]) => !(v.definition.filters as string[] | undefined)?.includes(k))
  )
    reject("validation_failed", "映射包含定义未登记的维度或筛选字段。");
  const zone = String(v.definition.timezone ?? "");
  try {
    new Intl.DateTimeFormat("en", { timeZone: zone });
  } catch {
    reject("validation_failed", "请填写有效业务时区。");
  }
  const group = dimensions.length
    ? "CASE p.dim " +
      dimensions
        .map(([key, col]) => "WHEN " + literal(key) + " THEN f." + q(col) + "::text")
        .join(" ") +
      " ELSE 'total' END"
    : "'total'::text";
  const conditions = filters.map(
    ([key, col]) =>
      "AND (NOT (p.filters ? " +
      literal(key) +
      ") OR f." +
      q(col) +
      "::text=p.filters->>" +
      literal(key) +
      ")",
  );
  const statement = `WITH p AS (SELECT ($1::jsonb->>'start')::date AS d0,($1::jsonb->>'end')::date AS d1,
    ($1::jsonb->>'as_of')::timestamptz AS k,COALESCE($1::jsonb->>'group_by','total') AS dim,
    COALESCE($1::jsonb->'filters','{}') AS filters,$2::jsonb AS authority),
    groups AS (SELECT ${group} AS group_key, ${m.aggregate}(f.${q(m.measure)})::text AS value
    FROM ${q(m.schema)}.${q(m.relation)} f CROSS JOIN p WHERE f.${q(m.event_time)} >= p.d0::timestamp AT TIME ZONE ${literal(zone)}
    AND f.${q(m.event_time)} < p.d1::timestamp AT TIME ZONE ${literal(zone)} AND f.${q(m.available_at)}<=p.k
    ${conditions.join(" ")} GROUP BY 1)
    SELECT g.group_key,g.value,CASE WHEN g.value IS NULL THEN 'NO_DATA' ELSE 'OK' END AS status,
    p.d0 AS period_start,p.d1 AS period_end,p.k AS as_of,${literal(m.source_release)} AS data_release,
    ${literal(zone)} AS business_timezone,${literal(v.unit)} AS unit FROM p LEFT JOIN groups g ON true ORDER BY g.group_key`;
  return {
    mode: "aggregate",
    source_ref: m.connection_id,
    target_ref: m.schema + "." + m.relation,
    sql_text: statement,
    target_hash: createHash("sha256").update(statement).digest("hex"),
    parameter_schema: {
      type: "object",
      additionalProperties: false,
      required: ["start", "end", "as_of"],
      properties: {
        start: { type: "string", format: "date" },
        end: { type: "string", format: "date" },
        as_of: { type: "string", format: "date-time" },
        group_by: { enum: ["total", ...Object.keys(m.dimensions)] },
        filters: {
          type: "object",
          additionalProperties: false,
          properties: Object.fromEntries(
            filters.map(([k]) => [k, { type: ["string", "number", "boolean"] }]),
          ),
        },
      },
    },
    result_schema: {
      type: "object",
      required: [
        "group_key",
        "value",
        "status",
        "unit",
        "as_of",
        "data_release",
        "period_start",
        "period_end",
        "business_timezone",
      ],
      properties: {
        value: { type: ["string", "null"] },
        unit: { const: v.unit },
        status: { enum: ["OK", "NO_DATA"] },
      },
    },
    access_requirements: {
      protocol: "postgres_readonly",
      identity: "verified_session_user",
      namespace_profiles: { managed: ["pg_catalog", m.schema, "pg_temp"] },
      required_relations: [m.relation],
      policy_expressions: { [m.relation]: m.row_policies },
      source_release: m.source_release,
    },
  };
}

export async function addBinding(c: Context, raw: unknown) {
  const f = input.parse(raw),
    v = await version(c, f.version_id);
  const owner = scope(c, v.owner_scope_id);
  const authority =
    owner.kind === "platform" ? await targetPartition(c, owner.id, true, true) : owner;
  if (!authority.manage || !c.can("query") || !c.can("govern")) reject("forbidden");
  if (
    (await latest(c, v.asset_id, true)).version_id !== v.version_id ||
    v.digest !== f.expected_digest
  )
    reject("conflict");
  if (v.kind !== "metric" || !!f.reuse_binding_id === !!f.mapping) reject("validation_failed");
  let b;
  if (f.mapping) b = compileBinding(v, f.mapping);
  else {
    const readable = (await published(c)).find((p) => p.binding_id === f.reuse_binding_id);
    if (!readable || readable.unit !== v.unit || readable.grain !== v.grain)
      reject("validation_failed", "选择单位、粒度一致且已发布的本地执行绑定。");
    const {
      rows: [source],
    } = await c.db.query(
      "SELECT * FROM web.execution_bindings WHERE binding_id=$1 AND tenant_id=$2 AND workspace_id=$3",
      [f.reuse_binding_id, c.target.tenantId, c.target.workspaceId],
    );
    if (!source) reject("not_found");
    b = source;
  }
  const {
    rows: [saved],
  } = await c.db.query(
    `INSERT INTO web.execution_bindings(version_id,tenant_id,workspace_id,source_ref,mode,target_ref,sql_text,target_hash,parameter_schema,result_schema,access_requirements,validation_receipt)
    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING binding_id,version_id`,
    [
      v.version_id,
      c.target.tenantId,
      c.target.workspaceId,
      b.source_ref,
      b.mode,
      b.target_ref,
      b.sql_text,
      b.target_hash,
      b.parameter_schema,
      b.result_schema,
      b.access_requirements,
      { requires_trial: true, author: c.actor.userId },
    ],
  );
  return saved;
}
