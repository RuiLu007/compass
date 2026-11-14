import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import type { RunEvent } from "../../src/contracts/analysis-run";
import { agentResponse, type AgentResponse } from "../../src/contracts/agent-response";

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

async function send(page: Page, question: string) {
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/analysis/sessions/submit") && r.request().method() === "POST",
  );
  await page.getByLabel("分析问题").fill(question);
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const submitted = await response;
  expect(submitted.status(), await submitted.text()).toBe(202);
  const accepted = await submitted.json();
  let snapshot: AgentResponse | undefined;
  await expect
    .poll(async () => {
      const result = await page.request.get(
        `/api/web/v1/analysis/sessions/${accepted.sessionId}/messages`,
        {
          params: { tenantId, workspaceId },
        },
      );
      expect(result.status(), await result.text()).toBe(200);
      snapshot = agentResponse(await result.json());
      return snapshot.packets.find(
        (packet) => packet.runId === accepted.runId && packet.event.type === "run.upsert",
      )?.event;
    })
    .toMatchObject({ type: "run.upsert", payload: { status: "completed" } });
  const events = snapshot!.packets.flatMap<RunEvent>((packet) =>
    packet.runId === accepted.runId &&
    packet.event.type === "view.upsert" &&
    packet.event.payload.kind === "timeline"
      ? [packet.event.payload.value.event as RunEvent]
      : [],
  );
  const route = events.find((event) => event.type === "run.routed");
  const step = events.find((event) => event.type === "step.started");
  expect(step).toBeDefined();
  const record = await page.request.get(`/api/web/v1/models/calls/${step!.payload.modelCallId}`, {
    params: { tenantId, workspaceId },
  });
  expect(record.status(), await record.text()).toBe(200);
  return { route: route?.payload, record: await record.json() };
}

test("business questions resolve the scene and model through the analysis run", async ({
  page,
  context,
}) => {
  const providers = await configure(context);
  await login(context, "admin");
  await page.goto(analysisPath);
  await expect(page.getByLabel("选择 Chat 主模型")).toHaveValue("auto");
  const result = await send(page, "根据昨日销售100元、前日80元，比较销售额并简述。");
  await expect(page.getByRole("region", { name: "最终回答" })).toContainText("这是模型测试回复。");
  expect(result.route).toMatchObject({
    sceneId: "SC-02",
    providerId: providers.flash,
    modelLabel: expect.stringMatching(/^Routing flash /),
  });
  expect(result.record).toMatchObject({
    provider_id: providers.flash,
    requested_model: "deepseek-flash",
    status: "completed",
  });
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
  const result = await send(page, "你好呀，讲个笑话");
  await expect(page.getByRole("region", { name: "最终回答" })).toContainText(
    "你有没有想分析的销售、库存或其他业务数据任务？",
  );
  expect(result.route).toMatchObject({
    sceneId: "SC-39",
    providerId: providers.flash,
    modelLabel: expect.stringMatching(/^Routing flash /),
    toolsEnabled: false,
  });
  expect(result.record).toMatchObject({
    provider_id: providers.flash,
    requested_model: "deepseek-flash",
    status: "completed",
  });
  for (const width of [1440, 768, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({ path: `../../work/992/off-topic-${width}.png`, fullPage: true });
  }
});
