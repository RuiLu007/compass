import { test, expect, type BrowserContext } from "@playwright/test";

const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const analysisPath = `/t/${tenantId}/w/${workspaceId}/analysis`;
const password = process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password";

async function login(context: BrowserContext, name: string) {
  const response = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: { loginName: `teaching.${name}`, password },
  });
  expect(response.status(), await response.text()).toBe(200);
}

async function configure(context: BrowserContext) {
  await login(context, "platform");
  const session = await context.request.get("/api/web/v1/session?pageId=platform-models");
  const csrf = (await session.json()).csrfToken;
  const providers: Record<string, string> = {};
  for (const [role, model] of [
    ["pro", "deepseek-v4-pro"],
    ["flash", "deepseek-flash"],
    ["jev", "jev"],
  ]) {
    const response = await context.request.post("/api/web/v1/platform/models", {
      headers: { origin, "X-CSRF-Token": csrf },
      data: {
        operation_id: crypto.randomUUID(),
        catalog_id: `${role === "jev" ? "typesafe" : "deepseek"}/${model}`,
        api_model: model,
        alias: `Routing ${role} ${crypto.randomUUID().slice(0, 8)}`,
        base_url: role === "jev" ? "https://api.typesafe.ai" : "https://api.deepseek.com",
        api_key: "controlled-e2e-only",
        enabled: true,
        chat_visible: role !== "jev",
      },
    });
    expect(response.status(), await response.text()).toBe(201);
    providers[role] = (await response.json()).provider_id;
  }
  return providers;
}

test("business questions expose two-level intent, dictionary features and auto model inference", async ({
  page,
  context,
}) => {
  await configure(context);
  await login(context, "admin");
  await page.goto(analysisPath);
  await page.getByLabel("选择 Chat 主模型").selectOption("auto");
  const request = page.waitForResponse(
    (r) => r.url().endsWith("/models/calls") && r.request().method() === "POST",
  );
  await page.getByLabel("分析问题").fill("根据昨日销售100元、前日80元，比较销售额并简述。");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  expect((await request).status()).toBe(200);
  await expect(page.locator(".model-chat-answer")).toContainText("这是模型测试回复。");
  await page.getByText("意图分类与响应结构", { exact: true }).click();
  const value = JSON.parse(await page.getByLabel("意图分类响应结构").innerText());
  expect(Object.keys(value.intent).sort()).toEqual(["category_id", "scene_id"]);
  expect(value.intent).toEqual({ category_id: "business_channel", scene_id: "SC-02" });
  expect(value.features.difficulty).toBe("L1");
  expect(value.routing.model).toBe("deepseek-flash");
  expect(value.metrics.classifier_source).toBe("jev");
  expect(value.result.status).toBe("completed");
  expect(value.metrics.classification_calls).toHaveLength(1);
  await page.screenshot({ path: "../../work/992/business-routing.png", fullPage: true });
});

test("unrelated chat uses Flash refusal prompt even when Pro is selected", async ({
  page,
  context,
}) => {
  const providers = await configure(context);
  await login(context, "admin");
  await page.goto(analysisPath);
  await page.getByLabel("选择 Chat 主模型").selectOption(providers.pro);
  await page.getByLabel("分析问题").fill("你好呀，讲个笑话");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".model-chat-answer")).toContainText(
    "你有没有想分析的销售、库存或其他业务数据任务？",
  );
  await page.getByText("意图分类与响应结构", { exact: true }).click();
  const value = JSON.parse(await page.getByLabel("意图分类响应结构").innerText());
  expect(value.intent).toEqual({ category_id: "input_boundary", scene_id: "SC-39" });
  expect(value.features.response_mode).toBe("refuse");
  expect(value.routing.reason).toBe("off_topic_flash");
  expect(value.routing.provider_id).toBe(providers.flash);
  expect(value.result.requested_model).toBe("deepseek-flash");
  for (const width of [1440, 768, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({ path: `../../work/992/off-topic-${width}.png`, fullPage: true });
  }
});
