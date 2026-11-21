import { randomUUID } from "node:crypto";
import { expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { searchDocuments, readDocument } from "../src/server/modules/documents/search";
import {
  hash,
  type Pin,
  type Document,
  type Binding,
} from "../src/server/modules/documents/contracts";

it("filters editions, current policies and explicit audience memberships before ranking and opening", () => {
  const tenant = randomUUID(),
    workspace = randomUUID(),
    audience = randomUUID();
  const pin: Pin = {
    tenantId: tenant,
    workspaceId: workspace,
    actorUserId: randomUUID(),
    runId: randomUUID(),
    knownAt: "2025-08-05T02:00:00Z",
    timezone: "Asia/Shanghai",
    collectionDigest: "1".repeat(64),
    connectionId: randomUUID(),
    connectionRevision: 1,
    dataRelease: "release",
    kinds: ["knowledge"],
    skillEnabled: true,
  };
  const current: Binding = {
    ...pin,
    policies: { A: { enabled: true, audienceWorkspaceId: audience } },
  };
  const doc: Document = {
    kind: "knowledge",
    docId: "A",
    version: "1",
    title: "入库政策",
    issuer: "采购部",
    publishedAt: "2025-08-01T00:00:00Z",
    requiresApproval: true,
    approvedAt: "2025-08-02T00:00:00Z",
    audienceWorkspaceId: workspace,
    validFrom: "2025-08-01",
    validTo: null,
    aliases: [],
    sections: [
      { sectionId: "receipt", heading: "收货", text: "已入库", sectionHash: hash("已入库") },
    ],
  };
  const c = {
    pin,
    current,
    access: {
      memberId: randomUUID(),
      tenantId: tenant,
      role: "admin" as const,
      authzVersion: "1",
      tenantPermissions: [],
      workspacePermissions: new Map([
        [workspace, []],
        [audience, []],
      ]),
    },
    collection: {
      schemaVersion: 1 as const,
      tenantId: tenant,
      aliases: {},
      documents: [
        doc,
        { ...doc, version: "2", publishedAt: "2025-08-06T00:00:00Z" },
        { ...doc, version: "3", approvedAt: null },
        { ...doc, version: "4", approvedAt: "2025-08-07T00:00:00Z" },
      ],
    },
  };
  const query = { query: "入库", historical: false };
  const results = searchDocuments(c, query);
  expect(results.map((r) => r.version)).toEqual(["1"]);
  const peer = {
    ...doc,
    version: "5",
    sections: [{ ...doc.sections[0], text: "入库待核", sectionHash: hash("入库待核") }],
  };
  expect(
    searchDocuments({ ...c, collection: { ...c.collection, documents: [doc, peer] } }, query)
      .map((r) => r.version)
      .sort(),
  ).toEqual(["1", "5"]);
  const ref = results[0].sourceRef;
  expect(readDocument(c, ref)[0].text).toBe("已入库");
  expect(
    searchDocuments({ ...c, collection: { ...c.collection, tenantId: randomUUID() } }, query),
  ).toEqual([]);
  for (const group of [audience, workspace]) {
    c.access.workspacePermissions.delete(group);
    expect(readDocument(c, ref)).toEqual([]);
    expect(searchDocuments(c, query)).toEqual([]);
    c.access.workspacePermissions.set(group, []);
  }
  c.current.policies.A.enabled = false;
  expect(readDocument(c, ref)).toEqual([]);
  delete c.current.policies.A;
  expect(searchDocuments(c, query)).toEqual([]);
});
