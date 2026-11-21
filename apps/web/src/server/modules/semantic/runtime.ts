import { z } from "zod";
import {
  id,
  text,
  queryParams,
  type MetricView,
  type SemanticCapability,
} from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import type { Context } from "./access";
import { digest, policies, published, releases } from "./catalog";
import { normalize as normalizeStoredName } from "./drafts";
import { fullTextMatches, relatedMetrics } from "./retrieval";

// Typesetting spaces around Han text are optional in mixed Chinese/Latin metric names.
function normalize(value: string) {
  return normalizeStoredName(value).replace(
    /(?<=\p{Script=Han}) (?=[\p{L}\p{N}])|(?<=[\p{L}\p{N}]) (?=\p{Script=Han})/gu,
    "",
  );
}

export async function restrictRuntime(c: Context, releaseIds?: string[]): Promise<Context> {
  const live = await releases(c);
  const fixed = releaseIds ? await releases(c, releaseIds) : [];
  const applicable = await policies(c, [...live, ...fixed]);
  const disabled = new Set(applicable.flatMap((p) => p.policy.disabled_capabilities ?? []));
  if (!disabled.size) return c;
  const can = (key: SemanticCapability) =>
    c.can(key) && !disabled.has(key) && (key !== "query" || !disabled.has("read"));
  return {
    ...c,
    can,
    scopes: c.scopes.map((s) => ({
      ...s,
      manage: s.manage && can("draft"),
      share: s.share && can("draft"),
      business: s.business && can("govern"),
      data: s.data && can("govern"),
      release: s.release && can("govern"),
    })),
  };
}

