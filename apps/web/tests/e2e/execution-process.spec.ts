import { expect, test } from "@playwright/test";

const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;

test("执行时间线在桌面与窄屏支持键盘、详情和完成后重新展开", async ({ page, context }) => {
  const login = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: {
      loginName: "teaching.admin",
      password: process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password",
    },
  });
  expect(login.status()).toBe(200);
  await page.goto("/guide#ui-execution");
  const guide = page.locator("#ui-execution");
  await expect(guide).toContainText("协议与组件示例");
  const toggle = guide.locator(".analysis-process-toggle");
  const list = guide.getByRole("list", { name: "执行过程" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.press("Enter");
  const rows = list.locator(":scope > li");
  await expect(rows).toHaveCount(10);
  const group = list.getByRole("button", { name: /商品贡献与口径复核/ });
  await group.press("Space");
  await expect(list.getByText("并行", { exact: true })).toBeVisible();

  const tool = list.getByRole("button", { name: /查询商品明细/ });
  await tool.press("Enter");
  await expect(tool).toHaveAttribute("aria-expanded", "true");
  await expect(list.getByText("MCP · Retail 数据服务")).toBeVisible();
  await list.getByRole("button", { name: /执行 Python 复核/ }).press("Enter");
  await expect(list.getByText("Sandbox · Python")).toBeVisible();
  await guide.getByRole("button", { name: "失败", exact: true }).click();
  await expect(tool).toHaveAttribute("aria-expanded", "true");
  await expect(list.getByRole("button", { name: /独立口径复核/ })).toContainText("失败");

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator("main").evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  expect(await list.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  const result = list.getByRole("region", { name: "调用结果", exact: true });
  await result.getByRole("button", { name: "自动换行" }).click();
  expect(await result.locator("pre").evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await result.getByRole("button", { name: "复制代码" }).click();
  await expect(result.getByRole("button", { name: "复制代码" })).toContainText("已复制");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('"rowCount":3');

  await guide.getByRole("button", { name: "成功", exact: true }).click();
  await expect(toggle).toHaveText("已完成，用时 2 分 18 秒");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.press("Enter");
  await expect(tool).toHaveAttribute("aria-expanded", "true");
});
