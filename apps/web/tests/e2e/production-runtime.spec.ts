import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import {
  allowedDetail,
  answerText,
  narrationText,
  ProductionRuntimeFixture,
  runtimeIds,
} from "./production-runtime.fixture";
import { setVisibility } from "./visibility";

// Real Web authentication and product UI; Agent v2 responses are isolated per browser context.
// Physical queue admission, Worker leases and server filtering are verified by integration tests.
const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
const analysisPath = `/t/${tenantId}/w/${workspaceId}/analysis`;
const origin = `http://127.0.0.1:${process.env.WEB_TEST_PORT ?? 8110}`;
const password = process.env.WEB_TEST_PASSWORD ?? "teaching-995-only-password";
test.use({ storageState: process.env.WEB_TEST_STORAGE_STATE });

async function enter(page: Page, context: BrowserContext) {
  const query = new URLSearchParams({ tenantId, workspaceId, pageId: "analysis-new" });
  let session = await context.request.get(`/api/web/v1/session?${query}`);
  if (session.status() === 401) {
    const login = await context.request.post("/api/web/v1/auth/login", {
      headers: { origin },
      data: { loginName: "teaching.admin", password },
    });
    expect(login.status()).toBe(200);
    session = await context.request.get(`/api/web/v1/session?${query}`);
  }
  expect(session.status()).toBe(200);
  const revision = session.headers()["x-compass-view-revision"];
  expect(revision).toBeTruthy();
  const supported = { state: "supported", reason: "isolated UI contract" };
  await page.route("**/api/web/v1/models/available?**", (route) =>
    route.fulfill({
      headers: { "X-Compass-View-Revision": revision, "Cache-Control": "no-store" },
      json: {
        items: [
          {
            id: "98400000-0000-4000-8000-000000000101",
            alias: "Runtime test model",
            api_model: "runtime-test",
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
  const fixture = new ProductionRuntimeFixture(page, revision);
  await fixture.install();
  await page.goto(analysisPath);
  await expect(page.locator("[data-protected-root]")).toBeVisible();
  await expect(page.getByLabel("选择 Chat 主模型")).toBeEnabled();
  return fixture;
}

async function submit(page: Page, fixture: ProductionRuntimeFixture, question: string) {
  await page.getByLabel("分析问题").fill(question);
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect.poll(() => fixture.submissions.length).toBe(1);
  await expect(page).toHaveURL(new RegExp(`session=${runtimeIds.session}`));
  await expect(page.locator(`[data-run-id="${runtimeIds.run}"]`)).toBeVisible();
}

for (const width of [1440, 390]) {
  test(`queued Run survives refresh and starts in the same turn at ${width}px`, async ({
    page,
    context,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const fixture = await enter(page, context);
    await submit(page, fixture, `排队任务 ${width}`);
    if (width === 390) await page.getByRole("button", { name: "展开导航" }).click();
    const sidebar = page.locator('.secondary-content[data-section="analysis"]:visible');
    const list = sidebar.locator(".conversation-list");
    const item = list.getByRole("link", { name: `排队任务 ${width}`, exact: true });
    await expect(item).toBeVisible();
    await expect(item.locator(".conversation-title")).toHaveCSS("white-space", "nowrap");
    const switcher = sidebar.getByRole("link", { name: "切换工作空间", exact: true });
    const listBounds = (await list.boundingBox())!;
    const switchBounds = (await switcher.boundingBox())!;
    expect(switchBounds.y - (listBounds.y + listBounds.height)).toBeLessThanOrEqual(4);
    if (width === 390) await page.keyboard.press("Escape");
    const status = page.getByRole("region", { name: "分析过程" });
    await expect(status).toContainText("等待执行");
    await expect(page.getByRole("region", { name: "最终回答" })).toHaveCount(0);
    fixture.update((protocol) => protocol.transition("paused", "queued"));
    await page.reload();
    await expect(status).toContainText("等待执行");
    await expect(status.getByRole("button", { name: "继续任务", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "批准执行", exact: true })).toHaveCount(0);
    const stop = page.getByRole("button", { name: "停止任务", exact: true });
    await expect(stop).toBeEnabled();
    expect(fixture.controls).toEqual([]);
    fixture.update((protocol) => protocol.begin());
    await expect(status).toContainText("渠道查询");
    const pause = status.getByRole("button", { name: "暂停任务", exact: true });
    await expect(pause).toBeVisible();
    await expect(pause).toBeEnabled();
    await expect(page.getByRole("button", { name: "终止任务", exact: true })).toHaveCount(0);
    await expect(page.getByText("正在执行", { exact: true })).toHaveCount(0);
    await stop.focus();
    await expect(stop).toBeFocused();
    fixture.update((protocol) => protocol.complete());
    await expect(page.getByRole("region", { name: "最终回答" })).toHaveText(answerText);
    await expect(page.locator("[data-run-id]")).toHaveCount(1);
    expect(fixture.submissions).toHaveLength(1);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBe(true);
  });
}

test("session list refreshes on return to the tab and on session changes", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  await expect(page.getByText("暂无分析会话", { exact: true })).toBeVisible();
  const initialReads = fixture.sessionListReads;
  await page.clock.install();
  await page.clock.fastForward(11000);
  expect(fixture.sessionListReads).toBe(initialReads);
  await setVisibility(page, "hidden");
  await page.clock.fastForward(11000);
  expect(fixture.sessionListReads).toBe(initialReads);
  await setVisibility(page, "visible");
  await expect.poll(() => fixture.sessionListReads).toBe(initialReads + 1);
  await page.evaluate(() => window.dispatchEvent(new Event("compass-sessions-changed")));
  await expect.poll(() => fixture.sessionListReads).toBe(initialReads + 2);
});

test("pending titles update after the stream ends and the sidebar manages sessions by keyboard and touch", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  fixture.titleStatus = "pending";
  const temporaryTitle = "查询最近两周门店渠道实付金额变化";
  const generatedTitle = "两周门店实付对比";
  await submit(page, fixture, temporaryTitle);
  const sidebar = page.locator('.secondary-content[data-section="analysis"]:visible');
  const temporary = sidebar.getByRole("link", { name: temporaryTitle, exact: true });
  await expect(temporary).toBeVisible();
  fixture.update((protocol) => protocol.complete());
  await expect(page.getByRole("region", { name: "最终回答" })).toBeVisible();
  fixture.generateTitle(generatedTitle);
  const generated = sidebar.getByRole("link", { name: generatedTitle, exact: true });
  await expect(generated).toBeVisible();
  await expect(temporary).toHaveCount(0);

  const opener = sidebar.getByRole("button", { name: `会话操作 · ${generatedTitle}` });
  await generated.focus();
  await page.keyboard.press("Tab");
  await expect(opener).toBeFocused();
  await opener.press("Enter");
  await page.getByRole("menuitem", { name: "改名", exact: true }).press("Enter");
  const rename = page.getByRole("dialog", { name: "改名", exact: true });
  const title = rename.getByLabel("会话标题");
  await expect(title).toBeFocused();
  await expect(title).toHaveValue(generatedTitle);
  await title.fill("门店渠道周报");
  await title.press("Enter");
  await expect(rename).toBeHidden();
  await expect(sidebar.getByRole("button", { name: "会话操作 · 门店渠道周报" })).toBeFocused();
  await page.reload();
  await expect(sidebar.getByRole("link", { name: "门店渠道周报", exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "展开导航" }).click();
  const trigger = sidebar.getByRole("button", { name: "会话操作 · 门店渠道周报" });
  await expect(trigger).toHaveCSS("opacity", "1");
  await trigger.click();
  await page.getByRole("menuitem", { name: "删除", exact: true }).click();
  const deletion = page.getByRole("dialog", { name: "删除会话" });
  await expect(deletion.getByRole("button", { name: "取消" })).toBeFocused();
  await deletion.getByRole("button", { name: "取消" }).click();
  expect(fixture.deletedRequests).toBe(0);
  await trigger.click();
  await page.getByRole("menuitem", { name: "删除", exact: true }).click();
  await deletion.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page).not.toHaveURL(/session=/);
  expect(fixture.deletedRequests).toBe(1);
  await page.reload();
  await expect(page.getByLabel("分析问题")).toBeVisible();
  await page.getByRole("button", { name: "展开导航" }).click();
  await expect(sidebar.getByRole("link", { name: "门店渠道周报", exact: true })).toHaveCount(0);
});

test("long session titles fill the row and scroll to the end once on hover", async ({
  page,
  context,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const fixture = await enter(page, context);
  const fullTitle = "两周门店渠道实付金额与商品贡献对比，结果汇总到本周经营复盘";
  await submit(page, fixture, fullTitle);
  const row = page.locator(".conversation-row").filter({ hasText: fullTitle });
  const title = row.locator(".conversation-title");
  const text = title.locator("span");
  const menu = row.getByRole("button", { name: `会话操作 · ${fullTitle}` });
  await expect(menu).toHaveCSS("opacity", "0");
  await expect(title).toHaveCSS("text-overflow", "ellipsis");
  const before = (await title.boundingBox())!;
  const menuBounds = (await menu.boundingBox())!;
  expect(before.x + before.width).toBeGreaterThan(menuBounds.x + 30);
  await title.hover({ position: { x: 20, y: 8 } });
  await expect(menu).toHaveCSS("opacity", "1");
  expect((await title.boundingBox())!.width).toBe(before.width);
  await expect(title).toHaveAttribute("data-scrolling", "");
  await text.evaluate((element) => {
    const [animation] = element.getAnimations();
    if (!animation || animation.effect?.getTiming().iterations !== 1)
      throw new Error("Expected one complete scroll");
    animation.finish();
  });
  const end = (await text.boundingBox())!;
  expect(end.x + end.width).toBeLessThanOrEqual(menuBounds.x);
  expect(end.x + end.width).toBeGreaterThan(menuBounds.x - 8);
  await page.getByLabel("分析问题").hover();
  await expect(title).toHaveCSS("text-overflow", "ellipsis");
  await expect(title).toHaveAttribute("title", fullTitle);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await title.hover({ position: { x: 20, y: 8 } });
  await expect(title).toHaveCSS("text-overflow", "ellipsis");
  await expect(title).toHaveAttribute("title", fullTitle);
});

test("429 keeps the question and admission key until the user retries", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  fixture.rejectSubmissions = 1;
  const input = page.getByLabel("分析问题"),
    text = "队列满时保留这条经营分析问题";
  await input.fill(text);
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect.poll(() => fixture.submissions.length).toBe(1);
  await expect(page.locator("section[data-chat-state]").getByRole("alert")).toContainText(
    "稍后重试",
  );
  await expect(input).toHaveValue(text);
  await expect(page).not.toHaveURL(/session=/);
  await expect(page.locator("[data-run-id]")).toHaveCount(0);
  expect(fixture.protocol).toBeNull();
  await page.clock.install();
  await page.clock.fastForward(2500);
  expect(fixture.submissions).toHaveLength(1);
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect.poll(() => fixture.submissions.length).toBe(2);
  expect(fixture.submissions[1].requestId).toBe(fixture.submissions[0].requestId);
  expect(fixture.submissions[1].question).toBe(text);
  await expect(page.getByRole("region", { name: "分析过程" })).toContainText("等待执行");
});

test("only structured success folds the process once and keyboard reopening survives duplicate frames", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  fixture.duplicateFrames = true;
  await submit(page, fixture, "不要按旁白文字切换最终回答");
  fixture.update((protocol) => protocol.begin());
  const process = page.locator(".analysis-process-toggle");
  const node = page.locator(`[data-node-id="${runtimeIds.node}"]`);
  await expect(process).toHaveAttribute("aria-expanded", "false");
  await expect(process).toContainText("渠道查询");
  await process.press("Enter");
  await node.locator(".ui-timeline-row").click();
  await expect(node).toContainText(narrationText);
  await expect(page.getByRole("region", { name: "最终回答" })).toHaveCount(0);
  await node.locator(".ui-timeline-row").focus();
  fixture.update((protocol) => protocol.complete());
  await expect(page.getByRole("region", { name: "最终回答" })).toHaveText(answerText);
  await expect(process).toHaveAttribute("aria-expanded", "false");
  await expect(process).toHaveText("已完成，用时 2 分 18 秒");
  await expect(process).toBeFocused();
  await process.press("Enter");
  await expect(process).toHaveAttribute("aria-expanded", "true");
  await page.evaluate(() => window.dispatchEvent(new Event("compass-sessions-changed")));
  await expect(node).toContainText(narrationText);
  await expect(process).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("region", { name: "最终回答" })).toHaveCount(1);
  expect(fixture.submissions).toHaveLength(1);
  expect(fixture.controls).toEqual([]);
});

test("appends new entries and updates an earlier tool in place while running", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  await submit(page, fixture, "核对渠道和商品");
  fixture.update((protocol) => protocol.begin());
  const process = page.locator(".analysis-process-toggle");
  const list = page.getByRole("list", { name: "执行过程" });
  const tool = page.locator(`[data-node-id="${runtimeIds.node}"]`);
  const childId = "98400000-0000-4000-8000-000000000007";
  const output = '{"rows":[{"channel":"POS","paidCent":"12000"}]}';

  await expect(process).toHaveAttribute("aria-expanded", "false");
  await expect(process).toContainText("渠道查询");
  await process.press("Enter");
  await tool.locator(".ui-timeline-row").press("Space");
  await expect(tool.getByRole("region", { name: "调用入参" })).toBeVisible();

  fixture.update((protocol) => {
    const parent = protocol.node();
    protocol.append([
      {
        type: "view.upsert",
        payload: {
          objectId: childId,
          revision: 1,
          kind: "execution.node",
          value: {
            id: childId,
            parentId: parent.value.id,
            kind: "model",
            revision: 1,
            status: "running",
            title: "分析请求",
            detail: { thinking: "核对商品数据，再比较分渠道的实付金额。" },
            startedAtMs: parent.value.startedAtMs + 1000,
          },
        },
      },
    ]);
  });
  await expect(list.locator(":scope > li")).toHaveCount(2);
  await expect(list.locator(":scope > li").last()).toContainText("思考 · 核对商品数据");
  await list.getByRole("button", { name: /思考 · 核对商品数据/ }).press("Enter");
  await expect(list.locator(".ui-timeline-summary").last()).toHaveText(
    "核对商品数据，再比较分渠道的实付金额。",
  );
  await expect(tool.locator(".ui-timeline-row")).toHaveAttribute("aria-expanded", "true");

  // A newer row can remain in progress while the older tool publishes its result.
  await process.click();
  await expect(process).toContainText("核对商品数据");
  fixture.update((protocol) => {
    const current = protocol.node();
    protocol.append([
      {
        type: "view.upsert",
        payload: {
          ...current,
          revision: current.revision + 1,
          value: {
            ...current.value,
            revision: current.revision + 1,
            status: "completed",
            endedAtMs: current.value.startedAtMs + 4500,
            detail: { input: '{"dimension":"channel"}', output },
          },
        },
      },
    ]);
  });
  await expect(process).toContainText("渠道查询");
  await expect(process).toContainText("成功");
  await process.press("Enter");
  await expect(list.locator(":scope > li").first()).toHaveAttribute(
    "data-node-id",
    runtimeIds.node,
  );
  await expect(tool.locator(".ui-timeline-row")).toHaveAttribute("aria-expanded", "true");
  await expect(tool.getByRole("region", { name: "调用结果" }).locator("pre")).toHaveText(output);

  await process.press("Enter");
  fixture.update((protocol) => protocol.complete());
  await expect(process).toHaveAttribute("aria-expanded", "false");
  await expect(process).toHaveText("已完成，用时 2 分 18 秒");
  await page.reload();
  await expect(process).toHaveText("已完成，用时 2 分 18 秒");
  await process.press("Enter");
  const thought = list.getByRole("button", { name: /思考 · 核对商品数据/ });
  await thought.press("Enter");
  await expect(list.locator(".ui-timeline-summary").last()).toHaveText(
    "核对商品数据，再比较分渠道的实付金额。",
  );
  await tool.locator(".ui-timeline-row").press("Enter");
  await expect(tool.getByRole("region", { name: "调用结果" }).locator("pre")).toHaveText(output);
});

test("a disconnected queued Run reconnects to one completed answer without resubmission", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  fixture.disconnectNext = true;
  fixture.duplicateFrames = true;
  await submit(page, fixture, "断线后继续同一个排队任务");
  await expect(page.getByText(/连接已中断，正在重新连接/)).toBeVisible();
  fixture.update((protocol) => {
    protocol.begin();
    protocol.complete();
  });
  await expect(page.getByRole("region", { name: "最终回答" })).toHaveText(answerText);
  await expect(page.locator(".analysis-process-toggle")).toHaveAttribute("aria-expanded", "false");
  await page.reload();
  await expect(page.getByRole("region", { name: "最终回答" })).toHaveCount(1);
  await expect(page.getByRole("region", { name: "最终回答" })).toHaveText(answerText);
  expect(fixture.submissions).toHaveLength(1);
  expect(fixture.controls).toEqual([]);
});

test("a current-scope snapshot removes previously authorized tool details on reload", async ({
  page,
  context,
}) => {
  const fixture = await enter(page, context);
  await submit(page, fixture, "检查当前授权范围的工具详情");
  fixture.update((protocol) => protocol.begin());
  const node = page.locator(`[data-node-id="${runtimeIds.node}"]`);
  const process = page.locator(".analysis-process-toggle");
  await expect(process).toContainText("渠道查询");
  await process.click();
  await expect(node).toContainText("渠道查询");
  await node.locator(".ui-timeline-row").click();
  await expect(node).toContainText(allowedDetail);
  const boundary = fixture.delivered.length;
  fixture.update((protocol) => protocol.restrictDetails());
  await page.reload();
  await process.click();
  await expect(node).toContainText("渠道查询");
  await node.locator(".ui-timeline-row").click();
  await expect(node).toContainText("已保留允许的执行状态。");
  await expect(page.locator("body")).not.toContainText(allowedDetail);
  expect(fixture.delivered.slice(boundary).length).toBeGreaterThan(0);
  expect(JSON.stringify(fixture.delivered.slice(boundary))).not.toContain(allowedDetail);
  const snapshot = fixture.protocol!.snapshot();
  const object = snapshot.packets.find((packet) => packet.event.type === "view.upsert")!;
  expect(object.event.payload).toMatchObject({ objectId: runtimeIds.node });
  expect(fixture.submissions).toHaveLength(1);
});

for (const terminal of ["failed", "canceled"] as const) {
  test(`${terminal} leaves incomplete analysis visible without a successful answer`, async ({
    page,
    context,
  }) => {
    const fixture = await enter(page, context);
    await submit(page, fixture, `检查 ${terminal} 的未完成回答`);
    fixture.update((protocol) => {
      protocol.begin();
      protocol.complete(terminal);
    });
    const status = page.getByRole("region", { name: "分析过程" });
    await expect(status).toContainText(terminal === "failed" ? "执行失败" : "已终止");
    await expect(page.getByRole("region", { name: "最终回答" })).toHaveCount(0);
    await expect(page.locator(".analysis-process-toggle")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await page.reload();
    await expect(status).toContainText(terminal === "failed" ? "执行失败" : "已终止");
    await expect(page.getByRole("region", { name: "最终回答" })).toHaveCount(0);
    expect(fixture.submissions).toHaveLength(1);
  });
}
