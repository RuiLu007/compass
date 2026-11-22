import { createHash } from "node:crypto";
import { z } from "zod";

const name = z.string().trim().min(1).max(240);
const date = z.iso.date();
const instant = z.iso.datetime({ offset: true });
const digest = z.string().regex(/^[a-f0-9]{64}$/);
export const docKind = z.enum(["knowledge", "history", "external"]);
const sourceKind = z.enum(["semantic", "detail", "knowledge", "history", "external"]);
const audience = z.uuid().nullable();
export const hash = (text: string | Buffer) => createHash("sha256").update(text).digest("hex");
const section = z
  .strictObject({
    sectionId: name,
    heading: name,
    text: z.string().min(1).max(8192),
    sectionHash: digest,
  })
  .refine((s) => s.text === s.text.normalize("NFC") && hash(s.text) === s.sectionHash);
const unique = (values: string[]) => new Set(values).size === values.length;
const doc = z
  .strictObject({
    kind: docKind,
    workspaceReference: z.literal(true).optional(),
    docId: name,
    version: name,
    title: name,
    issuer: name,
    publishedAt: instant,
    requiresApproval: z.boolean(),
    approvedAt: instant.nullable(),
    validFrom: date,
    validTo: date.nullable(),
    audienceWorkspaceId: audience,
    aliases: z.array(name).max(64),
    sections: z.array(section).min(1).max(256),
  })
  .refine(
    (d) =>
      (d.validTo === null || d.validTo > d.validFrom) && unique(d.sections.map((s) => s.sectionId)),
  );
export const collection = z
  .strictObject({
    schemaVersion: z.literal(1),
    tenantId: z.uuid(),
    aliases: z.record(name, name),
    documents: z.array(doc).max(512),
  })
  .refine((c) => unique(c.documents.map((d) => JSON.stringify([d.docId, d.version]))));
export const binding = z.strictObject({
  tenantId: z.uuid(),
  workspaceId: z.uuid(),
  knownAt: instant,
  timezone: name.refine((t) => {
    try {
      new Intl.DateTimeFormat("en", { timeZone: t });
      return true;
    } catch {
      return false;
    }
  }),
  collectionDigest: digest,
  connectionId: z.uuid().optional(),
  connectionRevision: z.number().int().positive().optional(),
  dataRelease: name,
  maxToolCalls: z.number().int().min(0).max(400).optional(),
  kinds: z.array(sourceKind).max(5).refine(unique),
  skillEnabled: z.boolean(),
  reportFormat: z.literal("comparison_brief").optional(),
  vectorIndex: z.literal("text-embedding-3-small:1024:v1").optional(),
  scenePack: z.literal("retail").optional(),
  policies: z.record(name, z.strictObject({ enabled: z.boolean(), audienceWorkspaceId: audience })),
});
export const pinSchema = binding
  .omit({ policies: true })
  .extend({ runId: z.uuid(), actorUserId: z.uuid() });
export const sourceRef = z.strictObject({
  kind: z.literal("document"),
  runId: z.uuid(),
  tenant: z.uuid(),
  workspace: z.uuid(),
  collectionDigest: digest,
  docId: name,
  version: name,
  sectionId: name,
  sectionHash: digest,
  businessDate: date,
});
export const searchArgs = z
  .strictObject({
    query: z.string().trim().min(1).max(256),
    queryVector: z.array(z.number().finite()).length(1024).optional(),
    kind: docKind.optional(),
    entity: name.optional(),
    businessDate: date.optional(),
    historical: z.boolean().default(false),
  })
  .refine((q) => !q.historical || q.businessDate !== undefined);
export type Document = z.infer<typeof doc>;
export type Collection = z.infer<typeof collection>;
export type Pin = z.infer<typeof pinSchema>;
export type Binding = z.infer<typeof binding>;
export type SourceRef = z.infer<typeof sourceRef>;
export type SearchArgs = z.infer<typeof searchArgs>;
