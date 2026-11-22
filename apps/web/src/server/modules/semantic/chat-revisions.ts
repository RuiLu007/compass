import { z } from "zod";
import { id, metricFields, text } from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import { ensureShare, type Context } from "./access";
import { version } from "./catalog";
import { idempotent, saveDraft } from "./drafts";
import { list } from "./service";

const editable = metricFields.pick({
  name: true,
  summary: true,
  unit: true,
  grain: true,
  formula: true,
  event_time: true,
  timezone: true,
  aggregation: true,
  responsible: true,
});
const detail = z.string().trim().min(1).max(2400);
const patch = z
  .strictObject({
    name: text.optional(),
    summary: detail.optional(),
    unit: text.optional(),
    grain: text.optional(),
    formula: detail.optional(),
    event_time: text.optional(),
    timezone: text.optional(),
    aggregation: text.optional(),
    responsible: text.optional(),
  })
  .refine((v) => Object.keys(v).length > 0);
const preview = z.strictObject({ metric: text, changes: patch, expected_digest: text.optional() });
const commit = z.strictObject({
  asset_id: id,
  expected_digest: text,
  changes: patch,
  request_key: id,
});

async function target(c: Context, selector: string, expected?: string) {
  if (!c.can("draft")) reject("forbidden");
  const matches = (await list(c)).filter(
    (m) => m.kind === "metric" && m.editable && [m.asset_id, m.code, m.name].includes(selector),
  );
  if (!matches.length) reject("not_found");
  if (matches.length !== 1) reject("conflict", "有多个同名指标，请指定唯一的指标代码或资产 ID。");
  const m = matches[0];
  ensureShare(c, m.owner_scope_id, m.usage_scope_id);
  if (!["published", "draft", "returned"].includes(m.state) || (expected && m.digest !== expected))
    reject("conflict");
  return version(c, m.version_id);
}

function values(m: Awaited<ReturnType<typeof target>>) {
  return editable.parse({
    name: m.name,
    summary: m.summary,
    grain: m.grain,
    unit: m.unit,
    formula: m.definition.formula,
    event_time: m.definition.event_time,
    timezone: m.definition.timezone,
    aggregation: m.definition.aggregation,
    responsible: m.definition.responsible ?? m.responsible,
  });
}

export async function lookupRevision(c: Context, raw: unknown) {
  const f = preview.parse(raw);
  if (!c.can("draft")) reject("forbidden");
  const editable = (await list(c)).filter((m) => m.kind === "metric" && m.editable);
  const key = f.metric.trim().normalize("NFKC").toLowerCase();
  const names = (m: (typeof editable)[number]) =>
    [m.asset_id, m.name, m.code, ...m.aliases].map((n) => n.normalize("NFKC").toLowerCase());
  const exact = editable.filter((m) => names(m).includes(key));
  if (exact.length === 1) return previewRevision(c, { ...f, metric: exact[0].asset_id });
  const related = exact.length
    ? exact
    : editable.filter((m) => names(m).some((n) => n.includes(key) || key.includes(n)));
  const options = (related.length ? related : editable).slice(0, 32).map((m) => ({
    asset_id: m.asset_id,
    code: m.code,
    name: m.name,
    summary: m.summary,
    unit: m.unit,
  }));
  return { status: options.length ? "choose_revision_target" : "no_match", options };
}

export async function previewRevision(c: Context, raw: unknown) {
  const f = preview.parse(raw);
  const m = await target(c, f.metric, f.expected_digest);
  const before = values(m);
  const changes = Object.entries(f.changes).flatMap(([field, after]) => {
    const prior = before[field as keyof typeof before];
    return prior === after ? [] : [{ field, before: prior, after }];
  });
  return { asset_id: m.asset_id, code: m.code, name: m.name, expected_digest: m.digest, changes };
}

export async function saveRevision(c: Context, raw: unknown) {
  const f = commit.parse(raw);
  if (!c.can("draft")) reject("forbidden");
  return idempotent(c, f.request_key, "drafts/chat-revision", f, async () => {
    const m = await target(c, f.asset_id, f.expected_digest);
    return saveDraft(c, {
      ...values(m),
      ...f.changes,
      asset_id: m.asset_id,
      expected_digest: m.digest,
      code: m.code,
      domain: m.domain,
      kind: m.kind,
      aliases: m.aliases,
      dependencies: m.dependencies,
      dimensions: m.definition.dimensions,
      filters: m.definition.filters,
      source_id: m.source_id,
      source_position: m.source_position,
      request_key: f.request_key,
    });
  });
}
