import { test, expect, type BrowserContext, type Page } from "@playwright/test";

// These are UI scenarios: login/authorization use the real Web app; model responses are local.
const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
const providerId = "99300000-0000-4000-8000-000000000101";
const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const analysisPath = `/t/${tenantId}/w/${workspaceId}/analysis`;
const password = process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password";
type AnalysisRequest = {
  requestId: string;
  tenantId: string;
  workspaceId: string;
  question: string;
  history: { question: string; answer: string }[];
  selection: { mode: "auto" | "fixed"; provider_id: string | null };
};
type InteractiveRunScenario =
  | "canceled_confirmed"
  | "cleanup_unconfirmed"
  | "already_terminal_completed"
  | "already_terminal_disconnected";

async function enter(page: Page, context: BrowserContext) {
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
  expect(revision).toBeTruthy();
  const supported = { state: "supported", reason: "UI fixture" };
  const requests: AnalysisRequest[] = [];
  let release: (() => void) | undefined;
  let hold = false;
  await page.route("**/api/web/v1/models/available?**", async (route) => {
    await route.fulfill({
      headers: { "X-Compass-View-Revision": revision, "Cache-Control": "no-store" },
      json: {
        items: [
          {
            id: providerId,
            alias: "DeepSeek V4 Pro",
            api_model: "deepseek-v4-pro",
            kind: "llm",
            capabilities: {
              sync: supported,
              stream: supported,
              tools: supported,
              thinking: supported,
              json_output: supported,
              schema_output: supported,
              reasoning_efforts: ["low", "high", "max"],
            },
          },
        ],
      },
    });
  });
  await page.route("**/api/web/v1/analysis/runs/*/cancel", async (route) => {
    const runId = route.request().url().split("/").at(-2)!;
    await route.fulfill({
      headers: { "X-Compass-View-Revision": revision, "Cache-Control": "no-store" },
      json: { runId, status: "accepted" },
    });
  });
  await page.route("**/api/web/v1/analysis/runs", async (route) => {
    const request = route.request().postDataJSON() as AnalysisRequest;
    requests.push(request);
    if (hold) {
      hold = false;
      await new Promise<void>((resolve) => (release = resolve));
    }
    const runId = `98900000-0000-4000-8000-${String(200 + requests.length).padStart(12, "0")}`;
    const channelStepId = "98900000-0000-4000-8000-000000000301";
    const productStepId = "98900000-0000-4000-8000-000000000302";
    const channelModelCallId = "98900000-0000-4000-8000-000000000303";
    const productModelCallId = "98900000-0000-4000-8000-000000000304";
    const answerStepId = "98900000-0000-4000-8000-000000000305";
    const answerModelCallId = "98900000-0000-4000-8000-000000000306";
    const text = Array.from(
      { length: 55 },
      (_, index) => `${index + 1}. 销售分析示例：先比较渠道表现，再核对商品与库存变化。`,
    ).join("\n");
    const csv = "product,amount\n示例商品,120\n";
    const events = [
      { type: "run.started", payload: {} },
      {
        type: "run.routed",
        payload: {
          providerId,
          modelLabel: "DeepSeek V4 Pro",
          sceneId: "SC-02",
          toolsEnabled: true,
        },
      },
      {
        type: "step.started",
        payload: { stepId: channelStepId, stepIndex: 1, modelCallId: channelModelCallId },
      },
      {
        type: "tool.started",
        payload: {
          stepId: channelStepId,
          toolCallId: "channel_query",
          toolIndex: 1,
          toolKind: "retail_sales",
          params: {
            dimension: "channel",
            period: { start: "2025-11-24", end: "2025-11-30" },
            compareTo: { start: "2025-11-17", end: "2025-11-23" },
          },
        },
      },
      {
        type: "tool.finished",
        payload: {
          stepId: channelStepId,
          toolCallId: "channel_query",
          toolIndex: 1,
          status: "ok",
          rowCount: 2,
        },
      },
      { type: "step.finished", payload: { stepId: channelStepId, status: "completed" } },
      {
        type: "step.started",
        payload: { stepId: productStepId, stepIndex: 2, modelCallId: productModelCallId },
      },
      {
        type: "tool.started",
        payload: {
          stepId: productStepId,
          toolCallId: "product_query",
          toolIndex: 1,
          toolKind: "retail_sales",
          params: {
            dimension: "product",
            channelCode: "POS",
            period: { start: "2025-11-24", end: "2025-11-30" },
            compareTo: { start: "2025-11-17", end: "2025-11-23" },
          },
        },
      },
      {
        type: "tool.finished",
        payload: {
          stepId: productStepId,
          toolCallId: "product_query",
          toolIndex: 1,
          status: "ok",
          rowCount: 1,
        },
      },
      { type: "step.finished", payload: { stepId: productStepId, status: "completed" } },
      {
        type: "step.started",
        payload: { stepId: answerStepId, stepIndex: 3, modelCallId: answerModelCallId },
      },
      { type: "step.finished", payload: { stepId: answerStepId, status: "completed" } },
      {
        type: "run.finished",
        payload: {
          status: "completed",
          finalAnswer: text,
          file: {
            status: "ready",
            stepId: productStepId,
            toolCallId: "product_query",
            filename: `retail-products-${runId}.csv`,
            mediaType: "text/csv; charset=utf-8",
            rowCount: 1,
            byteLength: Buffer.byteLength(csv),
            contentBase64: Buffer.from(csv).toString("base64"),
          },
        },
      },
    ];
    await route.fulfill({
      headers: {
        "Content-Type": "text/event-stream",
        "X-Compass-View-Revision": revision,
        "Cache-Control": "no-store",
      },
      body: events
        .map(
          (event, index) =>
            `data: ${JSON.stringify({
              protocol: "compass.run",
              version: 1,
              requestId: request.requestId,
              runId,
              seq: index + 1,
              event,
            })}\n\n`,
        )
        .join(""),
    });
  });
  await page.goto(analysisPath);
  await expect(page.locator("[data-protected-root]")).toBeVisible();
  await expect(page.getByLabel("选择 Chat 主模型")).toBeEnabled();
  return {
    revision,
    requests,
    holdNext() {
      hold = true;
    },
    release() {
      release?.();
      release = undefined;
    },
  };
}

