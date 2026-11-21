import { test, expect, type BrowserContext, type APIRequestContext } from "@playwright/test";
const base = "/api/web/v1/platform/promptops/";
const origin = "http://127.0.0.1:" + (process.env.WEB_TEST_PORT ?? 8110);
const workspace = "99500000-0000-4000-8000-000000000020",
  tenant = "99500000-0000-4000-8000-000000000010";
const starters =
  "/api/web/v1/analysis/prompt-starters?tenantId=" + tenant + "&workspaceId=" + workspace;
// Publishing and restoring change an Agent-wide head. This suite runs against a dedicated Agent DB.
test.skip(
  process.env.COMPASS_PROMPTOPS_E2E !== "true",
  "Requires isolated Agent/Web fixture databases.",
);
async function login(context: BrowserContext, name: string) {
  const r = await context.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: { loginName: "teaching." + name, password: process.env.WEB_TEST_PASSWORD },
  });
  expect(r.status(), await r.text()).toBe(200);
}
async function json(request: APIRequestContext, url: string) {
  const r = await request.get(url);
  expect(r.status(), await r.text()).toBe(200);
  return r.json();
}
test("administrator edits a file, publishes it to the live starter projection, and restores a whole snapshot", async ({
  page,
  browser,
}) => {
  test.setTimeout(120000);
  await login(page.context(), "platform");
  const tenantAdmin = await browser.newContext({ baseURL: origin });
  try {
    await login(tenantAdmin, "admin");
    expect((await tenantAdmin.request.get(base + "tree")).status()).toBe(403);
    await page.goto("/platform/promptops");
    await expect(page.getByRole("heading", { name: "Prompts", level: 1 })).toBeVisible();
    const head = await json(page.request, base + "tree");
    if (!head.generation) {
      await page.getByRole("button", { name: "导入本地目录" }).click();
      await page.getByRole("button", { name: "确认导入草稿" }).click();
      await expect(page.getByText(/导入已完成/)).toBeVisible();
      await page.getByRole("button", { name: "整体发布", exact: true }).click();
      await page.getByLabel("发布说明").fill("课程首发");
      await page.getByRole("button", { name: "确认整体发布" }).click();
      await expect(page.getByText(/已提交整库快照/)).toBeVisible();
    }
    const active = await json(page.request, base + "tree");
    const start = await json(tenantAdmin.request, starters);
    expect(start.models).toEqual([]);
    expect(start.workflow_parts.join("每周销售")).toContain("每周销售");
    const unauthorizedWrite = await page.request.post(base + "import-preview", {
      headers: { origin },
      data: {},
    });
    expect(unauthorizedWrite.status()).toBe(403);
    const modelStarters = "/api/web/v1/platform/models/prompt-starters";
    expect((await tenantAdmin.request.get(modelStarters)).status()).toBe(403);
    const examples = await json(page.request, modelStarters);
    expect(examples.models).toHaveLength(4);
    expect(examples.analysis).toEqual([]);
    expect(
      examples.models.some((item: { questions: unknown[] }) => item.questions.length > 1),
    ).toBe(true);
    const original = start.analysis
      .flatMap((g: { suggestions: { label: string; question: string }[] }) => g.suggestions)
      .find((s: { label: string }) => s.label === "如何拆解销售增长的影响因素？").question;
    const path = "support/starters/analysis.json";
    const baseline = await json(page.request, base + "file?path=" + encodeURIComponent(path));
    const candidate = JSON.parse(baseline.body);
    const marker = "经营问题 " + crypto.randomUUID() + "：如何分解销售增长？";
    // Exercise the narrow-screen editor and keyboard access.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByLabel("搜索文件").fill(path);
    await page.getByTitle(path, { exact: true }).click();
    candidate.questions["sales-growth"] = marker;
    await page.getByLabel("JSON 正文").fill(JSON.stringify(candidate, null, 2));
    await page.getByRole("button", { name: "保存草稿" }).click();
    await expect(page.getByText("草稿已保存。")).toBeVisible();
    expect(JSON.stringify(await json(tenantAdmin.request, starters))).not.toContain(marker);
    await page.getByRole("button", { name: "发布此文件", exact: true }).click();
    await page.getByLabel("发布说明").fill("更新用户问题");
    await page.getByRole("button", { name: "确认发布此文件", exact: true }).click();
    await expect(page.getByText(/发布完成/)).toBeVisible();
    expect(JSON.stringify(await json(tenantAdmin.request, starters))).toContain(marker);
    const live = await json(page.request, base + "file?path=" + encodeURIComponent(path));
    await page.getByRole("button", { name: "版本历史" }).click();
    await page.getByRole("button", { name: "查看 v" + live.current_version, exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText(marker);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "整库快照", exact: true }).click();
    await page
      .getByRole("button", { name: "S" + String(active.snapshot_no).padStart(3, "0"), exact: true })
      .click();
    await page.getByRole("button", { name: "恢复此快照" }).click();
    await page.getByLabel("发布说明").fill("恢复初始预填");
    await page.getByRole("button", { name: /确认恢复快照/ }).click();
    await expect(page.getByText(/已提交整库快照/)).toBeVisible();
    const restored = await json(tenantAdmin.request, starters);
    expect(JSON.stringify(restored)).toContain(original);
    expect(JSON.stringify(restored)).not.toContain(marker);
    const probe = await tenantAdmin.request.get(starters + "&path=" + encodeURIComponent(path));
    expect(probe.status()).toBe(422);
  } finally {
    await tenantAdmin.close();
  }
});

