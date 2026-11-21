import { allowed, type CurrentAccess } from "../access/public";
import type { Binding, Collection, Document, Pin, SourceRef, SearchArgs } from "./contracts";

export function businessDay(pin: Pin) {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: pin.timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(pin.knownAt));
}

type Corpus = { access: CurrentAccess; pin: Pin; current: Binding; collection: Collection };
function readable({ access, pin, current, collection }: Corpus, d: Document, date: string) {
  const policy = Object.hasOwn(current.policies, d.docId) ? current.policies[d.docId] : undefined;
  const k = Date.parse(pin.knownAt);
  return (
    access.memberId !== null &&
    collection.tenantId === pin.tenantId &&
    current.tenantId === pin.tenantId &&
    current.workspaceId === pin.workspaceId &&
    allowed(access, "module.analysis", pin) &&
    pin.kinds.includes(d.kind) &&
    current.kinds.includes(d.kind) &&
    policy?.enabled &&
    [d.audienceWorkspaceId, policy.audienceWorkspaceId].every(
      (group) => group === null || access.workspacePermissions.has(group),
    ) &&
    Date.parse(d.publishedAt) <= k &&
    (!d.requiresApproval || (d.approvedAt !== null && Date.parse(d.approvedAt) <= k)) &&
    d.validFrom <= date &&
    (d.validTo === null || d.validTo > date)
  );
}
function describe(pin: Pin, d: Document, index: number, date: string) {
  const s = d.sections[index];
  const sourceRef: SourceRef = {
    kind: "document",
    runId: pin.runId,
    tenant: pin.tenantId,
    workspace: pin.workspaceId,
    collectionDigest: pin.collectionDigest,
    docId: d.docId,
    version: d.version,
    sectionId: s.sectionId,
    sectionHash: s.sectionHash,
    businessDate: date,
  };
  return {
    sourceRef,
    kind: d.kind,
    docId: d.docId,
    version: d.version,
    title: d.title,
    issuer: d.issuer,
    publishedAt: d.publishedAt,
    approvedAt: d.approvedAt,
    validFrom: d.validFrom,
    validTo: d.validTo,
    heading: s.heading,
    sectionId: s.sectionId,
    sectionHash: s.sectionHash,
  };
}
function words(value: string) {
  const parts =
    value
      .normalize("NFKC")
      .toLowerCase()
      .match(/[\p{Script=Han}]+|[a-z0-9]+(?:-[a-z0-9]+)*/gu) ?? [];
  return parts.flatMap((p) => {
    const chars = [...p];
    return /\p{Script=Han}/u.test(p) && chars.length > 1
      ? chars.slice(1).map((_, i) => chars[i] + chars[i + 1])
      : [p];
  });
}
function field(value: string) {
  const tokens = words(value);
  const counts = new Map<string, number>();
  for (const t of tokens) counts.set(t, (counts.get(t) ?? 0) + 1);
  return { length: tokens.length, counts };
}
export function searchDocuments(c: Corpus, args: SearchArgs) {
  const date = args.businessDate ?? businessDay(c.pin);
  const visible = c.collection.documents.filter(
    (d) => readable(c, d, date) && (!args.kind || args.kind === d.kind),
  );
  const query = [args.query, args.entity ?? ""].join(" ").normalize("NFKC").toLowerCase();
  const expanded = Object.entries(c.collection.aliases)
    .filter(([alias]) => query.includes(alias))
    .map(([, v]) => v);
  const terms = new Set(words([query, ...expanded].join(" ")));
  const rows = visible.flatMap((d) =>
    d.sections.map((s, index) => ({
      d,
      s,
      index,
      fields: [
        field(d.docId),
        field(d.title + " " + s.heading),
        field(d.aliases.join(" ")),
        field(s.text),
      ],
    })),
  );
  const weights = [6, 3, 2, 1];
  const averages = weights.map(
    (_, i) => rows.reduce((n, r) => n + r.fields[i].length, 0) / rows.length || 1,
  );
  const scores = rows.map((r) => (query.includes(r.d.docId.toLowerCase()) ? 1000 : 0));
  for (const term of terms) {
    const df = rows.filter((r) => r.fields.some((f) => f.counts.has(term))).length;
    const idf = Math.log1p((rows.length - df + 0.5) / (df + 0.5));
    rows.forEach((row, i) =>
      row.fields.forEach((f, j) => {
        const tf = f.counts.get(term) ?? 0;
        scores[i] +=
          (weights[j] * idf * tf * 2.2) / (tf + 1.2 * (0.25 + (0.75 * f.length) / averages[j]));
      }),
    );
  }
  const ranked = rows
    .map((row, i) => ({ ...row, score: scores[i] }))
    .filter((r) => r.score > 0)
    .sort(
      (a, b) =>
        b.score - a.score ||
        a.d.docId.localeCompare(b.d.docId) ||
        a.d.version.localeCompare(b.d.version) ||
        a.s.sectionId.localeCompare(b.s.sectionId),
    );
  // When two effective editions disagree on the same clause, keep the counterpart
  // beside the hit so that lower-ranked sections cannot crowd it out of the preview.
  const selected = new Set<(typeof ranked)[number]>();
  for (const hit of ranked) {
    selected.add(hit);
    if (selected.size === 3) break;
    for (const peer of ranked) {
      if (
        peer.d.docId === hit.d.docId &&
        peer.d.version !== hit.d.version &&
        peer.s.heading === hit.s.heading &&
        peer.s.sectionHash !== hit.s.sectionHash
      )
        selected.add(peer);
      if (selected.size === 3) break;
    }
    if (selected.size === 3) break;
  }
  return [...selected].map(({ d, s, index }) => ({
    ...describe(c.pin, d, index, date),
    snippet: [...s.text].slice(0, 180).join(""),
    aliases: d.aliases.slice(0, 8),
  }));
}
export function readDocument(c: Corpus, ref: SourceRef, offset = 0) {
  if (
    ref.tenant !== c.pin.tenantId ||
    ref.workspace !== c.pin.workspaceId ||
    ref.runId !== c.pin.runId ||
    ref.collectionDigest !== c.pin.collectionDigest
  )
    return [];
  const doc = c.collection.documents.find(
    (d) => d.docId === ref.docId && d.version === ref.version && readable(c, d, ref.businessDate),
  );
  if (!doc) return [];
  const index = doc.sections.findIndex(
    (s) => s.sectionId === ref.sectionId && s.sectionHash === ref.sectionHash,
  );
  const target = index + offset;
  if (index < 0 || !doc.sections[target]) return [];
  return [{ ...describe(c.pin, doc, target, ref.businessDate), text: doc.sections[target].text }];
}
