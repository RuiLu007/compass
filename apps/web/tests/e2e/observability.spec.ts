import { test, expect, type BrowserContext } from "@playwright/test";

const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
async function login(context: BrowserContext, name: string) {
  const response = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: { loginName: `teaching.${name}`, password: process.env.WEB_TEST_PASSWORD },
  });
  expect(response.status(), await response.text()).toBe(200);
}

test("traces stay copyable through completion and cancellation, with admin-only cross-instance comparison", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  await login(context, "platform");
  const session = await context.request.get("/api/web/v1/session?pageId=platform-models");
  const csrf = (await session.json()).csrfToken;
  const ids: string[] = [];
  for (const model of ["deepseek-flash", "deepseek-v4-pro"]) {
    const response = await context.request.post("/api/web/v1/platform/models", {
      headers: { origin, "X-CSRF-Token": csrf },
      data: {
        operation_id: crypto.randomUUID(),
        catalog_id: `deepseek/${model}`,
        api_model: model,
        alias: `Observability ${model} ${crypto.randomUUID().slice(0, 8)}`,
        base_url: "https://api.deepseek.com",
        api_key: "controlled-e2e-only",
        enabled: true,
        chat_visible: true,
      },
    });
    expect(response.status(), await response.text()).toBe(201);
    ids.push((await response.json()).provider_id);
  }
  await page.goto("/platform/models");
  const panel = page.getByRole("region", { name: "调用观测" });
  await expect(panel).toContainText("browser-990");
  await expect(panel.getByRole("link", { name: "打开 Langfuse" })).toHaveAttribute(
    "href",
    /127\.0\.0\.1:8104\/project\//,
  );
  await login(context, "admin");
  await page.goto(`/t/${tenantId}/w/${workspaceId}/analysis`);
  await page.getByLabel("选择 Chat 主模型").selectOption(ids[1]);
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/models/calls") && r.request().method() === "POST",
  );
  await page.getByLabel("分析问题").fill("比较昨日销售额");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const trace = (await response).headers()["x-compass-trace-id"];
  expect(trace).toMatch(/^[0-9a-f]{32}$/);
  await expect(page.locator(".model-chat-answer").last()).toContainText("已完成");
  const detail = page.locator(".model-call-trace").last();
  await detail.locator("summary").click();
  await expect(detail).toContainText(trace);
  await expect(detail).toContainText("本轮已采样");
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await detail.getByRole("button", { name: "复制观测编号" }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(trace);
  const slowResponse = page.waitForResponse(
    (r) => r.url().endsWith("/models/calls") && r.request().method() === "POST",
  );
  await page.getByLabel("分析问题").fill("比较销售额 E2E_SLOW");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const slowTrace = (await slowResponse).headers()["x-compass-trace-id"];
  await expect(page.locator(".model-call-trace").last()).toBeVisible();
  await page.getByRole("button", { name: "停止生成", exact: true }).click();
  await expect(page.locator(".model-chat-answer").last()).toContainText("已停止");
  const canceled = page.locator(".model-call-trace").last();
  await canceled.locator("summary").click();
  await expect(canceled).toContainText(slowTrace);
  await page.setViewportSize({ width: 375, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: "../../work/990-chat-mobile.png", fullPage: true });
  expect((await context.request.get("/api/web/v1/platform/models/observability")).status()).toBe(
    403,
  );
  await login(context, "platform");
  await page.goto("/platform/models");
  await page.getByLabel("对比模型A").selectOption(ids[0]);
  await page.getByLabel("对比模型B").selectOption(ids[1]);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(panel).toContainText("跨实例聚合");
  await expect(panel.locator("tbody tr")).toHaveCount(2);
  await expect(panel.locator("tbody tr").last()).toContainText("deepseek-v4-pro");
  await expect(panel).toContainText("未知尝试");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: "../../work/990-observation-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "../../work/990-observation-desktop.png", fullPage: true });
  const anonymous = await browser.newContext();
  expect(
    (await anonymous.request.get(origin + "/api/web/v1/platform/models/observability")).status(),
  ).toBe(401);
  await anonymous.close();
});
