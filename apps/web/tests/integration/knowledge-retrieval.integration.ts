import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import pg from "pg";
import { retrieveKnowledge } from "../../src/server/modules/documents/retrieval";
import type { TrustedModelContext } from "../../src/server/modules/models/public";
vi.mock("server-only", () => ({}));

let db: pg.Client;
const tenant = randomUUID(),
  workspace = randomUUID(),
  otherWorkspace = randomUUID();
const base = randomUUID(),
  otherBase = randomUUID(),
  file = randomUUID();
const vector = [1, ...Array<number>(1023).fill(0)];
const platform: TrustedModelContext = {
  actor_user_id: randomUUID(),
  scope: { kind: "platform" },
  purpose: "manage",
};
const member: TrustedModelContext = {
  ...platform,
  scope: { kind: "tenant", tenant_id: tenant, workspace_id: workspace },
  purpose: "knowledge_manage",
};
beforeAll(async () => {
  const dsn = process.env.WEB_TEST_MIGRATION_DATABASE_URL;
  if (!dsn || !/^\/compass_web_test_[a-z0-9_]+$/.test(new URL(dsn).pathname))
    throw new Error("Dedicated Web test database required");
  db = new pg.Client({ connectionString: dsn });
  await db.connect();
  await db.query("BEGIN");
  await db.query("SET LOCAL ROLE compass_web_owner");
  await db.query("INSERT INTO web.tenant(id,slug,name,timezone) VALUES($1,$2,'检索验收','UTC')", [
    tenant,
    "search-" + tenant.slice(0, 8),
  ]);
  for (const [w, b] of [
    [workspace, base],
    [otherWorkspace, otherBase],
  ]) {
    await db.query("INSERT INTO web.workspace(id,tenant_id,slug,name) VALUES($1,$2,$3,$3)", [
      w,
      tenant,
      "w-" + w.slice(0, 8),
    ]);
    await db.query(
      "INSERT INTO web.knowledge_base(id,tenant_id,workspace_id,name) VALUES($1,$2,$3,'测试库')",
      [b, tenant, w],
    );
  }
  await db.query(
    "INSERT INTO web.knowledge_file(id,base_id,name,format,original,status,source_id) VALUES($1,$2,'rules.txt','txt',$3,'ready','rules')",
    [file, base, Buffer.from("规则")],
  );
  for (const [i, v, enabled] of [
    [1, vector, true],
    [2, [0, 1, ...Array<number>(1022).fill(0)], true],
    [3, vector, false],
  ] as const)
    await db.query(
      "INSERT INTO web.knowledge_chunk(file_id,ordinal,section_id,heading,body,section_hash,embedding,enabled) VALUES($1,$2,$3,'规则','正文','hash',$4,$5)",
      [file, i, "s" + i, JSON.stringify(v), enabled],
    );
  await db
    .query(
      "INSERT INTO web.knowledge_file(base_id,name,format,original,status,source_id) VALUES($1,'other.txt','txt',$2,'ready','other') RETURNING id",
      [otherBase, Buffer.from("其他工作区")],
    )
    .then(async (r) => {
      await db.query(
        "INSERT INTO web.knowledge_chunk(file_id,ordinal,section_id,heading,body,section_hash,embedding) VALUES($1,1,'s1','其他','其他工作区','hash',$2)",
        [r.rows[0].id, JSON.stringify(vector)],
      );
    });
});
afterAll(async () => {
  if (db) {
    await db.query("ROLLBACK");
    await db.end();
  }
});

it("uses cosine ranking, excludes disabled chunks and limits membership to its workspace", async () => {
  const results = await retrieveKnowledge(db, vector, member, null);
  expect(results.map((r) => r.ordinal)).toEqual([1, 2]);
  expect(results[0].similarity).toBeCloseTo(1);
  expect(results[1].similarity).toBeCloseTo(0);
  expect(results[0]).toMatchObject({ baseId: base, fileId: file, heading: "规则", body: "正文" });
  expect(await retrieveKnowledge(db, vector, member, otherBase)).toEqual([]);
  const cross = await retrieveKnowledge(db, vector, platform, otherBase);
  expect(cross).toHaveLength(1);
  expect(cross[0].body).toBe("其他工作区");
});
it("reads the current file and library state on every search", async () => {
  await db.query("UPDATE web.knowledge_file SET status='failed' WHERE id=$1", [file]);
  expect(await retrieveKnowledge(db, vector, member, base)).toEqual([]);
  await db.query("UPDATE web.knowledge_file SET status='ready',deleted=true WHERE id=$1", [file]);
  expect(await retrieveKnowledge(db, vector, member, base)).toEqual([]);
  await db.query("UPDATE web.knowledge_file SET deleted=false WHERE id=$1", [file]);
  await db.query("UPDATE web.knowledge_base SET deleted=true WHERE id=$1", [base]);
  expect(await retrieveKnowledge(db, vector, member, base)).toEqual([]);
});