test("new online file previews a template and preserves both authors' changes during a conflict", async ({
  page,
}) => {
  await login(page.context(), "platform");
  await page.goto("/platform/promptops");
  await page.getByRole("button", { name: "新建提示词" }).click();
  const path = "online/qa/" + crypto.randomUUID() + ".md";
  const source = "# 周报\n\n分析周期 {{period}}";
  await page.getByLabel("相对路径").fill(path);
  await page.getByLabel("Markdown 正文").fill(source);
  await page.getByRole("button", { name: "创建草稿" }).click();
  await expect(page.getByText("已创建线上草稿。")).toBeVisible();
  await page.getByRole("button", { name: "Markdown 预览" }).click();
  await expect(page.getByRole("heading", { name: "周报" })).toBeVisible();
  await page.getByRole("button", { name: "模板试填" }).click();
  await page.getByLabel("period", { exact: true }).fill("本周");
  await page.getByRole("button", { name: "运行模板预览" }).click();
  await expect(page.getByText("分析周期 本周")).toBeVisible();

  const row = await json(page.request, base + "file?path=" + encodeURIComponent(path));
  expect(row.usages).toEqual([]);
  const { csrfToken } = await json(page.request, "/api/web/v1/session?pageId=platform-promptops");
  const concurrentBody = "第二位编辑者已保存。";
  const r = await page.request.post(base + "file-draft", {
    headers: { origin, "X-CSRF-Token": csrfToken },
    data: {
      path,
      body: concurrentBody,
      request_id: crypto.randomUUID(),
      expected_edit_rev: row.edit_rev,
      base_epoch: row.active_epoch,
    },
  });
  expect(r.status(), await r.text()).toBe(200);
  await page.getByRole("button", { name: "源码编辑" }).click();
  const mine = "我的修改需要和线上草稿合并。";
  await page.getByLabel("Markdown 正文").fill(mine);
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByText(concurrentBody)).toBeVisible();
  await expect(page.getByLabel("Markdown 正文")).toHaveValue(mine);
  await expect(page.getByRole("button", { name: "保存草稿" })).toBeDisabled();
  await page.getByRole("button", { name: "保留我的输入，使用此基线" }).click();
  const merged = mine + "\n" + concurrentBody;
  await page.getByLabel("Markdown 正文").fill(merged);
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByText("草稿已保存。")).toBeVisible();
  expect(
    (await json(page.request, base + "file?path=" + encodeURIComponent(path))).draft_body,
  ).toBe(merged);
});

