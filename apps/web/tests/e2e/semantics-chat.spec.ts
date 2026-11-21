import { expect, test, type Page } from "@playwright/test";
import pg from "pg";

const origin = "http://127.0.0.1:" + (process.env.WEB_TEST_PORT ?? 8110);
const tenantId = "99500000-0000-4000-8000-000000000010";
const workspaceId = "99500000-0000-4000-8000-000000000020";
const scope = new URLSearchParams({ tenantId, workspaceId });
const entry = "/t/yuanshan/w/zongbu/analysis";

function restrictedSource() {
  const url = process.env.SEMANTIC_RETAIL_101_URL;
  if (!url || !/^\/compass_retail_test_[a-z0-9_]+$/.test(new URL(url).pathname)) {
    throw new Error("A dedicated restricted retail test connection is required.");
  }
  return new pg.Client({ connectionString: url });
}

async function selectLiveProvider(page: Page) {
  const picker = page.getByLabel("选择 Chat 主模型");
  if (process.env.SEMANTIC_TEST_PROVIDER) {
    await picker.selectOption(process.env.SEMANTIC_TEST_PROVIDER);
    return;
  }
  const provider = picker.locator('option:not([value=""]):not([value="auto"])').first();
  await expect(provider).toBeAttached();
  await picker.selectOption((await provider.getAttribute("value"))!);
}

test.skip(
  process.env.COMPASS_SEMANTIC_CHAT_E2E !== "true",
  "Requires a dedicated Registry-enabled Worker, published retail metrics and dual-department login.",
);

test("销售财务兼任用户刷新后仍能在原轮次选择口径并取得已发布结果", async ({ page }) => {
  test.setTimeout(420000);
  const source = restrictedSource();
  await source.connect();
  let cents: number;
  try {
    await source.query("BEGIN READ ONLY");
    const { rows } = await source.query(
      `SELECT sum(amount_cent)::text AS paid FROM retail_course.payment
       WHERE status='SUCCEEDED' AND attempted_at >= '2025-11-17T00:00:00+08:00'
         AND attempted_at < '2025-11-24T00:00:00+08:00' AND available_at <= '2026-04-01T00:00:00+08:00'`,
    );
    cents = Number(rows[0].paid);
    expect(cents).toBeGreaterThan(0);
  } finally {
    await source.end();
  }
  const auth = await page.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: {
      loginName: process.env.SEMANTIC_DUAL_LOGIN,
      password: process.env.WEB_TEST_PASSWORD,
    },
  });
  expect(auth.status()).toBe(200);
  let session = process.env.SEMANTIC_CHOICE_SESSION;
  if (!session) {
    await page.goto(entry);
    await selectLiveProvider(page);
    await page
      .getByLabel("分析问题")
      .fill("经营问答：从 2025-11-17（含）到 2025-11-24（不含），销售额合计多少？");
    const receipt = page.waitForResponse(
      (r) => r.url().endsWith("/analysis/sessions/submit") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "发送消息", exact: true }).click();
    const response = await receipt;
    expect(response.status()).toBe(202);
    session = (await response.json()).sessionId;
  }
  await page.goto(entry + "?session=" + session);
  const chooseMetric = page.getByRole("group", { name: "本次查询口径" });
  await expect(chooseMetric).toBeVisible({ timeout: 150000 });
  await expect(chooseMetric.getByRole("radio")).toHaveCount(2);
  await expect(chooseMetric).toContainText("确认收入");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  const paidOption = page.getByRole("radio", { name: /支付 GMV/ });
  await expect(paidOption).toBeVisible();
  await paidOption.focus();
  await page.keyboard.press("Space");
  await expect(paidOption).toBeChecked();
  const decided = page.waitForResponse(
    (r) => /\/decisions$/.test(r.url()) && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: /选择并继续|确认使用/ }).click();
  const decision = await decided;
  expect(decision.status()).toBe(200);
  const runId = decision.url().split("/").at(-2)!;
  await expect
    .poll(
      async () => {
        const res = await page.request.get(
          "/api/web/v1/analysis/sessions/" + session + "?" + scope,
        );
        expect(res.status()).toBe(200);
        const item = await res.json();
        expect(item.latestRunId).toBe(runId);
        return item.executionStatus;
      },
      { timeout: 240000, intervals: [1000, 3000, 5000] },
    )
    .toBe("completed");
  const final = page.getByRole("region", { name: "最终回答" });
  await expect(final).toContainText(/支付|成交额/);
  const result = (await final.innerText()).replaceAll(",", "").replaceAll("，", "");
  expect(
    [String(cents), (cents / 100).toFixed(2)].some((value) => result.includes(value)),
  ).toBeTruthy();
  await expect(page).toHaveURL(new RegExp("session=" + session));
});