async function installInteractiveRunFixture(
  page: Page,
  revision: string,
  scenario: InteractiveRunScenario,
) {
  await page.evaluate(
    ({ providerId, revision, scenario }) => {
      const nativeFetch = window.fetch.bind(window);
      const runId = "98900000-0000-4000-8000-000000000901";
      const stepId = "98900000-0000-4000-8000-000000000902";
      const modelCallId = "98900000-0000-4000-8000-000000000903";
      const toolCallId = "interactive_channel_query";
      const stats: { cancelCount: number; releaseTerminal: () => void } = {
        cancelCount: 0,
        releaseTerminal: () => undefined,
      };
      let notifyCancel!: () => void;
      const cancelSeen = new Promise<void>((resolve) => {
        notifyCancel = resolve;
      });
      let notifyTerminal!: () => void;
      const terminalReleased = new Promise<void>((resolve) => {
        notifyTerminal = resolve;
      });
      stats.releaseTerminal = notifyTerminal;
      Reflect.set(window, "__compassAnalysisFixtureStats", stats);

      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(
          input instanceof Request ? input.url : String(input),
          window.location.href,
        );
        if (/\/api\/web\/v1\/analysis\/runs\/[a-f0-9-]{36}\/cancel$/.test(url.pathname)) {
          stats.cancelCount += 1;
          notifyCancel();
          const alreadyTerminal = scenario.startsWith("already_terminal");
          return Response.json(
            alreadyTerminal
              ? { runId, status: "already_terminal", terminalStatus: "completed" }
              : { runId, status: "accepted" },
            {
              status: alreadyTerminal ? 200 : 202,
              headers: {
                "Cache-Control": "private, no-store",
                "X-Compass-View-Revision": revision,
              },
            },
          );
        }
        if (url.pathname !== "/api/web/v1/analysis/runs" || init?.method !== "POST")
          return nativeFetch(input, init);

        const raw = typeof init.body === "string" ? init.body : "{}";
        const request = JSON.parse(raw) as { requestId: string };
        const encoder = new TextEncoder();
        let sequence = 0;
        const stream = new ReadableStream<Uint8Array>({
          async start(controller) {
            const send = (event: { type: string; payload: unknown }) => {
              sequence += 1;
              controller.enqueue(
                encoder.encode(
                  `data: ${JSON.stringify({
                    protocol: "compass.run",
                    version: 1,
                    requestId: request.requestId,
                    runId,
                    seq: sequence,
                    event,
                  })}\n\n`,
                ),
              );
            };
            const finishToolAndStep = () => {
              send({
                type: "tool.finished",
                payload: {
                  stepId,
                  toolCallId,
                  toolIndex: 1,
                  status: "ok",
                  rowCount: 1,
                },
              });
              send({ type: "step.finished", payload: { stepId, status: "completed" } });
            };
            try {
              send({ type: "run.started", payload: {} });
              send({
                type: "run.routed",
                payload: {
                  providerId,
                  modelLabel: "DeepSeek V4 Pro",
                  sceneId: "SC-02",
                  toolsEnabled: true,
                },
              });
              send({
                type: "step.started",
                payload: { stepId, stepIndex: 1, modelCallId },
              });
              send({
                type: "tool.started",
                payload: {
                  stepId,
                  toolCallId,
                  toolIndex: 1,
                  toolKind: "retail_sales",
                  params: {
                    dimension: "channel",
                    period: { start: "2025-11-24", end: "2025-11-30" },
                    compareTo: null,
                  },
                },
              });
              await cancelSeen;
              if (scenario === "canceled_confirmed") {
                send({ type: "step.finished", payload: { stepId, status: "canceled" } });
                await terminalReleased;
                send({
                  type: "run.finished",
                  payload: { status: "canceled", reason: "user", cleanup: "confirmed" },
                });
              } else if (scenario === "cleanup_unconfirmed") {
                send({ type: "step.finished", payload: { stepId, status: "canceled" } });
                await terminalReleased;
                send({
                  type: "run.finished",
                  payload: {
                    status: "cleanup_unconfirmed",
                    reason: "execution_error",
                    cleanup: "unconfirmed",
                  },
                });
              } else if (scenario === "already_terminal_completed") {
                finishToolAndStep();
                await terminalReleased;
                send({
                  type: "run.finished",
                  payload: {
                    status: "completed",
                    finalAnswer: "原结果流确认后的完整回答。",
                    file: { status: "none" },
                  },
                });
              } else {
                finishToolAndStep();
                await terminalReleased;
              }
              controller.close();
            } catch (error) {
              controller.error(error);
            }
          },
        });
        return new Response(stream, {
          status: 200,
          headers: {
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "private, no-store",
            "X-Compass-View-Revision": revision,
          },
        });
      };
    },
    { providerId, revision, scenario },
  );
}