test("tool descriptions and examples publish while the code-owned schema remains read-only", async ({
  page,
}) => {
  await login(page.context(), "platform");
  const path = "tools/python_analysis.json";
  const baseline = await json(page.request, base + "file?path=" + encodeURIComponent(path));
  const original = JSON.parse(baseline.body);
  const marker = "工具描述验收 " + crypto.randomUUID();
  const { csrfToken } = await json(page.request, "/api/web/v1/session?pageId=platform-promptops");
  const headers = { origin, "X-CSRF-Token": csrfToken };
  try {
    await page.goto("/platform/promptops");
    await page.getByLabel("搜索文件").fill(path);
    await page.getByTitle(path, { exact: true }).click();
    await expect(page.getByRole("textbox", { name: "工具名称", exact: true })).toHaveAttribute(
      "readonly",
      "",
    );
    await expect(
      page.getByRole("region", { name: "完整工具定义 · 参数 Schema 只读", exact: true }),
    ).toContainText("source_query_id");
    const altered = structuredClone(original);
    altered.function.parameters.properties.code.type = "number";
    const rejected = await page.request.post(base + "file-draft", {
      headers,
      data: {
        path,
        body: JSON.stringify(altered),
        expected_edit_rev: baseline.edit_rev,
        base_epoch: baseline.active_epoch,
        request_id: crypto.randomUUID(),
      },
    });
    expect(rejected.status(), await rejected.text()).toBe(422);
    await page.getByRole("textbox", { name: "工具描述", exact: true }).fill(marker);
    await page.getByRole("textbox", { name: "调用示例（JSON）", exact: true }).fill("[");
    await expect(page.getByRole("button", { name: "保存草稿", exact: true })).toBeDisabled();
    const examples = [
      {
        description: "读取实际查询结果首行",
        arguments: { source_query_id: "query-result-ref", code: "result = data[0]" },
      },
    ];
    await page
      .getByRole("textbox", { name: "调用示例（JSON）", exact: true })
      .fill(JSON.stringify(examples, null, 2));
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.getByText("草稿已保存。", { exact: true })).toBeVisible();
    const draft = await json(page.request, base + "file?path=" + encodeURIComponent(path));
    expect(draft.body).toBe(baseline.body);
    expect(JSON.parse(draft.draft_body).examples).toEqual(examples);
    await page.getByRole("button", { name: "发布此文件", exact: true }).click();
    await page.getByLabel("发布说明").fill("验证工具描述与示例");
    await page.getByRole("button", { name: "确认发布此文件", exact: true }).click();
    await expect(page.getByText(/发布完成/)).toBeVisible();
    const published = JSON.parse(
      (await json(page.request, base + "file?path=" + encodeURIComponent(path))).body,
    );
    expect(published.function.description).toBe(marker);
    expect(published.examples).toEqual(examples);
    expect(published.function.parameters).toEqual(original.function.parameters);
  } finally {
    const latest = await json(page.request, base + "file?path=" + encodeURIComponent(path));
    const restored = await page.request.post(base + "file-draft", {
      headers,
      data: {
        path,
        body: baseline.body,
        expected_edit_rev: latest.edit_rev,
        base_epoch: latest.active_epoch,
        request_id: crypto.randomUUID(),
      },
    });
    expect(restored.status(), await restored.text()).toBe(200);
    const preview = await page.request.post(base + "release-preview", {
      headers,
      data: { action: "file", path },
    });
    expect(preview.status(), await preview.text()).toBe(200);
    const released = await page.request.post(base + "releases", {
      headers,
      data: {
        action: "file",
        path,
        confirmation_hash: (await preview.json()).confirmation_hash,
        request_id: crypto.randomUUID(),
        note: "恢复验收前的工具定义",
      },
    });
    expect(released.status(), await released.text()).toBe(200);
  }
});