const pins = z.array(id).max(3);
const lookup = z.strictObject({
  query: z.string().max(256),
  kinds: z
    .array(z.enum(["term", "metric", "entity"]))
    .max(3)
    .optional(),
  domain: text.optional(),
  scope_filter: id.optional(),
  cursor: z.number().int().min(0).default(0),
  limit: z.number().int().min(1).max(50).default(12),
});
const resolve = z.strictObject({
  mention: text,
  metric_id: id.optional(),
  dimensions: z.array(text).max(8).default([]),
  parameters: queryParams.optional(),
});
const safe = (m: MetricView) => ({
  asset_id: m.asset_id,
  version_id: m.version_id,
  code: m.code,
  name: m.name,
  domain: m.domain,
  kind: m.kind,
  unit: m.unit,
  timezone: m.definition.timezone ?? null,
  grain: m.grain,
  summary: m.summary,
  event_time: m.definition.event_time ?? null,
  known_at: m.definition.known_at ?? null,
  additivity: m.definition.additivity ?? null,
  formula: String(m.definition.formula ?? "").slice(0, 280),
  exceptions: String(m.definition.exceptions ?? "").slice(0, 280),
  aggregation: m.definition.aggregation ?? null,
  dimensions: m.definition.dimensions ?? [],
  filters: m.definition.filters ?? [],
  version: m.version_no,
  owner_scope_id: m.owner_scope_id,
  executable: !!m.binding_id,
});
function matchingNames(m: Pick<MetricView, "name" | "code" | "aliases">, question: string) {
  return [m.code, m.name, ...m.aliases]
    .map(normalize)
    .filter((n) => n && (question === n || (n.length > 1 && question.includes(n))));
}
export function rank(m: Pick<MetricView, "name" | "code" | "summary" | "aliases">, input: string) {
  const q = normalize(input);
  if (!q) return 1;
  const names = [m.code, m.name, ...m.aliases].map(normalize);
  if (names.includes(q)) return 4;
  if (names.some((n) => n.length > 1 && q.includes(n))) return 3;
  return names.some((n) => n.includes(q)) ? 2 : normalize(m.summary).includes(q) ? 1 : 0;
}
const matchReasons = [
  "",
  "定义摘要",
  "名称或别名包含检索词",
  "提问包含名称或别名",
  "名称、代码或别名完全匹配",
];
function searchHit(
  m: MetricView,
  query: string,
  fullText: Map<string, number>,
  related: Set<string>,
) {
  if (!query.trim()) return { m, rank: 1, reason: "可见目录" };
  const direct = rank(m, query);
  if (direct) return { m, rank: direct, reason: matchReasons[direct] };
  const score = fullText.get(m.version_id);
  if (score !== undefined) return { m, rank: 0.6 + Math.min(score, 0.3), reason: "全文词项匹配" };
  if (related.has(m.asset_id)) return { m, rank: 0.5, reason: "相关名称和定义" };
}
async function resolveMetric(
  c: Context,
  operation: string,
  f: z.infer<typeof resolve>,
  all: MetricView[],
  policy: Awaited<ReturnType<typeof policies>>,
  manifest_hash: string,
) {
  let direct =
    operation === "choice" && f.metric_id
      ? all.filter((m) => m.asset_id === f.metric_id)
      : all.filter((m) => rank(m, f.mention) >= 3);
  let mention: string | undefined;
  if (operation !== "choice" && direct.length) {
    const question = normalize(f.mention);
    const matchedNames = direct.flatMap((m) => matchingNames(m, question));
    const specific = [...new Set(matchedNames)].filter(
      (n) => !matchedNames.some((other) => other !== n && other.includes(n)),
    );
    const metricTerms = new Set(
      direct.filter((m) => m.kind !== "entity").flatMap((m) => matchingNames(m, question)),
    );
    const exact = specific.filter((n) => metricTerms.has(n));
    if (exact.length === 1) mention = exact[0];
    direct = direct.filter((m) =>
      [m.code, m.name, ...m.aliases].map(normalize).some((n) => specific.includes(n)),
    );
  }
  const terms = direct.filter((m) => m.kind === "term").map((m) => m.asset_id);
  const { rows: links } = await c.db.query<{ to_asset_id: string }>(
    "SELECT to_asset_id FROM web.semantic_relations WHERE kind='denotes' AND from_version_id=ANY($1::uuid[]) AND scope_id=ANY($2::uuid[])",
    [
      direct.filter((m) => m.kind === "term").map((m) => m.version_id),
      c.scopes.filter((s) => s.use).map((s) => s.id),
    ],
  );
  const targets = new Set([...direct.map((m) => m.asset_id), ...links.map((l) => l.to_asset_id)]);
  const visible = all.filter((m) => m.kind === "metric" && targets.has(m.asset_id));
  let matches = visible.filter((m) => m.binding_id);
  const family = matches.map((m) => m.asset_id);
  if (operation !== "choice") {
    const specificity: Record<string, number> = {
      platform: 0,
      tenant: 1,
      workspace: 2,
      department: 2,
      job: 2,
      personal: 3,
    };
    const candidates = policy.flatMap((p) =>
      (p.policy.defaults ?? [])
        .filter(
          (d) =>
            terms.includes(d.term_ref) && matches.some((m) => m.asset_id === d.target_metric_ref),
        )
        .map((d) => ({
          id: d.target_metric_ref,
          priority: specificity[c.scopes.find((s) => s.id === p.id)?.kind ?? "platform"],
        })),
    );
    const nearest = Math.max(-1, ...candidates.map((c) => c.priority));
    const defaults = new Set(candidates.filter((c) => c.priority === nearest).map((c) => c.id));
    const preferred = matches.filter((m) => defaults.has(m.asset_id));
    if (preferred.length) matches = preferred;
  }
  if (!matches.length)
    return { status: visible.length ? "unavailable_binding" : "no_match", mention, manifest_hash };
  if (matches.length > 1)
    return { status: "needs_clarification", mention, options: matches.map(safe), manifest_hash };
  const [m] = matches;
  if (
    f.dimensions.some(
      (d) => !Array.isArray(m.definition.dimensions) || !m.definition.dimensions.includes(d),
    )
  )
    return { status: "invalid_dimension" };
  return {
    status: "resolved",
    mention,
    family,
    metric: safe(m),
    binding_id: m.binding_id,
    manifest_hash,
  };
}
export async function registry(c: Context, operation: string, raw: unknown, releaseIds?: string[]) {
  const fixed = await releases(c, releaseIds ? pins.parse(releaseIds) : undefined);
  const all = await published(c, releaseIds);
  const policy = await policies(c, fixed);
  const manifest_hash = digest(fixed.map((r) => [r.partition, r.id, r.digest]));
  if (operation === "authorize") {
    const { refs } = z
      .strictObject({
        refs: z
          .array(z.strictObject({ asset_id: id, version_id: id, binding_id: id.optional() }))
          .min(1)
          .max(64),
      })
      .parse(raw);
    for (const ref of refs) {
      const m = all.find((m) => m.asset_id === ref.asset_id && m.version_id === ref.version_id);
      if (!m || (ref.binding_id && m.binding_id !== ref.binding_id)) reject("forbidden");
    }
    const { rows } = await c.db.query(
      "SELECT binding_id,source_ref,target_hash,sql_text,parameter_schema,result_schema,access_requirements FROM web.execution_bindings WHERE tenant_id=$1 AND workspace_id=$2 AND binding_id=ANY($3::uuid[])",
      [
        c.target.tenantId,
        c.target.workspaceId,
        refs.flatMap((r) => (r.binding_id ? [r.binding_id] : [])),
      ],
    );
    return { bindings: rows };
  }
  if (operation === "manifest" || operation === "context") {
    const pinned = new Set(policy.flatMap((p) => p.policy.pinned_asset_ids ?? []));
    const maxItems = Math.min(8, ...policy.map((p) => p.policy.context_max_items ?? 8));
    const { question } = z.object({ question: z.string().max(2400).optional() }).parse(raw ?? {});
    const intent = question
      ? await resolveMetric(
          c,
          "resolve",
          { mention: question, dimensions: [] },
          all,
          policy,
          manifest_hash,
        )
      : undefined;
    const normalizedQuestion = normalize(question ?? "");
    const lexical =
      question && !intent?.mention
        ? relatedMetrics(all, question)
        : { suggestions: [], byClause: [] };
    const metricNames = new Set(
      all.filter((m) => m.kind !== "entity").flatMap((m) => matchingNames(m, normalizedQuestion)),
    );
    const explicitNames = [...metricNames].filter(
      (name) => ![...metricNames].some((other) => name !== other && other.includes(name)),
    );
    const requested = new Map<string, { asset_id: string; code: string; name: string }>();
    if (explicitNames.length > 1 && explicitNames.length <= maxItems) {
      for (const name of explicitNames) {
        const match = await resolveMetric(
          c,
          "resolve",
          { mention: name, dimensions: [] },
          all,
          policy,
          manifest_hash,
        );
        if (match.status === "resolved" && match.metric) {
          const { asset_id, code, name } = match.metric;
          requested.set(asset_id, { asset_id, code, name });
        }
      }
    }
    if (!intent?.mention && lexical.byClause.length > 1 && lexical.byClause.length <= maxItems) {
      for (const { asset_id, code, name } of lexical.byClause)
        requested.set(asset_id, { asset_id, code, name });
    }
    const strength = (v: MetricView) =>
      Math.max(0, ...matchingNames(v, normalizedQuestion).map((n) => n.length));
    const matched = question
      ? all
          .filter((v) => rank(v, question) >= 3)
          .sort(
            (a, b) =>
              strength(b) - strength(a) ||
              rank(b, question) - rank(a, question) ||
              a.asset_id.localeCompare(b.asset_id),
          )
          .map((v) => v.asset_id)
      : [];
    return {
      manifest_hash,
      releases: fixed.map((r) => r.id),
      requested_metrics: [...requested.values()],
      resolution: intent
        ? {
            status: intent.status,
            mention: intent.mention,
            metric: intent.metric,
            family: intent.family,
            option_count: intent.options?.length,
          }
        : undefined,
      items: [
        ...new Set([
          ...requested.keys(),
          ...matched,
          ...lexical.suggestions.map((v) => v.asset_id),
          ...pinned,
        ]),
      ]
        .flatMap((id) => {
          const v = all.find((a) => a.asset_id === id);
          return v ? [{ ...safe(v), matched_terms: matchingNames(v, normalizedQuestion) }] : [];
        })
        .slice(0, maxItems),
      max_search_rounds: Math.min(8, ...policy.map((p) => p.policy.max_search_rounds ?? 8)),
      capabilities: {
        read: c.can("read"),
        query: c.can("query"),
        draft: c.can("draft"),
        govern: c.can("govern"),
      },
    };
  }
  if (operation === "search") {
    const f = lookup.parse(raw);
    const related = new Set(relatedMetrics(all, f.query).suggestions.map((v) => v.asset_id));
    const visible = all.filter(
      (m) =>
        (!f.kinds || f.kinds.includes(m.kind as "term" | "metric" | "entity")) &&
        (!f.domain || m.domain === f.domain) &&
        (!f.scope_filter || m.usage_scope_id === f.scope_filter),
    );
    const fullText = await fullTextMatches(c, f.query, visible);
    const matches = visible
      .map((m) => searchHit(m, f.query, fullText, related))
      .filter((hit) => hit !== undefined)
      .sort((a, b) => b.rank - a.rank || a.m.asset_id.localeCompare(b.m.asset_id));
    const limit = Math.min(f.limit, ...policy.map((p) => p.policy.page_size ?? 50));
    return {
      items: matches.slice(f.cursor, f.cursor + limit).map(({ m, reason }) => ({
        ...safe(m),
        scope_label: c.scopes.find((s) => s.id === m.usage_scope_id)?.label,
        match_reason: reason,
      })),
      cursor: matches.length > f.cursor + limit ? f.cursor + limit : null,
      manifest_hash,
    };
  }
  if (operation === "resolve" || operation === "choice")
    return resolveMetric(c, operation, resolve.parse(raw), all, policy, manifest_hash);
  const f = z
    .strictObject({
      asset_id: id,
      version_id: id.optional(),
      binding_id: id.optional(),
      reference_id: id.optional(),
      reference_kind: z.enum(["query", "report"]).optional(),
    })
    .parse(raw);
  const m = all.find(
    (m) => m.asset_id === f.asset_id && (!f.version_id || m.version_id === f.version_id),
  );
  if (!m) reject("not_found");
  if (operation === "definition")
    return {
      ...safe(m),
      definition: m.definition,
      aliases: m.aliases,
      dependencies: m.dependencies.filter((d) => all.some((p) => p.asset_id === d)),
      manifest_hash,
    };
  if (!m.binding_id || !c.can("query") || m.binding_id !== f.binding_id) reject("forbidden");
  if (operation === "reference") {
    if (!f.reference_id || !f.reference_kind) reject("validation_failed");
    await c.db.query(
      "INSERT INTO web.semantic_reference VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING",
      [c.target.workspaceId, m.version_id, f.reference_kind, f.reference_id],
    );
    return { ok: true };
  }
  if (operation !== "binding") reject("not_found");
  const {
    rows: [b],
  } = await c.db.query(
    "SELECT binding_id,version_id,mode,source_ref,target_hash,sql_text,parameter_schema,result_schema,access_requirements FROM web.execution_bindings WHERE binding_id=$1 AND version_id=$2 AND tenant_id=$3 AND workspace_id=$4",
    [f.binding_id, m.version_id, c.target.tenantId, c.target.workspaceId],
  );
  if (!b) reject("forbidden");
  return {
    binding: b,
    metric: safe(m),
    aggregation: m.definition.additivity ?? m.definition.aggregation,
    manifest_hash,
  };
}