async function fixtureCancelCount(page: Page) {
  return page.evaluate(
    () =>
      (Reflect.get(window, "__compassAnalysisFixtureStats") as { cancelCount: number }).cancelCount,
  );
}

async function releaseFixtureTerminal(page: Page) {
  await page.evaluate(() => {
    const fixture = Reflect.get(window, "__compassAnalysisFixtureStats") as {
      releaseTerminal: () => void;
    };
    fixture.releaseTerminal();
  });
}

async function expectNoOverflow(page: Page) {
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
  const form = await page.locator(".analysis-composer").boundingBox();
  expect(form).not.toBeNull();
  expect(form!.x).toBeGreaterThanOrEqual(0);
  expect(form!.x + form!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1);
}

const file = (index: number) => ({
  name: `经营分析资料-${String(index).padStart(2, "0")}.pptx`,
  mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  buffer: Buffer.from(`synthetic attachment ${index}`),
});

test("composer starts centered, docks on the first send, keeps history scrolling separate, and resets", async ({
  page,
  context,
}) => {
  const model = await enter(page, context);
  const state = page.locator("section[data-chat-state]");
  const form = page.locator(".analysis-composer");
  const input = page.getByLabel("分析问题");
  await expect(state).toHaveAttribute("data-chat-state", "empty");
  const initial = await form.boundingBox();
  const inputBox = await input.boundingBox();
  expect(inputBox!.height).toBeGreaterThanOrEqual(140);
  expect(initial!.y + initial!.height).toBeLessThan(page.viewportSize()!.height - 80);
  await expect(page.getByRole("button", { name: "发送消息", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: /深度思考|智能搜索/ })).toHaveCount(0);
  await page.getByLabel("选择 Chat 主模型").selectOption(providerId);
  await input.fill("分析销售趋势");
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("分析销售趋势\n");
  expect(model.requests).toHaveLength(0);
  await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
  expect(model.requests).toHaveLength(0);
  await expect(input).toHaveValue("分析销售趋势\n");
  model.holdNext();
  try {
    await input.press("Enter");
    await expect.poll(() => model.requests.length).toBe(1);
    expect(model.requests[0]).toMatchObject({
      tenantId,
      workspaceId,
      question: "分析销售趋势",
      history: [],
      selection: { mode: "fixed", provider_id: providerId },
    });
    await expect(state).toHaveAttribute("data-chat-state", "started");
    await expect(page.getByRole("button", { name: "停止任务", exact: true })).toBeVisible();
    const pending = await form.boundingBox();
    expect(pending!.y).toBeGreaterThan(initial!.y);
    expect(pending!.height).toBeLessThan(initial!.height);
    expect(page.viewportSize()!.height - (pending!.y + pending!.height)).toBeLessThan(80);
  } finally {
    model.release();
  }
  await expect(page.locator(".model-chat-history")).toContainText("55. 销售分析示例");
  await expect(page.getByRole("button", { name: "停止任务", exact: true })).toHaveCount(0);
  const process = page.getByRole("button", { name: /分析过程/ });
  await expect(process).toHaveAttribute("aria-expanded", "false");
  await process.click();
  await expect(process).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".analysis-tool-card")).toHaveCount(2);
  await expect(page.locator(".analysis-step .model-call-trace")).toHaveCount(2);
  await expect(page.locator(".analysis-model-step .model-call-trace")).toHaveCount(1);
  await expect(page.locator(".model-call-trace")).toHaveCount(3);
  await page.locator(".analysis-step .model-call-trace summary").first().click();
  await expect(page.getByRole("button", { name: "复制调用编号" }).first()).toBeVisible();
  await expect(page.locator(".analysis-final-answer")).toContainText("55. 销售分析示例");
  await expect(page.locator(".analysis-file-card")).toContainText(`retail-products-`);
  const history = page.locator(".model-chat-history");
  expect(await history.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(
    true,
  );
  const docked = await form.boundingBox();
  await history.evaluate((element) => element.scrollTo({ top: 0 }));
  await expect.poll(() => history.evaluate((element) => element.scrollTop)).toBe(0);
  const afterScroll = await form.boundingBox();
  expect(Math.abs(afterScroll!.y - docked!.y)).toBeLessThan(2);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(state).toHaveAttribute("data-chat-state", "empty");
  await expect(history).toHaveCount(0);
  await expect(input).toHaveValue("");
  await expect(page.getByLabel("选择 Chat 主模型")).toHaveValue("");
});

