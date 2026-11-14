import { expect, test, type Page } from "@playwright/test";

const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
const providerId = "40000000-0000-0000-0000-000000000004";
const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const query = new URLSearchParams({ tenantId, workspaceId }).toString();
test.skip(process.env.WEB_TEST_TOOL_RUNTIME !== "true", "Lesson 18 real backend fixture required");
test.setTimeout(120_000);

async function ask(page: Page, text: string) {
  await page.getByRole("textbox", { name: "分析问题" }).fill(text);
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
}

test.beforeEach(async ({ page, context }) => {
  expect(
    (
      await context.request.post("/api/web/v1/auth/login", {
        headers: { origin },
        data: { loginName: "teaching.admin", password: process.env.WEB_TEST_PASSWORD },
      })
    ).status(),
  ).toBe(200);
  const session = await context.request.get(`/api/web/v1/session?${query}&pageId=analysis-new`);
  expect(session.status()).toBe(200);
  const revision = session.headers()["x-compass-view-revision"];
  const supported = { state: "supported", reason: "Tool runtime fixture" };
  await page.route("**/api/web/v1/models/available?**", (route) =>
    route.fulfill({
      headers: { "X-Compass-View-Revision": revision, "Cache-Control": "no-store" },
      json: {
        items: [
          {
            id: providerId,
            alias: "课堂模型",
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
    }),
  );
  await page.goto(`/t/${tenantId}/w/${workspaceId}/analysis`);
});

test("真实 Retail 查询 → 独立 Python → 图表下载，执行树保留沙箱来源", async ({ page, context }) => {
  await ask(page, "并行查询门店渠道两个时段的商品实付金额，再用 Python 绘制图表");
  await expect(page.getByRole("region", { name: "最终回答" })).toContainText("Python 分析", {
    timeout: 60_000,
  });
  await expect(page.getByRole("region", { name: "最终回答" })).toContainText("-320.00");
  const chart = page.getByRole("img", { name: "Python 分析图表" });
  await expect(chart).toBeVisible();
  expect(
    await chart.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0),
  ).toBe(true);
  const src = await chart.getAttribute("src");
  expect(src).toBeTruthy();
  const response = await context.request.get(src!);
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("image/png");
  expect(response.headers()["cache-control"]).toContain("no-store");
  expect((await response.body()).subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  const unknown = src!.replace(
    /\/artifacts\/[a-f0-9-]+/,
    "/artifacts/00000000-0000-4000-8000-000000000000",
  );
  expect((await context.request.get(unknown)).status()).toBe(404);
  const originalPng = await response.body();
  await page.reload();
  await expect(chart).toBeVisible();
  const restored = await context.request.get((await chart.getAttribute("src"))!);
  expect(await restored.body()).toEqual(originalPng);
  await page.locator(".analysis-process-toggle").click();
  const python = page
    .getByRole("list", { name: "执行过程" })
    .getByRole("button", {
      name: /运行脚本 · (执行 Python 分析|生成分析图表)/,
    })
    .first();
  await python.click();
  await expect(page.getByText("Sandbox · Python", { exact: true })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载图表" }).click();
  expect((await download).suggestedFilename()).toBe("chart.png");
  await page.screenshot({
    path: "../../work/lesson-18-implementation/tool-runtime-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator("main").evaluate((main) => main.scrollWidth <= main.clientWidth)).toBe(
    true,
  );
  await page.screenshot({
    path: "../../work/lesson-18-implementation/tool-runtime-mobile.png",
    fullPage: true,
  });
});

test("本人 MCP 配置随任务加载，写操作待审，取消远端调用报告未知结局", async ({ page, context }) => {
  const session = await context.request.get(`/api/web/v1/session?${query}&pageId=analysis-new`);
  const csrf = (await session.json()).csrfToken;
  const existing = (
    await (await context.request.get(`/api/mcp/connections?${query}`)).json()
  ).items.find((item: { name: string }) => item.name.startsWith("Lesson18 "));
  const created = await context.request.fetch(
    `/api/mcp/connections${existing ? `/${existing.id}` : ""}?${query}`,
    {
      method: existing ? "PATCH" : "POST",
      headers: { origin, "X-CSRF-Token": csrf },
      data: {
        revision: existing?.revision ?? 0,
        enabled: true,
        name: `Lesson18 ${Date.now()}`,
        endpoint: "http://127.0.0.1:8129/mcp",
        selected_tools: "all",
        tool_policies: [
          { name: "add", effect: "read_only", approval: "none" },
          { name: "slow", effect: "read_only", approval: "none" },
        ],
      },
    },
  );
  expect(created.status()).toBe(existing ? 200 : 201);
  const connection = await created.json();
  await ask(page, "调用 MCP 求 2 加 3");
  await expect(page.getByRole("region", { name: "最终回答" })).toContainText("MCP 计算结果是 5");
  await page.locator(".analysis-process-toggle").click();
  await expect(
    page
      .getByRole("list", { name: "执行过程" })
      .getByRole("button", {
        name: /调用 MCP · add/,
      })
      .first(),
  ).toBeVisible();
  const clear = async () => {
    page.once("dialog", (dialog) => void dialog.accept());
    await page.getByRole("button", { name: "新建对话" }).click();
  };
  await clear();
  await ask(page, "先生成图表后审批 MCP 写操作");
  await expect(page.getByLabel("执行确认", { exact: true })).toContainText("等待你的决定");
  const waitingChart = page.getByRole("img", { name: "Python 分析图表" });
  await expect(waitingChart).toBeVisible();
  const waitingSrc = await waitingChart.getAttribute("src");
  const waitingBytes = await (await context.request.get(waitingSrc!)).body();
  await expect(page.getByText(/审批后调用 MCP 工具/).first()).toBeVisible();
  const beforeWrite = await (await context.request.get("http://127.0.0.1:8129/write-count")).json();
  await page.reload();
  await expect(page.getByRole("button", { name: "批准执行", exact: true })).toBeVisible();
  expect(await waitingChart.getAttribute("src")).toBe(waitingSrc);
  expect(await (await context.request.get(waitingSrc!)).body()).toEqual(waitingBytes);
  await page.getByRole("button", { name: "批准执行", exact: true }).click();
  await expect(page.getByRole("region", { name: "最终回答" })).toContainText("MCP 计算结果是 5");
  await page.reload();
  await expect(page.getByRole("region", { name: "最终回答" })).toContainText("MCP 计算结果是 5");
  expect(await (await context.request.get("http://127.0.0.1:8129/write-count")).json()).toEqual({
    count: beforeWrite.count + 1,
  });
  await clear();
  await ask(page, "调用 MCP 慢工具，然后取消");
  const toggle = page.locator(".analysis-process-toggle");
  if ((await toggle.getAttribute("aria-expanded")) === "false") await toggle.click();
  await expect(
    page
      .getByRole("list", { name: "执行过程" })
      .getByRole("button", {
        name: /调用 MCP · slow/,
      })
      .last(),
  ).toBeVisible();
  await page.getByRole("button", { name: "停止任务" }).click();
  await expect(
    page.getByText("执行资源正在核对，确认完成前无法继续。", { exact: true }),
  ).toBeVisible();
  expect(
    (
      await context.request.patch(`/api/mcp/connections/${connection.id}?${query}`, {
        headers: { origin, "X-CSRF-Token": csrf },
        data: {
          revision: connection.revision,
          name: connection.name,
          endpoint: connection.endpoint,
          enabled: false,
          selected_tools: "all",
          tool_policies: [],
        },
      })
    ).status(),
  ).toBe(200);
});
