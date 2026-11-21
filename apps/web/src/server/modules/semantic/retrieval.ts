import type { MetricView } from "../../../contracts/semantic";
import type { Context } from "./access";

export async function fullTextMatches(c: Context, query: string, visible: MetricView[]) {
  if (!query.trim() || !visible.length) return new Map<string, number>();
  const { rows } = await c.db.query<{ version_id: string; score: number }>(
    `WITH q AS (SELECT plainto_tsquery('simple'::regconfig, $1) AS words),
     hits AS (
       SELECT v.version_id, ts_rank(to_tsvector('simple'::regconfig, v.name || ' ' || v.summary), q.words) AS score
       FROM web.semantic_versions v CROSS JOIN q
       WHERE v.version_id = ANY($2::uuid[]) AND v.usage_scope_id = ANY($3::uuid[])
         AND to_tsvector('simple'::regconfig, v.name || ' ' || v.summary) @@ q.words
       UNION ALL
       SELECT a.origin_version_id, ts_rank(to_tsvector('simple'::regconfig, a.display_alias), q.words)
       FROM web.semantic_aliases a CROSS JOIN q
       WHERE a.origin_version_id = ANY($2::uuid[]) AND a.scope_id = ANY($3::uuid[])
         AND to_tsvector('simple'::regconfig, a.display_alias) @@ q.words)
     SELECT version_id, max(score) AS score FROM hits GROUP BY version_id`,
    [query, visible.map((m) => m.version_id), c.scopes.filter((s) => s.use).map((s) => s.id)],
  );
  return new Map(rows.map((row) => [row.version_id, row.score]));
}

// Chinese names are often separated by filters or grouping words in natural questions.
// Index adjacent characters and whole Latin words from the authorized catalogue only.
function terms(value: string) {
  const keys = new Set<string>();
  for (const word of value
    .normalize("NFKC")
    .toLowerCase()
    .match(/[\p{Script=Han}]+|[a-z][a-z0-9_]*/gu) ?? []) {
    if (/\p{Script=Han}/u.test(word)) {
      const chars = [...word];
      for (let i = 1; i < chars.length; i++) keys.add(chars[i - 1] + chars[i]);
    } else if (word.length > 1) keys.add(word);
  }
  return keys;
}

export function relatedMetrics(items: MetricView[], question: string) {
  const metrics = items.filter((m) => m.kind === "metric");
  const frequency = new Map<string, number>();
  const entries = metrics.map((metric) => {
    const names = terms([metric.name, metric.code, ...metric.aliases].join(" "));
    const description = terms(
      [metric.summary, metric.definition.formula, metric.definition.exceptions].join(" "),
    );
    for (const term of new Set([...names, ...description])) {
      frequency.set(term, (frequency.get(term) ?? 0) + 1);
    }
    return { metric, names, description };
  });
  const suggestions = new Map<string, MetricView>();
  const explicit = new Map<string, MetricView>();
  for (const clause of question.split(/[。！？；，、,;!?\n]+/u)) {
    const words = [...terms(clause)];
    const matches = entries
      .flatMap(({ metric, names, description }) => {
        const hits = words.filter((w) => names.has(w) || description.has(w));
        if (!hits.some((w) => names.has(w)) || hits.length < 2) return [];
        const score = hits.reduce((total, w) => {
          const weight = Math.log1p(metrics.length / (frequency.get(w) ?? 1));
          return total + weight * (names.has(w) ? 2 : 1);
        }, 0);
        const distinctive = hits.some((w) => frequency.get(w) === 1);
        return [{ metric, score, distinctive }];
      })
      .sort((a, b) => b.score - a.score || a.metric.asset_id.localeCompare(b.metric.asset_id));
    for (const { metric } of matches.slice(0, 3)) suggestions.set(metric.asset_id, metric);
    const best = matches[0];
    if (
      best?.distinctive &&
      best.score > (matches[1]?.score ?? 0) * 1.15 &&
      best.metric.binding_id
    ) {
      explicit.set(best.metric.asset_id, best.metric);
    }
  }
  return { suggestions: [...suggestions.values()], byClause: [...explicit.values()] };
}