test("completed answers enter follow-up history and delivered CSV files download by server filename", async ({
  page,
  context,
}) => {
  const model = await enter(page, context);
  const input = page.getByLabel("分析问题");
  await page.getByLabel("选择 Chat 主模型").selectOption(providerId);
  await input.fill("第一问：比较渠道");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".analysis-final-answer")).toHaveCount(1);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^retail-products-[a-f0-9-]{36}\.csv$/);

  await input.fill("第二问：继续解释商品贡献");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".analysis-final-answer")).toHaveCount(2);
  expect(model.requests).toHaveLength(2);
  expect(model.requests[1]).toMatchObject({
    question: "第二问：继续解释商品贡献",
    history: [{ question: "第一问：比较渠道" }],
  });
  expect(model.requests[1].history[0].answer).toContain("55. 销售分析示例");
});

test("stop keeps unfinished tools uncertain until the canceled terminal confirms cleanup", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  await installInteractiveRunFixture(page, fixture.revision, "canceled_confirmed");
  await page.getByLabel("选择 Chat 主模型").selectOption(providerId);
  await page.getByLabel("分析问题").fill("停止这次分析");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const toolStatus = page.locator(".analysis-tool-card .analysis-status-chip");
  await expect(toolStatus).toHaveText("执行中");

  await page.getByRole("button", { name: "停止任务", exact: true }).click();
  await expect.poll(() => fixtureCancelCount(page)).toBe(1);
  await expect(toolStatus).toHaveText("结果待确认");
  await expect(toolStatus).not.toHaveText("已终止");

  await releaseFixtureTerminal(page);
  await expect(page.locator(".analysis-run-notice")).toContainText("任务已终止");
  await expect(toolStatus).toHaveText("已终止");
});

