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

test("业务用户创建、修订并删除已生效的个人指标，租户管理员只读取获准目录", async ({
  browser,
  context,
  page,
}) => {
  await login(context, handle);
  const name = "门店履约样本 " + randomUUID().slice(0, 8);
  const code = "qa.fulfillment_" + randomUUID().slice(0, 8);
  const source = await page.request.post("/api/semantic/sources?" + target, {
    headers: { origin, "X-CSRF-Token": await csrf(page.request) },
    data: {
      request_key: randomUUID(),
      kind: "document",
      locator: code,
      version: "1",
      excerpt: "履约手册",
      priority: "P1",
      responsible: "履约组",
    },
  });
  expect(source.ok()).toBeTruthy();
  const { source_id } = await source.json();
  await page.goto(pagePath);
  await expect(page.getByText("默认口径与检索预算")).toHaveCount(0);
  const create = page.getByRole("button", { name: "新建资产", exact: true });
  await expect(create).toBeVisible();
  await create.focus();
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("dialog", { name: "新建资产" });
  await expect(drawer).toBeVisible();
  const title = page.getByLabel("名称", { exact: true });
  await expect(title).toBeFocused();
  await title.fill(name);
  await drawer.getByLabel("代码", { exact: true }).fill(code);
  await drawer.getByLabel("业务域", { exact: true }).fill("门店运营");
  for (const [label, value] of Object.entries({
    负责人: "履约组",
    业务含义: "已完成妥投的订单",
    统计粒度: "每个订单",
    单位: "单",
    业务时间: "妥投时点",
    公式: "COUNT(DISTINCT order_id)",
    聚合规则: "订单去重计数",
    来源位置: "履约手册第 1 节",
  }))
    await drawer.getByLabel(label, { exact: true }).fill(value);
  await expect(page.getByRole("button", { name: "资产授权" })).toHaveCount(0);
  await drawer.getByLabel("登记来源").selectOption(source_id);
  await expect(drawer.getByRole("button", { name: "保存并生效" })).toBeInViewport();
  const saved = page.waitForResponse(
    (r) => r.request().method() === "POST" && /\/semantic\/assets\?/.test(r.url()),
  );
  await page.getByRole("button", { name: "保存并生效" }).click();
  const receipt = await saved;
  expect(receipt.status(), await receipt.text()).toBe(200);
  const draft = await receipt.json();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();

  await page.getByRole("dialog").getByRole("button", { name: "完成" }).click();
  const list = page.getByRole("region", { name: "资产列表" });
  const search = page.getByRole("searchbox", { name: "搜索资产" });
  await search.fill(code);
  const row = list.getByRole("button", { name: new RegExp(name) });
  await expect(row).toHaveAttribute("aria-haspopup", "dialog");
  await expect(page.getByRole("article")).toHaveCount(0);
  // Selecting the current row keeps its loaded detail.
  await row.click();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(row).toBeFocused();
  await search.fill("no-match-" + randomUUID());
  await expect(list.getByText("没有符合筛选条件的资产")).toBeVisible();
  await expect(page.getByRole("heading", { name, exact: true })).toHaveCount(0);
  await list.getByRole("button", { name: "清除筛选" }).click();
  await expect(row).toBeVisible();

  const ownerDetail = await page.request.get(
    "/api/semantic/assets/" + draft.asset_id + "?" + target,
  );
  const before = await ownerDetail.json();
  expect(before.owner_scope_id).toBe(before.usage_scope_id);
  expect(before.state).toBe("published");
  expect(before.kind).toBe("metric");

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
  await expect(page.getByRole("dialog", { name })).toHaveAttribute("data-placement", "right");
  await page.keyboard.press("Escape");
  await expect(row).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("统计粒度", { exact: true }).fill("一次妥投订单");
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.getByRole("article")).toContainText("每个订单");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("统计粒度", { exact: true })).toHaveValue("一次妥投订单");
  await page.getByRole("button", { name: "保存并生效" }).click();
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

  expect(latest.grain).toBe("一次妥投订单");
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "确认删除" }).click();
  await expect(page.getByText("资产已删除", { exact: true })).toBeVisible();
  expect(
    (await page.request.get("/api/semantic/assets/" + draft.asset_id + "?" + target)).status(),
  ).toBe(404);
});