test("用户确认临时计算后编辑建议，重试保存仍然只有一份个人草稿", async ({ page }) => {
  test.setTimeout(420000);
  const auth = await page.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: {
      loginName: process.env.SEMANTIC_DUAL_LOGIN,
      password: process.env.WEB_TEST_PASSWORD,
    },
  });
  expect(auth.status()).toBe(200);
  let sessionId = process.env.SEMANTIC_OPEN_SESSION;
  let runId: string;
  if (sessionId) {
    const existing = await page.request.get(
      "/api/web/v1/analysis/sessions/" + sessionId + "?" + scope,
    );
    expect(existing.status()).toBe(200);
    runId = (await existing.json()).latestRunId;
  } else {
    await page.goto(entry);
    await selectLiveProvider(page);
    await page
      .getByLabel("分析问题")
      .fill(
        "经营问答：给我一个数，2025-10-01（含）至 2025-10-08（不含）成功支付使用了几种不同的支付方式？这个临时口径按 method 去重，请通过工作区受管连接的 retail_course.payment 只读计算，status 为 SUCCEEDED，时间按 attempted_at。只需返回种数及其计算口径。",
      );
    const submitted = page.waitForResponse(
      (candidate) =>
        candidate.url().endsWith("/analysis/sessions/submit") &&
        candidate.request().method() === "POST",
    );
    await page.getByRole("button", { name: "发送消息", exact: true }).click();
    const response = await submitted;
    expect(response.status()).toBe(202);
    ({ runId, sessionId } = await response.json());
  }
  await expect
    .poll(
      async () => {
        const r = await page.request.get(
          "/api/web/v1/analysis/sessions/" + sessionId + "?" + scope,
        );
        return r.status() === 200 ? (await r.json()).executionStatus : undefined;
      },
      { timeout: 220000, intervals: [1000, 3000, 5000] },
    )
    .toBe("completed");
  await page.goto(entry + "?session=" + sessionId);
  const answer = page.getByRole("region", { name: "最终回答" });
  await expect(answer).toBeVisible();
  const source = restrictedSource();
  await source.connect();
  try {
    await source.query("BEGIN READ ONLY");
    const counts = await source.query(
      `SELECT count(DISTINCT method)::int AS count FROM retail_course.payment WHERE status='SUCCEEDED'
       AND attempted_at >= '2025-10-01T00:00:00+08:00'
       AND attempted_at < '2025-10-08T00:00:00+08:00'`,
    );
    expect(counts.rows[0].count).toBeGreaterThan(1);
    const report = (await answer.innerText()).replaceAll(",", "");
    expect(report).toMatch(new RegExp(counts.rows[0].count + "\\s*(?:种|类)"));
  } finally {
    await source.end();
  }
  const results = page.getByRole("region", { name: "临时查询结果" });
  const row = results
    .locator(":scope > div")
    .filter({ hasText: /method|支付方式/i })
    .last();
  await expect(row.getByRole("button", { name: "确认查询结果" })).toBeVisible();
  await expect(row.getByText(/这项汇总可在其他期间复用/)).toHaveCount(0);
  const confirming = page.waitForResponse(
    (r) =>
      r.url().endsWith("/runs/" + runId + "/semantic-results") &&
      r.request().method() === "POST" &&
      r.request().postDataJSON()?.action === "confirm",
  );
  await row.getByRole("button", { name: "确认查询结果" }).click();
  const confirmed = await confirming;
  expect(confirmed.status()).toBe(200);
  if ((await confirmed.json()).reusable) {
    await expect(row.getByText(/这项汇总可在其他期间复用/)).toBeVisible();
  } else {
    await expect(row.getByText(/这项汇总可在其他期间复用/)).toHaveCount(0);
  }
  await row.getByRole("button", { name: "保存为指标草稿" }).click();
  const form = row.locator("form");
  await expect(form.getByLabel("指标名称", { exact: true })).not.toHaveValue("");
  await expect(form.getByLabel("公式", { exact: true })).not.toHaveValue("");
  const code = "qa.payment_method_" + crypto.randomUUID().slice(0, 8);
  await form.getByLabel("指标名称", { exact: true }).fill("成功支付方式种数");
  await form.getByLabel("指标代码", { exact: true }).fill(code);
  await form.getByLabel("业务域", { exact: true }).fill("销售");
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith("/runs/" + runId + "/semantic-results") && r.request().method() === "POST",
  );
  await form.getByRole("button", { name: "保存草稿", exact: true }).click();
  const final = await saved;
  expect(final.status(), await final.text()).toBe(200);
  const receipt = await final.json();
  expect(receipt.status).toBe("saved");
  const request = final.request();
  const again = await page.request.post(new URL(request.url()).pathname, {
    headers: { origin, "X-CSRF-Token": (await request.allHeaders())["x-csrf-token"] },
    data: request.postDataJSON(),
  });
  expect(again.status(), await again.text()).toBe(200);
  expect((await again.json()).draft_id).toBe(receipt.draft_id);
  const seen = await page.request.get("/api/semantic/assets?q=" + code + "&" + scope);
  expect((await seen.json()).items.map((v: { code: string }) => v.code)).toEqual([code]);
  const asset = await page.request.get("/api/semantic/assets/" + receipt.draft_id + "?" + scope);
  const detail = await asset.json();
  expect(detail.state).toBe("draft");
  expect(detail.owner_scope_id).toBe(detail.usage_scope_id);
  expect(detail.source_id).toBeTruthy();
  await row.getByRole("link", { name: "查看指标草稿" }).click();
  await expect(page.getByRole("heading", { name: "成功支付方式种数", exact: true })).toBeVisible();
});
