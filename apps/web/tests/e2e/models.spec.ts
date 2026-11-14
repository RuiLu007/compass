import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import pg from "pg";
import { DEFAULT_TEMPLATES } from "../../src/contracts/authorization";
import { setVisibility, observePageFlashes, pageFlashes } from "./visibility";
import { agentResponse, reduceAgentResponse } from "../../src/contracts/agent-response";
const fid = (n: number) => `99500000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const tenantId = fid(10),
  workspaceId = fid(20);
const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const password = process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password";
let owner: pg.Client;
test.beforeAll(async () => {
  const connectionString = process.env.WEB_TEST_MIGRATION_DATABASE_URL;
  if (
    !connectionString ||
    !/^\/compass_web_test_[a-z0-9_]+$/.test(new URL(connectionString).pathname)
  )
    throw new Error("Dedicated test migration database required");
  owner = new pg.Client({ connectionString });
  await owner.connect();
  await owner.query("SET ROLE compass_web_owner");
});
test.beforeEach(async () => {
  await owner.query(
    "UPDATE web.workspace_member SET permission_keys=$2,version=version+1 WHERE id=$1",
    [fid(40), [...DEFAULT_TEMPLATES.analyst, "models.invoke"]],
  );
  await owner.query("UPDATE web.tenant SET authz_version=authz_version+1 WHERE id=$1", [tenantId]);
});
test.afterAll(async () => {
  await owner?.end();
});
async function login(context: BrowserContext, name = "platform") {
  let response = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: { loginName: `teaching.${name}`, password },
  });
  if (response.status() === 429) {
    test.setTimeout(test.info().timeout + 65000);
    await expect
      .poll(
        async () => {
          response = await context.request.post("/api/web/v1/auth/login", {
            headers: { origin },
            data: { loginName: `teaching.${name}`, password },
          });
          return response.status();
        },
        { timeout: 65000, intervals: [5000] },
      )
      .not.toBe(429);
  }
  expect(response.status(), await response.text()).toBe(200);
}
async function active(page: Page, path = "/platform/models") {
  await page.goto(path);
  await expect(page.locator("[data-protected-root]")).toBeVisible();
}
async function csrf(context: BrowserContext) {
  const response = await context.request.get("/api/web/v1/session?pageId=platform-models");
  expect(response.ok()).toBe(true);
  return (await response.json()).csrfToken as string;
}
async function add(context: BrowserContext, model: "pro" | "flash" | "decision", alias: string) {
  const names = {
    pro: "deepseek-v4-pro",
    flash: "deepseek-flash",
    decision: "decision-model-preview",
  };
  const id = names[model];
  const response = await context.request.post("/api/web/v1/platform/models", {
    headers: { origin, "X-CSRF-Token": await csrf(context) },
    data: {
      operation_id: crypto.randomUUID(),
      catalog_id: `${model === "decision" ? "aliyun" : "deepseek"}/${id}`,
      api_model: id,
      alias,
      base_url:
        model === "decision"
          ? "https://fixture.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
          : "https://api.deepseek.com",
      api_key: "e2e-model-secret-only",
      enabled: true,
      chat_visible: model !== "decision",
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()).provider_id as string;
}
async function openTest(page: Page, alias: string) {
  await active(page);
  await page.getByLabel("搜索模型 Provider").fill(alias);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: alias })
    .getByRole("button", { name: "测试", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "能力测试", exact: true })).toBeVisible();
}
async function run(page: Page) {
  await page.getByRole("button", { name: "运行测试", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "已完成" })).toBeVisible();
}

test("platform-only administrator manages persistent providers and tests LLM capabilities through real BFF and Agent", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  await login(context);
  await active(page);
  const alias = `E2E LLM ${Date.now()}`;
  await page.getByRole("button", { name: "添加 Provider", exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("搜索模型目录").fill("deepseek-v4-pro");
  await expect(
    dialog
      .getByLabel("模型目录", { exact: true })
      .locator("option[value='deepseek/deepseek-v4-pro']"),
  ).toHaveCount(1);
  await dialog.getByLabel("模型目录", { exact: true }).selectOption("deepseek/deepseek-v4-pro");
  await dialog.getByLabel("显示别称").fill(alias);
  await dialog.getByLabel("Base URL", { exact: true }).fill("https://api.deepseek.com");
  await dialog.getByLabel("API Key", { exact: true }).fill("e2e-model-secret-only");
  await dialog.getByLabel("允许出现在分析 Chat 主模型列表").check();
  await dialog.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.reload();
  await page.getByLabel("搜索模型 Provider").fill(alias);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  const row = page.getByRole("row").filter({ hasText: alias });
  await expect(row).toContainText("已停用");
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.screenshot({
      path: `../../work/993/models-management-${width}.png`,
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await row.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("API Key", { exact: true })).toHaveValue("");
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await row.getByRole("button", { name: "测试", exact: true }).click();
  await page.getByLabel("测试消息", { exact: true }).fill("请回复一句测试文字");
  await run(page);
  await expect(page.getByRole("tabpanel")).toContainText("这是模型测试回复。");
  await page.getByLabel("返回方式").selectOption("stream");
  await page.getByLabel("开启 Thinking").check();
  await page.getByLabel("推理强度").selectOption("high");
  await run(page);
  await expect(page.getByRole("tabpanel")).toContainText("这是模型测试回复。");
  await page.getByRole("tab", { name: "调用信息", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("推理 tokens");
  await page.getByLabel("请求工具调用").check();
  await page.getByLabel("工具定义（JSON 数组）").fill(
    JSON.stringify([
      {
        type: "function",
        function: {
          name: "get_sales",
          description: "查询销售",
          parameters: {
            type: "object",
            properties: { month: { type: "string" } },
            required: ["month"],
            additionalProperties: false,
          },
        },
      },
      {
        type: "function",
        function: {
          name: "get_inventory",
          description: "查询库存",
          parameters: {
            type: "object",
            properties: { region: { type: "string" } },
            required: ["region"],
            additionalProperties: false,
          },
        },
      },
    ]),
  );
  await run(page);
  await expect(page.getByRole("heading", { name: "回填工具结果" })).toBeVisible();
  await page.getByLabel(/get_sales ·/).fill('{"sales":12345}');
  await page.getByLabel(/get_inventory ·/).fill('{"stock":345}');
  await page.getByRole("button", { name: "提交工具结果并续接" }).click();
  await expect(page.getByRole("heading", { name: "回填工具结果" })).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: "已完成" })).toBeVisible();
  await page.getByRole("tab", { name: "正文", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("工具结果已接收。");
  await page.getByLabel("请求工具调用").uncheck();
  await page.getByLabel("开启 Thinking").uncheck();
  await page.getByLabel("返回方式").selectOption("sync");
  for (const format of ["json", "schema"]) {
    await page.getByLabel("输出格式").selectOption(format);
    await run(page);
    await page.getByRole("tab", { name: "结构化结果", exact: true }).click();
    await expect(page.getByRole("tabpanel")).toContainText("校验状态：通过");
  }
  expect(await page.locator("body").textContent()).not.toContain("e2e-model-secret-only");
});

test("decision model accepts choice, noul and score questions and remains unavailable to Chat", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  await login(context);
  const alias = `E2E Decision ${Date.now()}`;
  await add(context, "decision", alias);
  await openTest(page, alias);
  await expect(page.getByLabel("开启 Thinking")).toHaveCount(0);
  await page.getByLabel("决策场景 state").fill("有足够库存，订单金额 200 元。");
  await page.getByLabel("问题说明", { exact: true }).fill("是否允许订单？");
  await page.getByRole("button", { name: "添加决策问题", exact: true }).click();
  await page.getByLabel("问题类型", { exact: true }).nth(1).selectOption("noul");
  await page.getByLabel("问题说明", { exact: true }).nth(1).fill("库存是否足够？");
  await page.getByRole("button", { name: "添加决策问题", exact: true }).click();
  await page.getByLabel("问题类型", { exact: true }).nth(2).selectOption("score");
  await page.getByLabel("问题说明", { exact: true }).nth(2).fill("评估订单优先级");
  await run(page);
  await page.getByRole("tab", { name: "结构化结果", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("校验状态：通过");
  await expect(page.getByRole("tabpanel")).toContainText("choice");
  await expect(page.getByRole("tabpanel")).toContainText("noul");
  await expect(page.getByRole("tabpanel")).toContainText("score");
  await page.screenshot({ path: "../../work/993/decision-browser.png", fullPage: true });
});

test("Chat explicitly selects LLMs, preserves session history, stops output and clears content on permission removal", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  await login(context);
  const prefix = `E2E Chat ${Date.now()}`;
  await add(context, "pro", `${prefix} Pro`);
  const flashId = await add(context, "flash", `${prefix} Flash`);
  await add(context, "decision", `${prefix} Decision`);
  const analyst = await browser.newContext({ baseURL: origin });
  await login(analyst, "analyst");
  const chat = await analyst.newPage();
  try {
    await active(chat, `/t/${tenantId}/w/${workspaceId}/analysis`);
    const select = chat.getByLabel("选择 Chat 主模型");
    await expect(select).toHaveValue("auto");
    await expect(chat.getByRole("button", { name: "发送消息", exact: true })).toBeDisabled();
    await expect(select.locator("option").filter({ hasText: "Decision" })).toHaveCount(0);
    await select.selectOption({ label: `${prefix} Pro` });
    await chat.getByLabel("分析问题").fill("你好");
    await chat.getByRole("button", { name: "发送消息", exact: true }).click();
    await expect(chat.locator(".model-chat-history")).toContainText("这是模型测试回复。");
    await expect(chat.getByRole("button", { name: "停止任务", exact: true })).toHaveCount(0);
    for (const width of [1440, 375]) {
      await chat.setViewportSize({ width, height: 1000 });
      await chat.screenshot({ path: `../../work/993/model-chat-${width}.png`, fullPage: true });
    }
    await chat.setViewportSize({ width: 1440, height: 1000 });
    chat.once("dialog", (dialog) => dialog.accept());
    await select.selectOption({ label: `${prefix} Flash` });
    // Changing a model persists a context-reset before React applies the selection.
    await expect(select).toHaveValue(flashId);
    await expect(chat.locator(".model-chat-history")).toContainText("这是模型测试回复。");
    const disabled = await context.request.patch(`/api/web/v1/platform/models/${flashId}`, {
      headers: { origin, "X-CSRF-Token": await csrf(context) },
      data: { operation_id: crypto.randomUUID(), expected_version: 1, enabled: false },
    });
    expect(disabled.ok(), await disabled.text()).toBe(true);
    await chat.getByLabel("分析问题").fill("模型失效时保留输入");
    await chat.getByRole("button", { name: "发送消息", exact: true }).click();
    await expect(select).toHaveValue("auto");
    await expect(chat.getByLabel("分析问题")).toHaveValue("模型失效时保留输入");
    await expect(chat.getByText("所选模型已不可用，请重新选择后发送。")).toBeVisible();
    await expect(select.locator("option").filter({ hasText: `${prefix} Flash` })).toHaveCount(0);
    await select.selectOption({ label: `${prefix} Pro` });
    for (const [prompt, message] of [
      ["E2E_AUTH_FAIL", "模型调用失败，本轮分析未完成。"],
      ["E2E_TRUNCATED", "模型返回结果不完整，本轮分析未完成。"],
    ]) {
      await chat.getByLabel("分析问题").fill(prompt);
      await chat.getByRole("button", { name: "发送消息", exact: true }).click();
      await expect(chat.locator(".model-chat-turn").last()).toContainText(message);
      await expect(chat.getByRole("button", { name: "停止任务", exact: true })).toHaveCount(0);
    }
    expect(await chat.locator("body").textContent()).not.toContain("private upstream body");
    await chat.getByLabel("分析问题").fill("E2E_SLOW");
    await chat.getByRole("button", { name: "发送消息", exact: true }).click();
    await expect(chat.locator(".model-chat-turn").last()).toContainText(
      /正在理解问题|正在分析问题|执行中|正在输出回答/,
    );
    const stop = chat.getByRole("button", { name: "停止任务", exact: true });
    await expect(stop).toBeVisible();
    await stop.click();
    await expect(chat.locator(".model-chat-turn").last()).toContainText("已终止");
    await chat.getByLabel("分析问题").fill("E2E_SLOW 私密测试内容");
    await chat.getByRole("button", { name: "发送消息", exact: true }).click();
    await owner.query(
      "UPDATE web.workspace_member SET permission_keys=$2,version=version+1 WHERE id=$1",
      [fid(40), DEFAULT_TEMPLATES.analyst],
    );
    await owner.query("UPDATE web.tenant SET authz_version=authz_version+1 WHERE id=$1", [
      tenantId,
    ]);
    await expect(chat.locator(".model-chat-history")).toHaveCount(0, { timeout: 35000 });
    await expect(
      chat.getByText("当前账户尚未获得模型调用权限，请联系工作区管理员。"),
    ).toBeVisible();
    expect(await chat.locator("body").textContent()).not.toContain("私密测试内容");
  } finally {
    await analyst.close();
  }
  await active(page);
  await expect(page.getByRole("heading", { name: "平台模型管理", exact: true })).toBeVisible();
  await login(context, "admin");
  await active(page, `/t/${tenantId}/w/${workspaceId}/admin/capabilities/models`);
  await expect(page.getByRole("heading", { name: "工作区可用模型", exact: true })).toBeVisible();
  await expect(page.locator(".model-summary-grid")).toContainText(`${prefix} Pro`);
  await expect(page.getByRole("button", { name: "添加 Provider", exact: true })).toHaveCount(0);
  expect(await page.locator("body").textContent()).not.toContain("api.deepseek.com");
  expect(await page.locator("body").textContent()).not.toContain("e2e-model-secret-only");
});

test("model management and test panels support keyboard selection and 375/768/1440 layouts", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  await login(context);
  const alias = `E2E Viewport ${Date.now()}`;
  await add(context, "flash", alias);
  await active(page);
  await page.getByRole("button", { name: "添加 Provider", exact: true }).first().click();
  await page.getByLabel("搜索模型目录").fill("deepseek-flash");
  const select = page.getByRole("dialog").getByLabel("模型目录", { exact: true });
  await expect(select.locator("option[value='deepseek/deepseek-flash']")).toHaveCount(1);
  await select.focus();
  await select.press("ArrowDown");
  await select.press("Enter");
  await expect(select).toHaveValue("deepseek/deepseek-flash");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  for (const width of [1440, 768, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    await openTest(page, alias);
    await expect(page.getByLabel("测试消息", { exact: true })).toBeVisible();
    await page.getByLabel("测试消息", { exact: true }).fill("响应式测试");
    await run(page);
    await expect(page.getByRole("tabpanel")).toContainText("这是模型测试回复。");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: `../../work/993/models-${width}.png`, fullPage: true });
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "返回模型列表", exact: true }).click();
  }
});

test("LiteLLM 手工模型可配置、测试并开放给 Chat，连接参数不回显", async ({ page, context }) => {
  await login(context);
  await active(page);
  await page.getByRole("button", { name: "添加 Provider", exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("搜索模型目录").fill("anthropic");
  const catalog = dialog.getByLabel("模型目录", { exact: true });
  await expect.poll(() => catalog.locator("option:not([value=''])").count()).toBeGreaterThan(0);
  expect(await catalog.locator("option[disabled]").count()).toBe(0);
  await dialog.getByLabel("手动输入模型标识").check();
  await dialog.getByLabel("模型标识", { exact: true }).fill("openai/teaching-unlisted-model");
  const alias = `SDK 手工模型 ${Date.now()}`;
  await dialog.getByLabel("显示别称", { exact: true }).fill(alias);
  await dialog.getByLabel("Base URL", { exact: true }).fill("https://api.deepseek.com/v1");
  await dialog.getByLabel("API Key", { exact: true }).fill("e2e-private-sdk-key");
  await dialog.locator("summary").click();
  await dialog
    .getByLabel("供应商连接参数（JSON）", { exact: true })
    .fill('{"extra_headers":{"X-Teaching-Connection":"private-sdk-header"}}');
  await dialog.getByLabel("启用此 Provider").check();
  await dialog.getByLabel("允许出现在分析 Chat 主模型列表").check();
  await dialog.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByLabel("搜索模型 Provider").fill(alias);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  const row = page.getByRole("row").filter({ hasText: alias });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(dialog.getByLabel("API Key", { exact: true })).toHaveValue("");
  await dialog.locator("summary").click();
  await expect(dialog.getByLabel("供应商连接参数（JSON）", { exact: true })).toHaveValue("");
  expect(await dialog.textContent()).not.toContain("private-sdk-header");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await row.getByRole("button", { name: "测试", exact: true }).click();
  await page.getByLabel("测试消息", { exact: true }).fill("验证 SDK 自定义模型");
  await run(page);
  await expect(page.getByText("这是模型测试回复。", { exact: true })).toBeVisible();
  await login(context, "analyst");
  const models = await context.request.get(
    `/api/web/v1/models/available?tenantId=${tenantId}&workspaceId=${workspaceId}&view=chat`,
  );
  expect(models.status()).toBe(200);
  const text = await models.text();
  expect(text).toContain(alias);
  expect(text).not.toContain("private-sdk-header");
  expect(text).not.toContain("e2e-private-sdk-key");
});

test("JEV System One preset accepts custom models and preserves credentials when edited", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  page.on("dialog", (dialog) => dialog.accept());
  const decisionRequests: { input: { questions: Record<string, { type: string }> } }[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /\/models\/[^/]+\/tests$/.test(new URL(request.url()).pathname)
    )
      decisionRequests.push(request.postDataJSON());
  });
  await login(context);
  await active(page);
  const alias = `E2E JEV ${Date.now()}`;
  await page.getByRole("button", { name: "添加 Provider", exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("搜索模型目录").fill("jev-latest");
  await expect(
    dialog.getByLabel("模型目录", { exact: true }).locator("option[value='typesafe/jev-latest']"),
  ).toHaveCount(1);
  await dialog.getByLabel("模型目录", { exact: true }).selectOption("typesafe/jev-latest");
  await expect(dialog.getByLabel("Base URL", { exact: true })).toHaveValue(
    "https://api.typesafe.ai/v1",
  );
  await expect(dialog.getByLabel("允许出现在分析 Chat 主模型列表")).toBeDisabled();
  await expect(dialog.getByText("供应商连接参数", { exact: true })).toHaveCount(0);
  await dialog.getByLabel("API 模型名", { exact: true }).fill("jev-custom-version");
  await dialog.getByLabel("显示别称").fill(alias);
  await dialog.getByLabel("API Key", { exact: true }).fill("e2e-jev-private-key");
  await page.screenshot({ path: "../../work/993/systemone/jev-provider-form.png", fullPage: true });
  await dialog.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await openTest(page, alias);
  await expect(page.getByLabel("测试示例", { exact: true }).locator("option")).toHaveCount(4);
  await expect(page.getByLabel("决策场景 state")).not.toHaveValue("");
  await expect(page.getByLabel("问题说明", { exact: true })).not.toHaveValue("");
  await run(page);
  for (const [example, expectedTypes] of [
    ["escalation", ["noul"]],
    ["severity", ["score"]],
    ["multi-question", ["choice", "noul", "score"]],
  ] as const) {
    const before = decisionRequests.length;
    await page.getByLabel("测试示例", { exact: true }).selectOption(example);
    await page.getByRole("button", { name: "加载示例", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "等待测试" })).toBeVisible();
    expect(decisionRequests).toHaveLength(before);
    await expect(page.getByLabel("问题类型", { exact: true })).toHaveCount(expectedTypes.length);
    await run(page);
    const submitted = decisionRequests.at(-1)!.input;
    expect(Object.values(submitted.questions).map((question) => question.type)).toEqual(
      expectedTypes,
    );
    await page.getByRole("tab", { name: "原始响应", exact: true }).click();
    const raw = JSON.parse((await page.getByLabel("原始响应 JSON").textContent())!);
    expect(raw.model).toBe("jev-custom-version");
    expect(Object.keys(raw.answers)).toEqual(Object.keys(submitted.questions));
    expect(raw.usage).toEqual({ input_tokens: 12, output_tokens: 3 });
    expect(raw).not.toHaveProperty("config_version");
  }
  await page.screenshot({ path: "../../work/993/systemone/jev-examples-raw.png", fullPage: true });
  await page.getByRole("tab", { name: "调用信息", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("jev-custom-version");
  await active(page);
  await page.getByLabel("搜索模型 Provider").fill(alias);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: alias })
    .getByRole("button", { name: "编辑", exact: true })
    .click();
  await expect(dialog.getByLabel("API Key", { exact: true })).toHaveValue("");
  await dialog.getByLabel("手动输入模型标识").check();
  await dialog.getByLabel("模型标识", { exact: true }).fill("typesafe/jev-future-version");
  await expect(dialog.getByLabel("允许出现在分析 Chat 主模型列表")).toBeDisabled();
  await dialog.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await openTest(page, alias);
  await page.getByLabel("决策场景 state").fill("订单支付后重复扣款，用户要求退款。");
  await run(page);
  await page.getByRole("tab", { name: "调用信息", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("jev-future-version");
  expect(await page.locator("body").textContent()).not.toContain("e2e-jev-private-key");
  await page.screenshot({ path: "../../work/993/systemone/jev-result.png", fullPage: true });
});

for (const completeInBackground of [false, true]) {
  test(`app switching preserves chat and ${completeInBackground ? "background completion" : "an active analysis run"} without a document reload`, async ({
    page,
    context,
  }) => {
    await login(context);
    const alias = `Visibility stream ${Date.now()}`;
    await add(context, "pro", alias);
    await active(page, "/t/yuanshan/w/zongbu/analysis");
    await page.getByLabel("选择 Chat 主模型").selectOption({ label: alias });
    const calls: { question: string; sessionId: string | null; history?: unknown }[] = [];
    const accepted: { sessionId: string; runId: string }[] = [];
    let documents = 0;
    page.on("request", (request) => {
      if (request.isNavigationRequest()) documents++;
      if (request.method() === "POST" && request.url().endsWith("/analysis/sessions/submit"))
        calls.push(request.postDataJSON());
    });
    const send = async (question: string) => {
      const submitted = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url().endsWith("/analysis/sessions/submit"),
      );
      await page.getByLabel("分析问题").fill(question);
      await page.getByRole("button", { name: "发送消息", exact: true }).click();
      const response = await submitted;
      expect(response.status(), await response.text()).toBe(202);
      accepted.push(await response.json());
      await expect(page.locator(".model-chat-turn").last()).toHaveAttribute(
        "data-run-id",
        accepted.at(-1)!.runId,
      );
    };
    const stop = page.getByRole("button", { name: "停止任务", exact: true });
    const turns = page.locator(".model-chat-turn");
    const current = turns.last();
    const state = current.locator(".analysis-run-header").getByRole("status");
    const answer = current.getByRole("region", { name: "最终回答" });
    await send("第一轮聊天记录");
    await expect(answer).toContainText("这是模型测试回复。");
    await expect(stop).toHaveCount(0);
    await send("E2E_SLOW 切换 App 时继续生成");
    await expect(current).toHaveAttribute("data-run-id", /^[a-f0-9-]{36}$/);
    const activeRun = await current.getAttribute("data-run-id");
    await expect(state).toContainText(/正在理解问题|正在分析问题|执行中|正在输出回答/);
    await expect(answer).toHaveCount(0);
    // Capture the active session after acceptance/navigation, at the visibility boundary.
    await page.evaluate(() =>
      Reflect.set(window, "retainedChat", document.querySelector(".model-chat-history")),
    );
    await observePageFlashes(page);
    await setVisibility(page, "hidden");
    await expect(page.locator("[data-protected-root]")).toBeVisible();
    if (completeInBackground) {
      await expect(answer).toContainText("这是模型测试回复。");
      await expect(turns).toHaveCount(2);
    }
    await setVisibility(page, "visible");
    await expect(page.locator("[data-protected-root]")).toBeVisible();
    if (!completeInBackground) {
      await expect(stop).toBeVisible();
      expect(await current.getAttribute("data-run-id")).toBe(activeRun);
      await setVisibility(page, "hidden");
      await setVisibility(page, "visible");
    }
    await expect(answer).toContainText("这是模型测试回复。");
    await expect(stop).toHaveCount(0);
    await expect(turns).toHaveCount(2);
    expect(await current.getAttribute("data-run-id")).toBe(activeRun);
    await expect(page.locator(".model-chat-history")).toContainText("第一轮聊天记录");
    expect(
      await page.evaluate(
        () => Reflect.get(window, "retainedChat") === document.querySelector(".model-chat-history"),
      ),
    ).toBe(true);
    await send("继续第三轮聊天");
    await expect(turns).toHaveCount(3);
    await expect(answer).toContainText("这是模型测试回复。");
    await expect(stop).toHaveCount(0);
    expect(calls).toHaveLength(3);
    expect(calls.every((call) => call.history === undefined)).toBe(true);
    expect(calls[0].sessionId).toBeNull();
    expect(calls[1].sessionId).toBe(accepted[0].sessionId);
    expect(calls[2]).toMatchObject({
      question: "继续第三轮聊天",
      sessionId: accepted[0].sessionId,
    });
    expect(accepted.every((run) => run.sessionId === accepted[0].sessionId)).toBe(true);
    const messages = await context.request.get(
      `/api/web/v1/analysis/sessions/${accepted[0].sessionId}/messages`,
      { params: { tenantId, workspaceId } },
    );
    expect(messages.status(), await messages.text()).toBe(200);
    const persisted = reduceAgentResponse(null, agentResponse(await messages.json()));
    const history = accepted.map(({ runId }) =>
      Object.values(persisted.runs[runId].messages)
        .sort((a, b) => a.ordinal - b.ordinal)
        .map((message) => ({ role: message.role, text: message.content.text })),
    );
    expect(history).toEqual(
      ["第一轮聊天记录", "E2E_SLOW 切换 App 时继续生成", "继续第三轮聊天"].map((question) => [
        { role: "user", text: question },
        { role: "assistant", text: "这是模型测试回复。" },
      ]),
    );
    await page.getByLabel("分析问题").fill("保留下一轮草稿");
    await setVisibility(page, "hidden");
    await setVisibility(page, "visible");
    await expect(page.getByLabel("分析问题")).toHaveValue("保留下一轮草稿");
    expect(documents).toBe(0);
    expect(await pageFlashes(page)).toEqual({ sameRoot: true, flashes: [] });
  });
}