test("cleanup_unconfirmed leaves unfinished tool cards uncertain", async ({ page, context }) => {
  const fixture = await enter(page, context);
  await installInteractiveRunFixture(page, fixture.revision, "cleanup_unconfirmed");
  await page.getByLabel("选择 Chat 主模型").selectOption(providerId);
  await page.getByLabel("分析问题").fill("触发清理待确认");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".analysis-tool-card")).toHaveCount(1);
  await page.getByRole("button", { name: "停止任务", exact: true }).click();

  await expect(page.locator(".analysis-tool-card .analysis-status-chip")).toHaveText("结果待确认");
  await releaseFixtureTerminal(page);
  await expect(page.locator(".analysis-run-notice")).toContainText("终止清理失败");
  await expect(page.locator(".analysis-tool-card .analysis-status-chip")).toHaveText("结果待确认");
  await expect(page.locator(".analysis-tool-card .analysis-status-chip")).not.toHaveText("已终止");
});

test("already_terminal waits for the original clean stream before committing the answer", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  await installInteractiveRunFixture(page, fixture.revision, "already_terminal_completed");
  await page.getByLabel("选择 Chat 主模型").selectOption(providerId);
  await page.getByLabel("分析问题").fill("完成态竞态");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".analysis-tool-card")).toHaveCount(1);
  await page.getByRole("button", { name: "停止任务", exact: true }).click();

  await expect(page.locator(".analysis-run-header > .analysis-status-chip")).toHaveText(
    "已结束，等待结果",
  );
  await expect(page.locator(".analysis-final-answer")).toHaveCount(0);
  const processToggle = page.getByRole("button", { name: /分析过程/ });
  await page.locator(".analysis-step .model-call-trace summary").click();
  await page.getByRole("button", { name: "复制调用编号" }).focus();
  await expect(page.getByRole("button", { name: "复制调用编号" })).toBeFocused();
  await releaseFixtureTerminal(page);
  await expect(page.locator(".analysis-final-answer")).toContainText("原结果流确认后的完整回答。");
  await expect(processToggle).toHaveAttribute("aria-expanded", "false");
  await expect(processToggle).toBeFocused();
});

test("already_terminal with a broken original stream withholds results and explains the gap", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  await installInteractiveRunFixture(page, fixture.revision, "already_terminal_disconnected");
  await page.getByLabel("选择 Chat 主模型").selectOption(providerId);
  await page.getByLabel("分析问题").fill("终态结果流断开");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".analysis-tool-card")).toHaveCount(1);
  await page.getByRole("button", { name: "停止任务", exact: true }).click();

  await expect(page.locator(".analysis-run-header > .analysis-status-chip")).toHaveText(
    "已结束，等待结果",
  );
  await releaseFixtureTerminal(page);
  await expect(page.locator(".analysis-run-notice")).toContainText(
    "服务端终态已确认，但结果流不完整",
  );
  await expect(page.locator(".analysis-run-notice")).toContainText("无法安全提交最终回答或文件");
  await expect(page.locator(".analysis-final-answer")).toHaveCount(0);
});

