import { test, expect } from "@playwright/test";

const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const password = process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password";

test("platform admin demonstrates recovery, partial output, validation, cancellation and overload", async ({
  page,
  context,
}) => {
  const login = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: { loginName: "teaching.platform", password },
  });
  expect(login.status()).toBe(200);
  await page.goto("/platform/models");
  const panel = page.getByRole("region", { name: "可靠性模拟测试" });
  await expect(panel.getByRole("button", { name: "运行场景" })).toBeEnabled();
  for (const scenario of [
    "success",
    "rate_limit",
    "unavailable",
    "exhausted",
    "authentication",
    "timeout",
    "interrupted",
    "schema_invalid",
    "cancel",
    "concurrency",
    "cleanup_failed",
  ]) {
    await page.getByLabel("可靠性演示场景").selectOption(scenario);
    await panel.getByRole("button", { name: "运行场景" }).click();
    await expect(panel.getByRole("status")).toContainText("场景符合预期", { timeout: 30000 });
    if (scenario === "rate_limit") {
      await expect(panel).toContainText("Attempt 2");
      await expect(panel).toContainText("未知");
    }
    if (scenario === "interrupted") {
      await expect(page.getByLabel("演示输出预览")).toContainText("正在分析销售数据");
      await expect(panel).toContainText("结果未提交");
    }
    if (scenario === "concurrency") {
      await expect(panel).toContainText("2 / 2");
      await expect(panel.locator(".reliability-calls details")).toHaveCount(20);
    }
  }
  await panel.screenshot({ path: "../../work/991/showcase-panel.png" });
  await page.screenshot({ path: "../../work/991/showcase-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(panel.getByRole("button", { name: "运行场景" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: "../../work/991/showcase-mobile.png", fullPage: true });
});

test("tenant admin cannot run the reliability laboratory", async ({ context }) => {
  const login = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: { loginName: "teaching.admin", password },
  });
  expect(login.status()).toBe(200);
  const session = await context.request.get("/api/web/v1/session");
  const csrf = (await session.json()).csrfToken;
  const catalog = await context.request.get("/api/web/v1/platform/models/reliability/scenarios");
  expect(catalog.status()).toBe(403);
  const run = await context.request.post("/api/web/v1/platform/models/reliability/runs", {
    headers: { origin, "X-CSRF-Token": csrf },
    data: { call_id: crypto.randomUUID(), scenario: "success" },
  });
  expect(run.status()).toBe(403);
});
