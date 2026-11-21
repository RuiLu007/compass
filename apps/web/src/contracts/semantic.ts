import { z } from "zod";

export const id = z.uuid();
export const text = z.string().trim().min(1).max(160);
const short = z.string().trim().max(160);
const note = z.string().trim().max(2400);
const names = z.array(text).max(40);
export const scopeParams = z.strictObject({ tenantId: id, workspaceId: id });
export const semanticCapability = z.enum(["read", "query", "draft", "govern"]);
export type SemanticCapability = z.infer<typeof semanticCapability>;
export const metricFields = z.strictObject({
  name: text,
  summary: note.default(""),
  code: z.string().regex(/^[a-z][a-z0-9_.-]{0,119}$/),
  domain: text,
  kind: z.enum(["metric", "term", "entity"]).default("metric"),
  responsible: short.default(""),
  grain: short.default(""),
  unit: short.default(""),
  formula: note.default(""),
  event_time: short.default(""),
  timezone: short.default(""),
  aggregation: short.default(""),
  dimensions: names.default([]),
  filters: names.default([]),
  aliases: names.default([]),
  dependencies: z.array(id).max(40).default([]),
  source_id: id.nullish(),
  source_position: short.default(""),
  owner_scope_id: id.optional(),
  usage_scope_id: id.optional(),
  derived_from: id.optional(),
});
export type MetricFields = z.infer<typeof metricFields>;
export type ScopeView = {
  id: string;
  kind: string;
  label: string;
  use: boolean;
  manage: boolean;
  share: boolean;
  business: boolean;
  data: boolean;
  release: boolean;
};
export type MetricView = {
  asset_id: string;
  version_id: string;
  draft_id: string;
  owner_scope_id: string;
  usage_scope_id: string;
  version_no: number;
  name: string;
  summary: string;
  domain: string;
  code: string;
  kind: string;
  grain: string;
  unit: string;
  responsible: string;
  creator: string;
  editor: string;
  digest: string;
  state: string;
  definition: Record<string, unknown>;
  source_id: string | null;
  source_position: string;
  aliases: string[];
  dependencies: string[];
  editable: boolean;
  binding_id: string | null;
  actions: string[];
  generation: number;
  release_id: string | null;
};
export type Finding = {
  field: string;
  message: string;
  suggestion: string;
  severity: "error" | "warning";
};
export const reviewBody = z.strictObject({
  expected_digest: text,
  note,
  request_key: id,
  binding_id: id.optional(),
});
export const releaseBody = reviewBody.extend({
  version_id: id,
  expected_generation: z.number().int().nonnegative(),
  audience_id: id.nullish(),
  mode: z.enum(["stable", "gray"]).default("stable"),
});
export const sourceBody = z.strictObject({
  request_key: id,
  owner_scope_id: id.optional(),
  kind: z.enum(["schema", "sql", "bi", "report", "document"]),
  locator: text,
  version: text,
  excerpt: note,
  priority: z.enum(["P0", "P1", "P2"]),
  responsible: text,
});
export const draftBody = metricFields.extend({
  request_key: id,
  expected_digest: text.optional(),
  asset_id: id.optional(),
});
export const queryParams = z.strictObject({
  start: z.iso.date(),
  end: z.iso.date(),
  as_of: z.iso.datetime({ offset: true }),
  group_by: short.optional(),
  filters: z.record(text, z.union([z.number().finite(), text, z.boolean()])).optional(),
});
