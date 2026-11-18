import { writeFile } from "node:fs/promises";
import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
  type TestInfo,
} from "@playwright/test";
import {
  agentResponse,
  reduceAgentResponse,
  type AgentResponse,
  type Conversation,
} from "../../src/contracts/agent-response";
import { parseModelContract } from "../../src/contracts/model-contract";
import type { components } from "../../src/generated/api";

// The model catalog is fixed for the controlled provider. Every analysis operation uses
// the isolated BFF, Agent, PostgreSQL, Redis and Agent's in-process consumer.
const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
const scope = new URLSearchParams({ tenantId, workspaceId });
const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const analysisPath = `/t/${tenantId}/w/${workspaceId}/analysis`;
type Accepted = components["schemas"]["SessionAccepted"];
type Timeline = components["schemas"]["RuntimeTimeline"];
type Usage = components["schemas"]["RuntimeUsage"];
type Scheduling = components["schemas"]["SchedulingPublic"];

test.skip(
  process.env.WEB_TEST_WORKFLOW !== "true",
  "Isolated Workflow provider and Agent consumer required",
);
test.use({ storageState: process.env.WEB_TEST_STORAGE_STATE });

async function enter(page: Page, context: BrowserContext, entry = analysisPath) {
  const login = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: {
      loginName: "teaching.admin",
      password: process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password",
    },
  });
  expect(login.status()).toBe(200);
  const session = await context.request.get(`/api/web/v1/session?${scope}&pageId=analysis-new`);
  expect(session.status()).toBe(200);
  const revision = session.headers()["x-compass-view-revision"];
  expect(revision).toBeTruthy();
  const supported = { state: "supported", reason: "Workflow controlled provider" };
  await page.route("**/api/web/v1/models/available?**", (route) =>
    route.fulfill({
      headers: { "X-Compass-View-Revision": revision, "Cache-Control": "no-store" },
      json: {
        items: [
          {
            id: "40000000-0000-0000-0000-000000000004",
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
    }),
  );
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname.startsWith("/api/web/v1/analysis/")
    )
      mutations.push(new URL(request.url()).pathname);
  });
  await page.goto(entry);
  if (entry === analysisPath) await expect(page.getByLabel("选择 Chat 主模型")).toBeEnabled();
  return mutations;
}

async function submit(page: Page, question: string): Promise<Accepted> {
  await page.getByRole("textbox", { name: "分析问题" }).fill(question);
  const accepted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/web/v1/analysis/sessions/submit",
  );
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const response = await accepted;
  expect(response.status()).toBe(202);
  expect(response.headers()["cache-control"]).toContain("no-store");
  const receipt = parseModelContract<Accepted>("SessionAccepted", await response.json());
  await expect(page).toHaveURL(new RegExp(`session=${receipt.sessionId}`));
  return receipt;
}

async function snapshot(context: BrowserContext, receipt: Accepted) {
  const response = await context.request.get(
    `/api/web/v1/analysis/sessions/${receipt.sessionId}/messages?${scope}`,
  );
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toContain("no-store");
  const result = agentResponse(await response.json());
  expect(result.sessionId).toBe(receipt.sessionId);
  expect(result.mode).toBe("snapshot");
  expect(Object.keys(reduceAgentResponse(null, result).runs)).toEqual([receipt.runId]);
  expect(JSON.stringify(result)).not.toMatch(/"(?:bootId|workerId|epoch|privateCheckpoint)"/);
  return result;
}

async function platform(request: APIRequestContext) {
  const response = await request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: {
      loginName: "teaching.platform",
      password: process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password",
    },
  });
  expect(response.status()).toBe(200);
}

async function timeline(request: APIRequestContext, runId: string) {
  const response = await request.get(`/api/web/v1/analysis/runs/${runId}/timeline?${scope}`);
  expect(response.status()).toBe(200);
  const result = parseModelContract<Timeline>("RuntimeTimeline", await response.json());
  expect(result.runId).toBe(runId);
  expect(new Set(result.entries.map((entry) => entry.id)).size).toBe(result.entries.length);
  return result;
}

async function usage(context: BrowserContext) {
  const response = await context.request.get(`/api/web/v1/analysis/runtime/usage?${scope}`);
  expect(response.status()).toBe(200);
  const result = parseModelContract<Usage>("RuntimeUsage", await response.json());
  expect(result.tenantId).toBe(tenantId);
  expect(result.occupied).toBe(result.jobs.leased + result.jobs.reclaiming);
  return result;
}

function scheduling(value: AgentResponse) {
  return value.packets
    .flatMap((packet) => {
      const event = packet.event;
      if (event.type !== "view.upsert" || event.payload.kind !== "timeline") return [];
      const progress = event.payload.value.event;
      return progress.type === "stream.extension" &&
        progress.payload.name === "compass.runtime.scheduling"
        ? [
            {
              ordinal: event.payload.value.ordinal,
              data: parseModelContract<Scheduling>("SchedulingPublic", progress.payload.data),
            },
          ]
        : [];
    })
    .sort((left, right) => left.ordinal - right.ordinal)
    .map((entry) => entry.data);
}

async function record(testInfo: TestInfo, value: unknown) {
  const path = testInfo.outputPath("runtime-business-result.json");
  await writeFile(path, JSON.stringify(value, null, 2));
  await testInfo.attach("runtime-business-result", {
    path,
    contentType: "application/json",
  });
}

