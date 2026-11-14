import { test, expect, type BrowserContext, type Page, type Response } from "@playwright/test";
import type { RunEvent } from "../../src/contracts/analysis-run";
import { agentResponse } from "../../src/contracts/agent-response";

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

function waitForRun(page: Page) {
  return page.waitForResponse(
    (r) => r.url().endsWith("/analysis/sessions/submit") && r.request().method() === "POST",
  );
}

async function modelCallId(context: BrowserContext, response: Response) {
  expect(response.status(), await response.text()).toBe(202);
  const accepted = (await response.json()) as { sessionId: string; runId: string };
  let identifier: string | undefined;
  await expect
    .poll(async () => {
      const messages = await context.request.get(
        `/api/web/v1/analysis/sessions/${accepted.sessionId}/messages`,
        { params: { tenantId, workspaceId } },
      );
      expect(messages.status(), await messages.text()).toBe(200);
      const snapshot = agentResponse(await messages.json());
      const events = snapshot.packets.flatMap<RunEvent>((packet) =>
        packet.runId === accepted.runId &&
        packet.event.type === "view.upsert" &&
        packet.event.payload.kind === "timeline"
          ? [packet.event.payload.value.event as RunEvent]
          : [],
      );
      identifier = events.find((event) => event.type === "step.started")?.payload.modelCallId;
      return identifier;
    })
    .toMatch(/^[a-f0-9-]{36}$/);
  return identifier!;
}

async function callRecord(context: BrowserContext, callId: string) {
  const response = await context.request.get(`/api/web/v1/models/calls/${callId}`, {
    params: { tenantId, workspaceId },
  });
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}

test("completed and canceled runs retain trace and model-call correlation", async ({
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
  const response = waitForRun(page);
  await page.getByLabel("分析问题").fill("比较昨日销售额");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const completedRun = await response;
  const headers = completedRun.headers();
  const trace = headers["x-compass-trace-id"];
  expect(trace).toMatch(/^[0-9a-f]{32}$/);
  expect(headers["x-compass-trace-sampled"]).toBe("1");
  const turn = page.locator(".model-chat-turn").last();
  await expect(turn.getByRole("region", { name: "最终回答" })).toBeVisible();
  const completedCallId = await modelCallId(context, completedRun);
  expect(await callRecord(context, completedCallId)).toMatchObject({
    call_id: completedCallId,
    status: "completed",
  });
  const slowResponse = waitForRun(page);
  await page.getByLabel("分析问题").fill("比较销售额 E2E_SLOW");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const canceledRun = await slowResponse;
  const slowTrace = canceledRun.headers()["x-compass-trace-id"];
  expect(slowTrace).toMatch(/^[0-9a-f]{32}$/);
  expect(slowTrace).not.toBe(trace);
  const canceledCallId = await modelCallId(context, canceledRun);
  await expect
    .poll(async () => {
      const record = await context.request.get(`/api/web/v1/models/calls/${canceledCallId}`, {
        params: { tenantId, workspaceId },
      });
      return record.status() === 200 ? (await record.json()).status : null;
    })
    .toBe("running");
  await expect(turn.locator(".analysis-run-header").getByRole("status")).toHaveText(
    /正在理解问题|正在分析问题|执行中|正在输出回答/,
  );
  await page.getByRole("button", { name: "停止任务", exact: true }).click();
  await expect(turn.getByText("任务已终止", { exact: true })).toBeVisible();
  await expect(turn).not.toContainText(/调用编号|观测编号|调用信息/);
  expect(await callRecord(context, canceledCallId)).toMatchObject({
    call_id: canceledCallId,
    status: "canceled",
  });
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
