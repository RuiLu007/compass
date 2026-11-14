import { expect, test, type Page } from "@playwright/test";

const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
const providerId = "40000000-0000-0000-0000-000000000004";
const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const analysisPath = `/t/${tenantId}/w/${workspaceId}/analysis`;
const password = process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password";
const completed = expect.configure({ timeout: 45_000 });

test.skip(
  process.env.WEB_TEST_WORKFLOW !== "true",
  "Lesson 17 isolated Agent/Retail fixture required",
);

async function send(page: Page) {
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
}

test("生成并保存 JS、改名和启停、换时段复用、固定提案和脚本周报", async ({ page, context }) => {
  test.setTimeout(180_000);
  const login = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: { loginName: "teaching.admin", password },
  });
  expect(login.status()).toBe(200);
  const session = await context.request.get(
    `/api/web/v1/session?${new URLSearchParams({ tenantId, workspaceId, pageId: "analysis-new" })}`,
  );
  expect(session.status()).toBe(200);
  const revision = session.headers()["x-compass-view-revision"];
  const supported = { state: "supported", reason: "Workflow fixture" };
  await page.route("**/api/web/v1/models/available?**", async (route) => {
    await route.fulfill({
      headers: { "X-Compass-View-Revision": revision, "Cache-Control": "no-store" },
      json: {
        items: [
          {
            id: providerId,
            alias: "Retail Pro",
            api_model: "fixture",
            kind: "llm",
            capabilities: {
              sync: supported,
              stream: supported,
              tools: supported,
              thinking: supported,
              json_output: supported,
              schema_output: supported,
              reasoning_efforts: [],
            },
          },
        ],
      },
    });
  });
  await page.goto(analysisPath);
  await page.getByRole("button", { name: "渠道异动诊断", exact: true }).click();
  await send(page);
  await completed(page.getByRole("region", { name: "最终回答" })).toContainText("独立复核");
  await completed(page.getByRole("region", { name: "最终回答" })).toContainText("320.00");

  const saveCard = page.getByRole("region", { name: "保存工作流", exact: true });
  await expect(saveCard).toContainText("要把本次编排保存");
  const assetsPath = `/api/web/v1/analysis/workflows?${new URLSearchParams({ tenantId, workspaceId })}`;
  const before = await context.request.get(assetsPath);
  expect(before.ok()).toBe(true);
  expect((await before.json()).items).toEqual([]);
  await saveCard.getByRole("button", { name: "查看工作流详情" }).click();
  await expect(saveCard.locator("pre")).toContainText("await parallel");
  await expect(saveCard).toContainText("2025-11-17");
  expect((await (await context.request.get(assetsPath)).json()).items).toEqual([]);
  await saveCard.getByLabel("工作流名称").fill("渠道异动诊断");
  await saveCard.getByRole("button", { name: "确认保存为工作流" }).click();
  await expect(saveCard.getByRole("status")).toContainText("已保存");
  const pluginOpened = context.waitForEvent("page");
  await saveCard.getByRole("link", { name: "在新标签页打开插件 → Workflow" }).click();
  const plugin = await pluginOpened;
  const library = plugin.getByRole("region", { name: "Workflow" });
  const row = library.getByRole("button", { name: "渠道异动诊断 · v1 · 已启用" });
  await row.press("Enter");
  await expect(library.getByRole("heading", { name: "渠道异动诊断" })).toBeFocused();
  await expect(row).toHaveAttribute("aria-current", "true");
  await library.getByText("查看脚本", { exact: true }).click();
  await expect(library.locator("pre")).toContainText("await parallel");
  await plugin.setViewportSize({ width: 390, height: 820 });
  await library.getByRole("button", { name: "自动换行" }).click();
  expect(
    await library.locator("pre").evaluate((code) => code.scrollWidth === code.clientWidth),
  ).toBe(true);
  expect(
    await plugin.locator("main").evaluate((main) => main.scrollWidth === main.clientWidth),
  ).toBe(true);
  await library.getByLabel("工作流名称").fill("渠道每周核对");
  await library.getByRole("button", { name: "停用", exact: true }).click();
  await expect(library.getByRole("button", { name: "渠道异动诊断 · v1 · 已停用" })).toBeVisible();
  await expect(library.getByLabel("工作流名称")).toHaveValue("渠道每周核对");
  await library.getByRole("button", { name: "改名", exact: true }).click();
  await expect(library.getByRole("button", { name: "渠道每周核对 · v1 · 已停用" })).toBeVisible();

  await expect(page.getByText("我的工作流", { exact: true })).toHaveCount(0);
  await expect(library.getByRole("button", { name: "使用", exact: true })).toBeDisabled();
  await library.getByRole("button", { name: "重新启用", exact: true }).click();
  await expect(library.getByRole("button", { name: "使用", exact: true })).toBeEnabled();
  await expect(library.getByRole("button", { name: /渠道每周核对 · v1 · 已启用/ })).toBeVisible();
  await library.getByLabel("基准期开始").fill("2025-11-10");
  await library.getByLabel("基准期结束（不含）").fill("2025-11-17");
  await library.getByLabel("本期开始").fill("2025-11-17");
  await library.getByLabel("本期结束（不含）").fill("2025-11-24");
  await library.getByRole("button", { name: "使用", exact: true }).click();
  await expect(plugin.getByLabel("分析问题")).toHaveValue(
    "使用已保存的工作流「渠道每周核对」分析所选时段。",
  );
  await send(plugin);
  await completed(plugin.getByRole("region", { name: "最终回答" }).last()).toContainText("150.00");
  await plugin.close();

  const clear = async () => {
    page.once("dialog", (dialog) => void dialog.accept());
    await page.getByRole("button", { name: "新建对话" }).click();
  };
  await clear();
  await page
    .getByRole("textbox", { name: "分析问题" })
    .fill(
      "对比 2025-11-10 至 2025-11-16 和 2025-11-17 至 2025-11-23，找出实付额变化最大的渠道，再并行拆解前三项商品贡献并独立复核。",
    );
  const runRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" && request.url().endsWith("/api/web/v1/analysis/sessions/submit"),
  );
  await send(page);
  const automatic = (await runRequest).postDataJSON();
  expect(automatic.history).toBeUndefined();
  expect(automatic.sessionId).toBeNull();
  expect(automatic.workflowAssetRef).toBeUndefined();
  expect(automatic.showcase).toBeUndefined();
  await completed(page.getByRole("region", { name: "最终回答" })).toContainText("150.00");
  const process = page.locator(".analysis-process-toggle");
  await expect(process).toHaveAttribute("aria-expanded", "false");
  await process.press("Enter");
  const execution = page.getByRole("list", { name: "执行过程", exact: true });
  await expect(execution.locator(':scope > [data-kind="subagent"]')).toHaveCount(3);
  const workflow = execution.getByRole("button", { name: /工作流 · .*渠道每周核对/ }).first();
  await workflow.press("Enter");
  await expect(execution).toContainText("已复用「渠道每周核对」 · v1");
  const queryTool = execution.getByRole("button", { name: /查询数据 · 比较渠道实付金额/ }).first();
  await queryTool.press("Enter");
  const queryRow = execution.locator(':scope > [data-kind="tool"]').filter({ has: queryTool });
  await expect(queryRow.getByRole("region", { name: "调用入参" })).toContainText(
    '"dimension": "channel"',
  );
  await expect(queryRow.getByRole("region", { name: "调用结果" })).toContainText('"rowCount"');
  await expect(page.getByRole("region", { name: "保存工作流", exact: true })).toHaveCount(0);
  expect((await (await context.request.get(assetsPath)).json()).items).toHaveLength(1);

  await expect(page.getByRole("button", { name: "广告预算提案", exact: true })).toHaveCount(0);

  await clear();
  await page.getByRole("button", { name: "每周经营复盘", exact: true }).click();
  await send(page);
  await completed(page.getByRole("region", { name: "最终回答" })).toContainText("下周跟踪");
});