test("attachment rail accepts ten local files, scrolls in one row, removes and reselects without sending files", async ({
  page,
  context,
}) => {
  const model = await enter(page, context);
  await page.getByLabel("选择 Chat 主模型").selectOption(providerId);
  await page.getByLabel("分析问题").fill("查看附件");
  const chooser = page.locator("input[type=file]");
  await chooser.setInputFiles(Array.from({ length: 11 }, (_, index) => file(index + 1)));
  const cards = page.locator(".composer-attachment");
  await expect(cards).toHaveCount(10);
  await expect(page.locator(".analysis-composer-dock").getByRole("alert")).toContainText("10");
  await expect(page.locator(".analysis-composer-dock")).toContainText("暂不支持");
  const rail = page.locator(".composer-attachment-list");
  const positions = await cards.evaluateAll((elements) =>
    elements.map((element) => element.getBoundingClientRect().y),
  );
  expect(Math.max(...positions) - Math.min(...positions)).toBeLessThan(2);
  const arrows = page.locator(".composer-attachment-scroll");
  await expect(arrows).toHaveCount(2);
  await expect(arrows.first()).toBeDisabled();
  await expect(arrows.last()).toBeEnabled();
  await arrows.last().click();
  await expect.poll(() => rail.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  async function scrollToEdge(direction: -1 | 1) {
    for (let index = 0; index < 12; index++) {
      const { left, end } = await rail.evaluate((element) => ({
        left: element.scrollLeft,
        end: element.scrollWidth - element.clientWidth,
      }));
      if (direction === 1 ? left >= end - 1 : left <= 1) return;
      const arrow = direction === 1 ? arrows.last() : arrows.first();
      await expect(arrow).toBeEnabled();
      await arrow.click();
      await expect.poll(() => rail.evaluate((element) => element.scrollLeft)).not.toBe(left);
    }
    throw new Error("Attachment rail did not reach its edge");
  }
  await scrollToEdge(1);
  await expect(arrows.last()).toBeDisabled();
  await expect(arrows.first()).toBeEnabled();
  await scrollToEdge(-1);
  await expect(arrows.first()).toBeDisabled();
  const send = page.getByRole("button", { name: "发送消息", exact: true });
  if (await send.isEnabled()) await send.click();
  expect(model.requests).toHaveLength(0);
  await cards.first().getByRole("button").click();
  await expect(cards).toHaveCount(9);
  await chooser.setInputFiles(file(1));
  await expect(cards).toHaveCount(10);
  while (await cards.count()) await cards.first().getByRole("button").click();
  await expect(cards).toHaveCount(0);
  await expect(send).toBeEnabled();
  await send.click();
  await expect(page.locator(".model-chat-history")).toContainText("销售分析示例");
  expect(model.requests).toHaveLength(1);
  expect(model.requests[0]).not.toHaveProperty("attachments");
  await chooser.setInputFiles(file(1));
  await cards.first().getByRole("button").click();
  const navigationDialog = page.waitForEvent("dialog");
  const navigating = page.getByRole("link", { name: "报告空间", exact: true }).click();
  const confirmation = await navigationDialog;
  expect(confirmation.type()).toBe("confirm");
  expect(confirmation.message()).toContain("离开");
  await confirmation.dismiss();
  await navigating;
  await expect(page.locator(".model-chat-history")).toContainText("销售分析示例");
  await chooser.setInputFiles(file(1));
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(cards).toHaveCount(0);
  await expect(page.locator("section[data-chat-state]")).toHaveAttribute(
    "data-chat-state",
    "empty",
  );
  await chooser.setInputFiles(file(1));
  await expect(cards).toHaveCount(1);
});

for (const width of [375, 768, 1440]) {
  test(`composer fits ${width}px with centered, attached and docked states`, async ({
    page,
    context,
  }) => {
    await page.setViewportSize({ width, height: width === 375 ? 812 : 1000 });
    await enter(page, context);
    await expectNoOverflow(page);
    const select = page.getByLabel("选择 Chat 主模型");
    const attach = page.getByRole("button", { name: "添加附件", exact: true });
    const send = page.getByRole("button", { name: "发送消息", exact: true });
    const selectBox = await select.boundingBox();
    const attachBox = await attach.boundingBox();
    const sendBox = await send.boundingBox();
    expect(selectBox!.x).toBeLessThan(attachBox!.x);
    expect(attachBox!.x).toBeLessThan(sendBox!.x);
    expect(Math.abs(selectBox!.y - sendBox!.y)).toBeLessThan(14);
    await page.screenshot({ path: `../../work/993/composer-empty-${width}.png`, fullPage: true });
    await page
      .locator("input[type=file]")
      .setInputFiles(Array.from({ length: 10 }, (_, index) => file(index + 1)));
    await expect(page.locator(".composer-attachment")).toHaveCount(10);
    await expectNoOverflow(page);
    await page.screenshot({
      path: `../../work/993/composer-attachments-${width}.png`,
      fullPage: true,
    });
    const cards = page.locator(".composer-attachment");
    while (await cards.count()) await cards.first().getByRole("button").click();
    await select.selectOption(providerId);
    await page.getByLabel("分析问题").fill("分析本月销售趋势");
    await send.click();
    await expect(page.locator(".model-chat-history")).toContainText("55. 销售分析示例");
    await expectNoOverflow(page);
    const form = await page.locator(".analysis-composer").boundingBox();
    expect(page.viewportSize()!.height - (form!.y + form!.height)).toBeLessThan(90);
    await page.screenshot({ path: `../../work/993/composer-started-${width}.png`, fullPage: true });
  });
}

test("docked composer keeps attachment removal and send reachable in short mobile viewports", async ({
  page,
  context,
}) => {
  await page.setViewportSize({ width: 375, height: 350 });
  const model = await enter(page, context);
  await page.getByLabel("选择 Chat 主模型").selectOption(providerId);
  await page.getByLabel("分析问题").fill("分析销售趋势");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".model-chat-history")).toContainText("销售分析示例");
  await page.getByLabel("分析问题").fill("查看所选附件");
  await page
    .locator("input[type=file]")
    .setInputFiles(Array.from({ length: 10 }, (_, index) => file(index + 1)));
  for (const height of [300, 350]) {
    await page.setViewportSize({ width: 375, height });
    const dock = page.locator(".analysis-composer-dock");
    const dockBox = await dock.boundingBox();
    const welcomeBox = await page.locator(".analysis-welcome").boundingBox();
    const mainBox = await page.getByRole("main").boundingBox();
    expect(dockBox!.y + dockBox!.height).toBeLessThanOrEqual(
      welcomeBox!.y + welcomeBox!.height + 1,
    );
    expect(dockBox!.y + dockBox!.height).toBeLessThanOrEqual(mainBox!.y + mainBox!.height + 1);
    const send = page.getByRole("button", { name: "发送消息", exact: true });
    await send.scrollIntoViewIfNeeded();
    const sendBox = await send.boundingBox();
    expect(sendBox!.y).toBeGreaterThanOrEqual(0);
    expect(sendBox!.y + sendBox!.height).toBeLessThanOrEqual(height + 1);
    if (await send.isEnabled()) await send.click();
    expect(model.requests).toHaveLength(1);
    const remove = page.locator(".composer-attachment").first().getByRole("button");
    await remove.scrollIntoViewIfNeeded();
    const removeBox = await remove.boundingBox();
    expect(removeBox!.y).toBeGreaterThanOrEqual(0);
    expect(removeBox!.y + removeBox!.height).toBeLessThanOrEqual(height + 1);
    await remove.click();
    await expect(page.locator(".composer-attachment")).toHaveCount(height === 300 ? 9 : 8);
    const notice = page.locator(".composer-attachment-notice");
    await notice.scrollIntoViewIfNeeded();
    const noticeBox = await notice.boundingBox();
    const visibleDockBox = await dock.boundingBox();
    expect(noticeBox!.y).toBeGreaterThanOrEqual(visibleDockBox!.y - 1);
    expect(noticeBox!.y + noticeBox!.height).toBeLessThanOrEqual(
      visibleDockBox!.y + visibleDockBox!.height + 1,
    );
    expect(noticeBox!.y + noticeBox!.height).toBeLessThanOrEqual(height + 1);
    await expectNoOverflow(page);
    await page.screenshot({
      path: `../../work/993/composer-started-short-${height}.png`,
      fullPage: true,
    });
  }
});
