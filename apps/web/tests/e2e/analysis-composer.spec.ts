import { test, expect, type BrowserContext, type Page } from "@playwright/test";

// These are UI scenarios: login/authorization use the real Web app; model responses are local.
const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
const providerId = "99300000-0000-4000-8000-000000000101";
const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const analysisPath = `/t/${tenantId}/w/${workspaceId}/analysis`;
const password = process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password";
type ModelRequest = {
  call_id: string;
  provider_id: string;
  input: {
    thinking: boolean;
    reasoning_effort: string;
    messages: { role: string; content: string }[];
  };
};

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
  const requests: ModelRequest[] = [];
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
  await page.route("**/api/web/v1/models/calls", async (route) => {
    const request = route.request().postDataJSON() as ModelRequest;
    requests.push(request);
    if (hold) {
      hold = false;
      await new Promise<void>((resolve) => (release = resolve));
    }
    const text = Array.from(
      { length: 55 },
      (_, index) => `${index + 1}. 销售分析示例：先比较渠道表现，再核对商品与库存变化。`,
    ).join("\n");
    const events = [
      { type: "start", call_id: request.call_id },
      {
        type: "route_selected",
        call_id: request.call_id,
        intent: { category_id: "business_channel", scene_id: "SC-02" },
        features: {
          task_type: "对比拆解",
          difficulty: "L1",
          response_mode: "answer",
          required_capabilities: [],
        },
        routing: {
          mode: "fixed",
          provider_id: providerId,
          model: "deepseek-v4-pro",
          reason: "user_fixed_main_model",
          dictionary_version: "test",
          routing_version: "test",
        },
      },
      {
        type: "reasoning_meta",
        call_id: request.call_id,
        thinking: true,
        reasoning_effort: "high",
      },
      { type: "text_delta", call_id: request.call_id, text },
      {
        type: "final",
        call_id: request.call_id,
        provider_id: providerId,
        config_version: 1,
        status: "completed",
        duration_ms: 100,
        usage: { input_tokens: 10, output_tokens: 20, reasoning_tokens: 5 },
        error_code: null,
        upstream_outcome_unknown: false,
        requested_model: "deepseek-v4-pro",
        response_model: "deepseek-v4-pro",
        upstream_request_id: "composer-ui-fixture",
        finish_reason: "stop",
        validation: { status: "not_requested", mode: "text", paths: [] },
        continuation_token: null,
      },
    ];
    await route.fulfill({
      headers: {
        "Content-Type": "text/event-stream",
        "X-Compass-View-Revision": revision,
        "Cache-Control": "no-store",
      },
      body: events
        .map((event) => {
          const value =
            event.type === "final"
              ? {
                  type: "final",
                  call_id: request.call_id,
                  status: "completed",
                  intent: events[1].intent,
                  features: events[1].features,
                  routing: events[1].routing,
                  metrics: {},
                  result: { ...event, type: undefined, kind: "generate", text, tool_calls: [] },
                }
              : event;
          return `event: ${event.type}\ndata: ${JSON.stringify(value)}\n\n`;
        })
        .join(""),
    });
  });
  await page.goto(analysisPath);
  await expect(page.locator("[data-protected-root]")).toBeVisible();
  await expect(page.getByLabel("选择 Chat 主模型")).toBeEnabled();
  return {
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
    expect(model.requests[0].input.thinking).toBe(true);
    expect(model.requests[0].input.reasoning_effort).toBe("high");
    await expect(state).toHaveAttribute("data-chat-state", "started");
    await expect(page.getByRole("button", { name: "停止生成", exact: true })).toBeVisible();
    const pending = await form.boundingBox();
    expect(pending!.y).toBeGreaterThan(initial!.y);
    expect(pending!.height).toBeLessThan(initial!.height);
    expect(page.viewportSize()!.height - (pending!.y + pending!.height)).toBeLessThan(80);
  } finally {
    model.release();
  }
  await expect(page.locator(".model-chat-history")).toContainText("55. 销售分析示例");
  await expect(page.getByRole("button", { name: "停止生成", exact: true })).toHaveCount(0);
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
  expect(model.requests[0].input).not.toHaveProperty("attachments");
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
