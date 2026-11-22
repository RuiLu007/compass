import { afterEach, expect, it, vi } from "vitest";
import type { SqlClient } from "../src/server/platform/db";
import { hash, type Collection } from "../src/server/modules/documents/contracts";
import { currentBinding } from "../src/server/modules/documents/store";
import { parseFile } from "../src/server/modules/documents/indexing";
import { knowledgeCatalog } from "../src/server/modules/documents/library";

vi.mock("server-only", () => ({}));
vi.mock("../src/server/modules/documents/store", () => ({
  currentBinding: vi.fn(),
  loadCollection: vi.fn(),
}));
const scope = {
  tenant_id: "99500000-0000-4000-8000-000000000010",
  workspace_id: "99500000-0000-4000-8000-000000000021",
};
const section = (text: string) => ({
  sectionId: "s1",
  heading: "运输",
  text,
  sectionHash: hash(text),
});
function store(body: string | null): SqlClient {
  return {
    query: vi.fn().mockResolvedValue({
      rows: [
        {
          source_id: "policy-file",
          source_version: "1",
          origin_digest: null,
          name: "运输.txt",
          uploaded: "2026-11-20T12:00:00Z",
          active: true,
          sections: body ? [section(body)] : [],
        },
      ],
    }),
  };
}
afterEach(() => vi.clearAllMocks());

it("pins a document-only workspace and removes stale or switched-off sections from an earlier run", async () => {
  vi.mocked(currentBinding).mockResolvedValue(undefined);
  const initial = await knowledgeCatalog(store("初版"), scope);
  expect(initial.binding.connectionId).toBeUndefined();
  expect(initial.binding.kinds).toEqual(["knowledge"]);
  expect(hash(JSON.stringify(initial.live))).toBe(initial.binding.collectionDigest);
  const pinned: Collection = initial.live;
  const changed = await knowledgeCatalog(store("修订版"), scope);
  expect(changed.authorizedSnapshot(pinned).documents).toEqual([]);
  expect(changed.authorizedSnapshot(changed.live).documents[0].sections[0].text).toBe("修订版");
  const disabled = await knowledgeCatalog(store(null), scope);
  expect(disabled.authorizedSnapshot(changed.live).documents).toEqual([]);
  expect(disabled.live.documents).toEqual([]);
});

it("retains Markdown headings, splits long paragraphs and rejects empty or invalid text", async () => {
  const text = "## 活动规则\n首段\n\n## 例外\n" + "补".repeat(1000);
  const { chunks } = await parseFile("md", Buffer.from(text));
  expect(chunks.map((s) => s.heading)).toEqual(["活动规则", "例外", "例外"]);
  expect(chunks.map((s) => s.body).join("")).toBe("首段" + "补".repeat(1000));
  expect(chunks.every((c) => hash(c.body) === c.hash)).toBe(true);
  await expect(parseFile("txt", Buffer.from([255]))).rejects.toThrow("UTF-8");
  await expect(parseFile("txt", Buffer.from("  "))).rejects.toThrow("没有可提取的正文");
});
