import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type BrowserContext } from "@playwright/test";
import pg from "pg";

const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
const target = new URLSearchParams({ tenantId, workspaceId }).toString();
const origin = "http://127.0.0.1:" + (process.env.WEB_TEST_PORT ?? 8110);
const pagePath = "/t/yuanshan/w/zongbu/admin/data/metrics";
const dbNamePattern = /^\/compass_web_test_[a-z0-9_]+$/;
const handle = "semantic." + randomUUID().slice(0, 8);

test.beforeAll(async () => {
  const dsn = process.env.WEB_TEST_MIGRATION_DATABASE_URL;
  if (!dsn || !dbNamePattern.test(new URL(dsn).pathname)) {
    throw new Error("A dedicated Web migration test database is required");
  }
  const db = new pg.Client({ connectionString: dsn });
  await db.connect();
  try {
    await db.query("BEGIN");
    await db.query("SET LOCAL ROLE compass_web_owner");
    const account = randomUUID();
    const membership = randomUUID();
    const rights = ["module.analysis", "module.admin.data", "semantics.read", "semantics.draft"];
    const { rowCount } = await db.query(
      `INSERT INTO web.app_user(id,login_name,display_name,password_hash)
         SELECT $1,$2,'经营指标维护员',password_hash FROM web.app_user WHERE login_name='teaching.admin'`,
      [account, handle],
    );
    if (rowCount !== 1) throw new Error("The teaching identity fixture is required");
    await db.query(
      "INSERT INTO web.tenant_member(id,tenant_id,user_id,role_key) VALUES($1,$2,$3,'member')",
      [membership, tenantId, account],
    );
    await db.query(
      `INSERT INTO web.workspace_member(id,tenant_id,workspace_id,tenant_member_id,role_key,permission_keys)
       VALUES($1,$2,$3,$4,'analyst',$5)`,
      [randomUUID(), tenantId, workspaceId, membership, rights],
    );
    await db.query("COMMIT");
  } finally {
    await db.end();
  }
});

async function login(context: BrowserContext, loginName: string) {
  const response = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: {
      loginName,
      password: process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password",
    },
  });
  expect(response.status(), await response.text()).toBe(200);
}
async function csrf(request: APIRequestContext) {
  const session = await request.get("/api/web/v1/session?pageId=data-metrics&" + target);
  expect(session.status()).toBe(200);
  return (await session.json()).csrfToken as string;
}

test("业务用户在指标口径页面保存和续编个人草稿，租户管理员只读取获准目录", async ({
  browser,
  context,
  page,
}) => {
  await login(context, handle);
  await page.goto(pagePath);
  const create = page.getByRole("button", { name: "新建指标", exact: true });
  await expect(create).toBeVisible();
  await create.focus();
  await page.keyboard.press("Enter");
  const name = "门店履约样本 " + randomUUID().slice(0, 8);
  const code = "qa.fulfillment_" + randomUUID().slice(0, 8);
  const title = page.getByLabel("指标名称", { exact: true });
  await expect(title).toBeFocused();
  await title.fill(name);
  await page.getByLabel("指标代码", { exact: true }).fill(code);
  await page.locator('form input[name="domain"]').fill("门店运营");
  await page.getByLabel("业务含义", { exact: true }).fill("按妥投时点统计已履约订单。");
  const saved = page.waitForResponse(
    (r) => r.request().method() === "POST" && /\/semantic\/drafts\?/.test(r.url()),
  );
  await page.getByRole("button", { name: "保存草稿" }).click();
  const receipt = await saved;
  expect(receipt.status(), await receipt.text()).toBe(200);
  const draft = await receipt.json();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();

  const ownerDetail = await page.request.get(
    "/api/semantic/assets/" + draft.asset_id + "?" + target,
  );
  const before = await ownerDetail.json();
  expect(before.owner_scope_id).toBe(before.usage_scope_id);
  expect(before.state).toBe("draft");

  const admin = await browser.newContext({ baseURL: origin });
  try {
    await login(admin, "teaching.admin");
    const list = await admin.request.get("/api/semantic/assets?" + target);
    expect(list.status()).toBe(200);
    expect((await list.json()).items.map((a: { asset_id: string }) => a.asset_id)).not.toContain(
      draft.asset_id,
    );
    const detail = await admin.request.get("/api/semantic/assets/" + draft.asset_id + "?" + target);
    expect(detail.status()).toBe(404);
  } finally {
    await admin.close();
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.getByRole("button", { name: new RegExp(name) }).click();
  await page.getByRole("button", { name: "编辑草稿", exact: true }).click();
  await page.getByLabel("统计粒度", { exact: true }).fill("一次妥投订单");
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByRole("article")).toContainText("一次妥投订单");
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();

  const stale = await page.request.post(
    "/api/semantic/drafts/" + before.draft_id + "/revisions?" + target,
    {
      headers: { origin, "X-CSRF-Token": await csrf(page.request) },
      data: {
        request_key: randomUUID(),
        expected_digest: before.digest,
        asset_id: before.asset_id,
        code,
        name,
        domain: "门店运营",
        grain: "旧版标签页里的字段",
      },
    },
  );
  expect(stale.status()).toBe(409);
  const refreshed = await page.request.get("/api/semantic/assets/" + draft.asset_id + "?" + target);
  const latest = await refreshed.json();
  expect(latest.version_no).toBe(before.version_no + 1);
  expect(latest.draft_id).toBe(before.draft_id);
  expect(latest.grain).toBe("一次妥投订单");
});

test("重复的保存请求只建立一个草稿，缺漏的口径得到字段提示", async ({ context }) => {
  await login(context, handle);
  const headers = { origin, "X-CSRF-Token": await csrf(context.request) };
  const body = {
    request_key: randomUUID(),
    name: "新店订单",
    code: "qa.orders_" + randomUUID().slice(0, 8),
    domain: "销售",
  };
  const responses = await Promise.all(
    [0, 1].map(() =>
      context.request.post("/api/semantic/drafts?" + target, { headers, data: body }),
    ),
  );
  const [a, b] = await Promise.all(
    responses.map(async (response) => {
      expect(response.status(), await response.text()).toBe(200);
      return response.json();
    }),
  );
  expect(b).toEqual(a);
  const check = await context.request.post(
    "/api/semantic/revisions/" + a.version_id + "/validate?" + target,
    {
      headers,
      data: { request_key: randomUUID(), expected_digest: a.digest, note: "复核口径完整性" },
    },
  );
  expect(check.status()).toBe(200);
  expect((await check.json()).findings).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ field: "粒度", severity: "error" }),
      expect.objectContaining({ field: "来源", severity: "error" }),
    ]),
  );
  const submit = await context.request.post(
    "/api/semantic/revisions/" + a.version_id + "/submit?" + target,
    {
      headers,
      data: { request_key: randomUUID(), expected_digest: a.digest, note: "送业务评审" },
    },
  );
  expect(submit.status()).toBe(422);
});
