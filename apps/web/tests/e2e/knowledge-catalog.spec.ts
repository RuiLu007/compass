import { createHash, randomUUID } from "node:crypto";
import { test, expect, type APIRequestContext } from "@playwright/test";
import pg from "pg";

const headquarters = {
  tenantId: "99500000-0000-4000-8000-000000000010",
  workspaceId: "99500000-0000-4000-8000-000000000020",
};
const ownerScope = { tenantId: randomUUID(), workspaceId: randomUUID() };
const baseId = randomUUID();
const fileId = randomUUID();
const body = "跨租户经营资料验收";
const origin = "http://127.0.0.1:" + (process.env.WEB_TEST_PORT ?? 8110);
let db: pg.Client;

test.beforeAll(async () => {
  const dsn = process.env.WEB_TEST_MIGRATION_DATABASE_URL;
  if (!dsn || !/^\/compass_web_test_[a-z0-9_]+$/.test(new URL(dsn).pathname))
    throw new Error("Dedicated Web test database required");
  db = new pg.Client({ connectionString: dsn });
  await db.connect();
  await db.query("SET ROLE compass_web_owner");
  await db.query("BEGIN");
  try {
    await db.query(
      "INSERT INTO web.tenant(id,slug,name,timezone) VALUES($1,$2,'目录测试租户','UTC')",
      [ownerScope.tenantId, "catalog-" + ownerScope.tenantId.slice(0, 8)],
    );
    await db.query(
      "INSERT INTO web.workspace(tenant_id,id,slug,name) VALUES($1,$2,'north','北区')",
      [ownerScope.tenantId, ownerScope.workspaceId],
    );
    await db.query(
      "INSERT INTO web.knowledge_base(id,tenant_id,workspace_id,name) VALUES($1,$2,$3,'北区资料库')",
      [baseId, ownerScope.tenantId, ownerScope.workspaceId],
    );
    await db.query(
      "INSERT INTO web.knowledge_file(id,base_id,name,format,original,status,source_id) VALUES($1,$2,'政策.txt','txt',$3,'ready','policy')",
      [fileId, baseId, Buffer.from(body)],
    );
    await db.query(
      "INSERT INTO web.knowledge_chunk(file_id,ordinal,section_id,heading,body,section_hash,embedding) VALUES($1,1,'s1','政策',$2,$3,array_fill(1.0::real,ARRAY[1024])::public.vector)",
      [fileId, body, createHash("sha256").update(body).digest("hex")],
    );
    await db.query("COMMIT");
  } catch (error) {
    await db.query("ROLLBACK");
    throw error;
  }
});
test.afterAll(async () => {
  if (!db) return;
  try {
    await db.query("BEGIN");
    await db.query("DELETE FROM web.knowledge_chunk WHERE file_id=$1", [fileId]);
    await db.query("DELETE FROM web.knowledge_file WHERE id=$1", [fileId]);
    await db.query("DELETE FROM web.knowledge_base WHERE id=$1", [baseId]);
    await db.query("DELETE FROM web.workspace WHERE id=$1", [ownerScope.workspaceId]);
    await db.query("DELETE FROM web.tenant WHERE id=$1", [ownerScope.tenantId]);
    await db.query("COMMIT");
  } finally {
    await db.end();
  }
});

async function login(request: APIRequestContext, loginName: string) {
  const response = await request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: { loginName, password: process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password" },
  });
  expect(response.status()).toBe(200);
}
function url(path: string, scope = headquarters) {
  return "/api/web/v1/knowledge/" + path + "?" + new URLSearchParams(scope);
}
const filePath = baseId + "/files/" + fileId;

test("平台管理员从总部读取其他租户的原文与分段，编辑遵循归属工作区权限", async ({ request }) => {
  await login(request, "teaching.platform");
  const list = await request.get(url("catalog"));
  expect(list.status()).toBe(200);
  expect((await list.json()).bases).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        id: baseId,
        ...ownerScope,
        tenantName: "目录测试租户",
        workspaceName: "北区",
        files: 1,
        canManage: false,
      }),
    ]),
  );
  const files = await request.get(url("catalog/" + baseId + "/files"));
  expect(await files.json()).toEqual([expect.objectContaining({ id: fileId })]);
  expect(await (await request.get(url("catalog/" + filePath + "/original"))).text()).toBe(body);
  expect(await (await request.get(url("catalog/" + filePath + "/chunks"))).json()).toEqual([
    expect.objectContaining({ ordinal: 1, body }),
  ]);
  expect(
    (await request.get(url("catalog/" + randomUUID() + "/files/" + fileId + "/original"))).status(),
  ).toBe(404);
  const session = await request.get(
    "/api/web/v1/session?pageId=data-documents&" + new URLSearchParams(headquarters),
  );
  const patch = {
    headers: { origin, "X-CSRF-Token": (await session.json()).csrfToken },
    data: { name: "禁止跨租户改名", revision: 1 },
  };
  expect((await request.patch(url("catalog/" + baseId), patch)).status()).toBe(404);
  expect((await request.patch(url("bases/" + baseId, ownerScope), patch)).status()).toBe(403);
});
test("普通成员无法打开平台目录或使用其文件地址", async ({ request }) => {
  await login(request, "teaching.analyst");
  for (const path of [
    "catalog",
    "catalog/" + filePath + "/original",
    "catalog/" + filePath + "/chunks",
  ]) {
    expect((await request.get(url(path))).status()).toBe(403);
  }
});

test("租户管理员的目录和原文按当前工作区过滤", async ({ request }) => {
  await login(request, "teaching.admin");
  const scoped = await request.get(url("bases"));
  expect(scoped.status()).toBe(200);
  expect(await scoped.json()).not.toContainEqual(expect.objectContaining({ id: baseId }));
  expect((await request.get(url("bases/" + filePath + "/original"))).status()).toBe(404);
  expect((await request.get(url("catalog"))).status()).toBe(403);
});

test("检索测试校验平台权限、工作区归属、查询输入和 CSRF", async ({ request }) => {
  await login(request, "teaching.analyst");
  let session = await request.get(
    "/api/web/v1/session?pageId=data-documents&" + new URLSearchParams(headquarters),
  );
  let headers = { origin, "X-CSRF-Token": (await session.json()).csrfToken };
  expect(
    (await request.post(url("catalog/search"), { headers, data: { query: "经营规则" } })).status(),
  ).toBe(403);
  await login(request, "teaching.admin");
  session = await request.get(
    "/api/web/v1/session?pageId=data-documents&" + new URLSearchParams(headquarters),
  );
  headers = { origin, "X-CSRF-Token": (await session.json()).csrfToken };
  expect(
    (
      await request.post(url("bases/search"), { headers, data: { query: "经营规则", baseId } })
    ).status(),
  ).toBe(404);
  expect(
    (await request.post(url("bases/search"), { headers, data: { query: " " } })).status(),
  ).toBe(422);
  expect(
    (
      await request.post(url("bases/search"), { headers: { origin }, data: { query: "经营规则" } })
    ).status(),
  ).toBe(403);
});