test("登记来源抽屉保留失败输入，重试成功后可在新指标中选取来源", async ({ context, page }) => {
  await login(context, handle);
  await page.goto(pagePath);
  const trigger = page.getByRole("button", { name: "登记来源", exact: true });
  await trigger.click();
  const drawer = page.getByRole("dialog", { name: "登记来源", exact: true });
  const locator = drawer.getByLabel("来源位置");
  await expect(locator).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.keyboard.press("Enter");
  await expect(locator).toBeFocused();
  const location = "门店履约手册-" + randomUUID().slice(0, 8);
  await locator.fill(location);
  await drawer.getByLabel("来源摘要").fill("门店履约口径的统计说明");
  await drawer.getByLabel("责任人").fill("运营组");
  const save = drawer.getByRole("button", { name: "保存来源" });
  const endpoint = "**/semantic/sources?**";
  const release = Promise.withResolvers<void>();
  await page.route(
    endpoint,
    async (route) => {
      await release.promise;
      await route.fulfill({
        status: 503,
        json: { error: { message: "暂时无法登记来源，请重试。" } },
      });
    },
    { times: 1 },
  );
  await save.click();
  try {
    await expect(drawer.getByRole("button", { name: "正在保存…" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeVisible();
  } finally {
    release.resolve();
  }
  await expect(drawer.getByRole("alert")).toHaveText("暂时无法登记来源，请重试。");
  await expect(locator).toHaveValue(location);
  const stored = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().includes("/semantic/sources?"),
  );
  await save.click();
  const response = await stored;
  expect(response.status(), await response.text()).toBe(200);
  const { source_id } = await response.json();
  await expect(drawer).toHaveCount(0);
  await expect(page.getByText("来源已登记", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新建资产", exact: true }).click();
  const create = page.getByRole("dialog", { name: "新建资产" });
  const source = create.getByLabel("登记来源");
  await expect(source.getByRole("option", { name: new RegExp(location) })).toHaveCount(1);
  await source.selectOption(source_id);
  await create.getByRole("button", { name: "取消" }).click();
  await expect(create).toHaveCount(0);
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

test("实体字段、术语指向和类型筛选可用，引用中的实体有删除保护", async ({ context, page }) => {
  await login(context, handle);
  const headers = { origin, "X-CSRF-Token": await csrf(context.request) };
  const suffix = randomUUID().slice(0, 8);
  const api = (path: string, fields: Record<string, unknown>) =>
    context.request.post("/api/semantic/" + path + "?" + target, {
      headers,
      data: { request_key: randomUUID(), ...fields },
    });
  const src = await api("sources", {
    kind: "document",
    locator: "目录核验-" + suffix,
    version: "1",
    excerpt: "测试字典",
    priority: "P1",
    responsible: "运营组",
  });
  expect(src.ok()).toBeTruthy();
  const { source_id } = await src.json();
  const common = { responsible: "运营组", domain: "经营", source_id, source_position: "业务字典" };
  const entityName = "核算网点-" + suffix;
  const created = await api("assets", {
    ...common,
    kind: "entity",
    code: "site.qa_" + suffix,
    name: entityName,
    summary: "承担履约的经营网点",
    table: "site",
    primary_key: ["site_id"],
    grain: "一个网点",
    fields: { site_id: "网点标识" },
  });
  expect(created.status(), await created.text()).toBe(200);
  const entity = await created.json();
  await page.goto(pagePath);
  const filter = page.getByRole("search").getByLabel("资产类型");
  await filter.selectOption("term");
  await page.getByRole("button", { name: "新建资产" }).click();
  const form = page.getByRole("dialog");
  await expect(form.getByLabel("资产类型")).toHaveValue("term");
  const termName = "履约位置-" + suffix;
  const exact = { exact: true };
  for (const [label, value] of Object.entries({
    名称: termName,
    代码: "term.qa_" + suffix,
    业务域: "经营",
    负责人: "运营组",
    业务含义: "指订单实际发货的网点。",
    示例问题: "哪个网点发货？",
    易混场景: "销售归属门店可能不同。",
    来源位置: "业务字典",
  }))
    await form.getByLabel(label, exact).fill(value);
  await form.getByLabel("登记来源").selectOption(source_id);
  await form.getByRole("button", { name: "添加关联对象" }).click();
  await form.getByLabel("关联对象 1").selectOption(entity.asset_id);
  await form.getByRole("button", { name: "保存并生效" }).click();
  const detail = page.getByRole("dialog");
  await expect(detail.getByRole("heading", { name: termName })).toBeVisible();
  await expect(detail.getByRole("table", { name: "资产定义" })).toContainText(
    "销售归属门店可能不同",
  );
  await detail.getByRole("button", { name: entityName }).click();
  await expect(detail.getByRole("heading", { name: "实体字段" })).toBeVisible();
  await expect(detail).toContainText("网点标识");
  await detail.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "确认删除" }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("仍被其他定义引用");
  await page.getByRole("dialog").getByRole("button", { name: "取消" }).click();
  await detail.getByRole("button", { name: "返回" + termName }).click();
  await expect(detail.getByRole("heading", { name: termName })).toBeVisible();
  await detail.getByRole("button", { name: "完成" }).click();
  await expect(filter).toHaveValue("term");
  await filter.selectOption("entity");
  const results = page.getByRole("table", { name: "语义资产目录" });
  await expect(results.getByRole("button", { name: new RegExp(entityName) })).toBeVisible();
  await expect(results.getByRole("button", { name: new RegExp(termName) })).toHaveCount(0);
});

test("资产授权抽屉按部门和成员批量管理使用权", async ({ context, page, browser }) => {
  await login(context, "teaching.admin");
  const headers = { origin, "X-CSRF-Token": await csrf(context.request) };
  const suffix = randomUUID().slice(0, 8);
  async function create(path: string, fields: Record<string, unknown>) {
    const res = await context.request.post("/api/semantic/" + path + "?" + target, {
      headers,
      data: { request_key: randomUUID(), ...fields },
    });
    expect(res.status(), await res.text()).toBe(200);
    return res.json();
  }
  const tenant = await create("scopes", { kind: "tenant" });
  for (const n of [1, 2])
    await create("scopes", { kind: "department", name: `批量部门-${suffix}-${n}` });
  const owner = { owner_scope_id: tenant.scope_id };
  const src = await create("sources", {
    ...owner,
    kind: "document",
    locator: "资产授权-" + suffix,
    version: "1",
    excerpt: "验收数据字典",
    priority: "P1",
    responsible: "运营",
  });
  const shared = {
    ...owner,
    source_id: src.source_id,
    source_position: "字典",
    domain: "经营",
    responsible: "运营",
    summary: "验收定义",
    grain: "订单",
  };
  const entity = await create("assets", {
    ...shared,
    kind: "entity",
    name: "批量资产-" + suffix + "-实体",
    code: "grant.entity_" + suffix,
    table: "store",
    primary_key: ["id"],
    fields: { id: "门店标识" },
  });
  await create("assets", {
    ...shared,
    kind: "metric",
    name: "批量资产-" + suffix + "-指标",
    code: "grant.metric_" + suffix,
    unit: "单",
    formula: "count(id)",
    event_time: "created_at",
    aggregation: "count",
    timezone: "UTC",
  });
  const reader = await browser.newContext({ baseURL: origin });
  try {
    await login(reader, handle);
    const detail = () =>
      reader.request.get("/api/semantic/assets/" + entity.asset_id + "?" + target);
    expect((await detail()).status()).toBe(404);
    await page.goto(pagePath);
    const trigger = page.getByRole("button", { name: "资产授权" });
    const drawer = page.getByRole("dialog", { name: "资产授权" });
    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(drawer.getByRole("button", { name: "按部门授权" })).toBeFocused();
    await drawer.getByRole("searchbox", { name: "搜索部门" }).fill(suffix);
    await drawer
      .getByRole("region", { name: "部门" })
      .getByLabel(/全选搜索结果/)
      .check();
    await drawer.getByRole("searchbox", { name: "搜索资产" }).fill("批量资产-" + suffix);
    const kind = drawer.getByLabel("筛选授权资产类型");
    await kind.selectOption("metric");
    await expect(
      drawer.getByRole("region", { name: "资产" }).getByLabel(/全选搜索结果/),
    ).toHaveAccessibleName("全选搜索结果（1）");
    await kind.selectOption("");
    await drawer
      .getByRole("region", { name: "资产" })
      .getByLabel(/全选搜索结果/)
      .check();
    await expect(drawer.getByRole("status")).toHaveText("已选 2 个部门、2 项资产");
    const columns = drawer.locator(".semantic-grant-column");
    expect((await columns.nth(1).boundingBox())!.x).toBeGreaterThan(
      (await columns.first().boundingBox())!.x,
    );
    await page.route(
      "**/semantic/asset-grants?**",
      (route) => route.fulfill({ status: 503, json: { error: { message: "保存失败，请重试。" } } }),
      { times: 1 },
    );
    await drawer.getByRole("button", { name: "授予使用权" }).click();
    await expect(drawer.getByRole("alert")).toHaveText("保存失败，请重试。");
    await drawer.getByRole("button", { name: "授予使用权" }).click();
    await expect(drawer).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await page.setViewportSize({ width: 390, height: 844 });
    await trigger.click();
    await drawer.getByRole("button", { name: "按成员授权" }).click();
    const members = drawer.getByRole("region", { name: "成员" });
    await members.getByRole("searchbox").fill(handle);
    await members.getByLabel(/全选搜索结果/).check();
    const assets = drawer.getByRole("region", { name: "资产" });
    await assets.getByRole("searchbox").fill("批量资产-" + suffix);
    await assets.getByLabel(/全选搜索结果/).check();
    expect(await drawer.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect((await columns.nth(1).boundingBox())!.y).toBeGreaterThan(
      (await columns.first().boundingBox())!.y,
    );
    await drawer.getByRole("button", { name: "授予使用权" }).click();
    await expect(drawer).toHaveCount(0);
    expect((await detail()).status()).toBe(200);
    await trigger.click();
    await drawer.getByRole("button", { name: "按成员授权" }).click();
    await members.getByRole("searchbox").fill(handle);
    await members.getByLabel(/全选搜索结果/).check();
    await assets.getByRole("searchbox").fill("批量资产-" + suffix);
    await expect(assets.getByText("已授权", { exact: true })).toHaveCount(2);
    await assets.getByLabel(/全选搜索结果/).check();
    await drawer.getByRole("button", { name: "撤销授权" }).click();
    await expect(drawer).toHaveCount(0);
    expect((await detail()).status()).toBe(404);
    await trigger.click();
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
  } finally {
    await reader.close();
  }
});