async function replay(context: BrowserContext, receipt: Accepted, history: AgentResponse) {
  const response = await context.request.get(
    `/api/web/v1/analysis/runs/${receipt.runId}/events?${scope}&after=0-0`,
  );
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("text/event-stream");
  const frames = (await response.text())
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => agentResponse(JSON.parse(line.slice(6))));
  expect(frames.length).toBeGreaterThan(0);
  let live: Conversation = {
    sessionId: receipt.sessionId,
    nextCursor: null,
    runs: {
      [receipt.runId]: {
        runId: receipt.runId,
        requestId: "",
        cursor: "0-0",
        messages: {},
        objects: {},
      },
    },
  };
  for (const frame of frames) {
    expect(frame.mode).toBe("delta");
    live = reduceAgentResponse(live, frame);
    expect(reduceAgentResponse(live, frame)).toEqual(live);
  }
  expect(live).toEqual(reduceAgentResponse(null, history));
  return frames;
}

test("真实 Agent 槽位接单，刷新恢复同一运行及执行历史", async ({
  page,
  context,
  request,
}, testInfo) => {
  const mutations = await enter(page, context);
  await platform(request);
  const receipt = await submit(page, "汇总本周经营数据并说明主要变化。");
  await page.reload();
  const answer = page.getByRole("region", { name: "最终回答" });
  await expect(answer).toContainText("查询与分析完成。");
  await expect(page.locator(`[data-run-id="${receipt.runId}"]`)).toBeVisible();
  await expect(page.locator("[data-run-id]")).toHaveCount(1);
  const before = await snapshot(context, receipt);
  const schedule = scheduling(before);
  expect(schedule.map((event) => event.reasonCode)).toEqual(["queued", "claimed", "done"]);
  expect(schedule.map((event) => event.claimCount)).toEqual([0, 1, 1]);
  const run = reduceAgentResponse(null, before).runs[receipt.runId];
  expect(run.run?.status).toBe("completed");
  expect(
    Object.values(run.messages).filter((message) => message.role === "assistant"),
  ).toMatchObject([{ status: "completed" }]);
  expect(
    (
      await context.request.get(`/api/web/v1/analysis/runs/${receipt.runId}/timeline?${scope}`)
    ).status(),
  ).toBe(403);
  const diagnostic = await timeline(request, receipt.runId);
  expect(
    diagnostic.entries.filter(
      (entry) => entry.kind === "command" && entry.status === "worker_advance",
    ).length,
  ).toBeGreaterThan(0);
  const sse = await replay(context, receipt, before);
  await page.reload();
  await expect(answer).toContainText("查询与分析完成。");
  await expect(answer).toHaveCount(1);
  expect(await snapshot(context, receipt)).toEqual(before);
  expect(await timeline(request, receipt.runId)).toEqual(diagnostic);
  expect(mutations).toEqual(["/api/web/v1/analysis/sessions/submit"]);
  await record(testInfo, {
    receipt,
    schedule,
    diagnostic,
    sse,
    snapshot: before,
    usage: await usage(context),
  });
});

test("预算问题由模型回答且无需课堂审批", async ({ page, context }) => {
  await enter(page, context);
  await expect(page.getByRole("button", { name: "广告预算提案", exact: true })).toHaveCount(0);
  const accepted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/web/v1/analysis/sessions/submit",
  );
  await page.getByRole("textbox", { name: "分析问题" }).fill("帮我准备广告预算方案");
  await page.getByRole("button", { name: "发送消息" }).click();
  const response = await accepted;
  expect(response.status()).toBe(202);
  const receipt = parseModelContract<Accepted>("SessionAccepted", await response.json());
  await expect(page.getByRole("region", { name: "最终回答" })).not.toBeEmpty();
  await expect(page.getByLabel("执行确认", { exact: true })).toHaveCount(0);
  const state = reduceAgentResponse(null, await snapshot(context, receipt));
  expect(state.runs[receipt.runId].run?.status).toBe("completed");
});

test("定时任务真实预览、保存与键盘删除，保留授权历史", async ({ page, context }) => {
  const automations = `/t/${tenantId}/w/${workspaceId}/automations`;
  const name = `定时验收 ${crypto.randomUUID()}`;
  await page.setViewportSize({ width: 390, height: 820 });
  await enter(page, context, `${automations}/new`);
  await page.getByLabel("任务名称").fill(name);
  await page.getByLabel("分析内容").fill("汇总本周经营数据并说明主要变化。");
  await page.getByRole("button", { name: "预览触发时间" }).click();
  await expect(page.getByText(/预计触发时间 · Asia\/Shanghai/)).toBeVisible();
  await expect(page.getByText(/自动选模在运行时选择路由。/)).toBeVisible();
  const saved = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/web/v1/analysis/schedules",
  );
  await page.getByRole("button", { name: "保存任务", exact: true }).click();
  const receipt = await saved;
  expect(receipt.status()).toBe(201);
  const { id } = parseModelContract<components["schemas"]["ScheduleWriteReceipt"]>(
    "ScheduleWriteReceipt",
    await receipt.json(),
  );
  await page.getByRole("link", { name: "返回全部任务" }).click();
  const remove = page.getByRole("row").filter({ hasText: name }).getByRole("button", {
    name: "删除",
    exact: true,
  });
  await remove.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "取消", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(remove).toBeFocused();
  await remove.press("Enter");
  await dialog.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(remove).toHaveCount(0);
  await page.goto(`${automations}/history?schedule=${id}`);
  await expect(page.getByText("此任务暂无执行记录。")).toBeVisible();
});