test("published system file reaches a new live model call while the accepted run keeps its generation", async ({
  page,
  browser,
  request,
}, info) => {
  test.skip(
    process.env.COMPASS_CONTEXT_E2E !== "true",
    "Requires managed worker and controlled model.",
  );
  test.setTimeout(120000);
  await login(page.context(), "platform");
  const tenantAdmin = await browser.newContext({ baseURL: origin });
  const path = "tasks/business_qa/system.md",
    marker = "L21-" + crypto.randomUUID(),
    gate = crypto.randomUUID();
  const gateUrl = "http://127.0.0.1:" + process.env.COMPASS_E2E_MODEL_PORT + "/gates/" + gate;
  const baseline = await json(page.request, base + "file?path=" + encodeURIComponent(path));
  expect(baseline.has_draft).toBe(false);
  const analysis = "/t/" + tenant + "/w/" + workspace + "/analysis";
  const oldAnswer = "本轮系统版本 初始版本",
    newAnswer = "本轮系统版本 " + marker;
  const { csrfToken } = await json(page.request, "/api/web/v1/session?pageId=platform-promptops");
  const headers = { origin, "X-CSRF-Token": csrfToken };
  async function submit(question: string) {
    const chat = await tenantAdmin.newPage();
    await chat.goto(analysis);
    await chat.getByLabel("分析问题").fill(question);
    const submitted = chat.waitForResponse(
      (r) => r.url().endsWith("/analysis/sessions/submit") && r.request().method() === "POST",
    );
    await chat.getByRole("button", { name: "发送消息", exact: true }).click();
    const r = await submitted;
    expect(r.status(), await r.text()).toBe(202);
    await expect(chat).toHaveURL(new RegExp((await r.json()).sessionId));
    return chat;
  }
  async function publish(note: string) {
    await page.getByRole("button", { name: "发布此文件", exact: true }).click();
    await page.getByLabel("发布说明").fill(note);
    const done = page.waitForResponse(
      (r) => r.url().endsWith("/promptops/releases") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "确认发布此文件", exact: true }).click();
    const r = await done;
    expect(r.status(), await r.text()).toBe(200);
    return Date.now();
  }
  try {
    await login(tenantAdmin, "admin");
    await page.goto("/platform/promptops");
    await page.getByLabel("搜索文件").fill(path);
    await page.getByTitle(path, { exact: true }).click();
    await page
      .getByLabel("Markdown 正文")
      .fill(baseline.body.trimEnd() + "\n\n发布验收标识：" + marker + "\n");
    await page.getByRole("button", { name: "保存草稿" }).click();
    await expect(page.getByText("草稿已保存。")).toBeVisible();
    const question = "经营问答：校验在线发布。";
    const draft = await submit(question);
    const final = { name: "最终回答" };
    await expect(draft.getByRole("region", final)).toHaveText(oldAnswer);
    const inflight = await submit(question + " 发布门闩 " + gate);
    await expect.poll(async () => (await (await request.get(gateUrl)).json()).waiting).toBe(true);
    const committedAt = await publish("上线受管系统片段");
    await request.post(gateUrl + "/release");
    const newPage = await submit(question);
    await expect(newPage.getByRole("region", final)).toHaveText(newAnswer);
    const latency = Date.now() - committedAt;
    expect(latency).toBeLessThanOrEqual(60_000);
    info.annotations.push({ type: "publish-to-new-answer", description: latency + " ms" });
    console.log("PromptOps commit to rendered answer", latency, "ms");
    await expect(inflight.getByRole("region", final)).toHaveText(oldAnswer);
    await draft.reload();
    await expect(draft.getByRole("region", final)).toHaveText(oldAnswer);
    await page.getByRole("button", { name: "版本历史" }).click();
    await page
      .getByRole("button", { name: "查看 v" + baseline.current_version, exact: true })
      .click();
    page.once("dialog", (dialog) => void dialog.accept());
    await page.getByRole("button", { name: "将此版本恢复为草稿" }).click();
    await expect(page.getByText("已恢复为草稿。")).toBeVisible();
    await publish("恢复原系统片段");
    const after = await submit(question);
    await expect(after.getByRole("region", final)).toHaveText(oldAnswer);
    await newPage.reload();
    await expect(newPage.getByRole("region", final)).toHaveText(newAnswer);
  } finally {
    await request.post(gateUrl + "/release");
    // Restore only this test's draft or published content if a browser assertion was interrupted.
    const current = await json(page.request, base + "file?path=" + encodeURIComponent(path));
    if (
      [current.draft_body, current.body].some(
        (value) => typeof value === "string" && value.includes(marker),
      )
    ) {
      const restored = await page.request.post(base + "draft-from-version", {
        headers,
        data: {
          path,
          version: baseline.current_version,
          expected_edit_rev: current.edit_rev,
          base_epoch: current.active_epoch,
          request_id: crypto.randomUUID(),
        },
      });
      expect(restored.status()).toBe(200);
      const target = { path, action: "file" };
      const preview = await page.request.post(base + "release-preview", { headers, data: target });
      const release = await page.request.post(base + "releases", {
        headers,
        data: {
          ...target,
          confirmation_hash: (await preview.json()).confirmation_hash,
          request_id: crypto.randomUUID(),
          note: "浏览器验收后恢复",
        },
      });
      expect(release.status()).toBe(200);
    }
    await tenantAdmin.close();
  }
});
