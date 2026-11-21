import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

const tenant = "99500000-0000-4000-8000-000000000010",
  workspace = "99500000-0000-4000-8000-000000000020";
const origin = "http://127.0.0.1:" + (process.env.WEB_TEST_PORT ?? 8110);
const analysis = "/t/" + tenant + "/w/" + workspace + "/analysis";
const cases = [
  { task: "经营问答", artifact: null, mime: null },
  { task: "数据分析", artifact: "analysis.csv", mime: "text/csv" },
  { task: "经营看板", artifact: "dashboard.json", mime: "application/json" },
  { task: "经营报告", artifact: "report.md", mime: "text/markdown" },
] as const;

// The only controlled dependency is the external model. Runs use a real worker, PG and Redis.
test.skip(
  process.env.COMPASS_CONTEXT_E2E !== "true",
  "Requires isolated managed Runtime and retail fixture.",
);
test.setTimeout(120000);
async function attachment(page: Page, label: "看板" | "报告") {
  await page.getByRole("button", { name: "打开" + label, exact: true }).click();
  const dialog = page.getByRole("dialog", { name: label });
  await expect(dialog).toContainText(/120|200/);
  if (label === "看板") {
    const input = dialog.getByLabel("筛选当前快照");
    await input.fill("120");
    await expect(dialog.getByRole("table").first()).toContainText("120");
    await input.fill("没有这个区域");
    await expect(dialog.getByText("当前筛选没有可显示的数据。").first()).toBeVisible();
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
}
for (const { task, artifact, mime } of cases) {
  test(task + "通过授权查询完成，并从会话重新打开", async ({ page }) => {
    const login = await page.request.post("/api/web/v1/auth/login", {
      headers: { origin },
      data: { loginName: "teaching.admin", password: process.env.WEB_TEST_PASSWORD },
    });
    expect(login.status(), await login.text()).toBe(200);
    await page.goto(analysis);
    await expect(page.getByLabel("选择 Chat 主模型")).toHaveValue("auto");
    const receive = page.waitForResponse(
      (r) => r.url().endsWith("/analysis/sessions/submit") && r.request().method() === "POST",
    );
    await page
      .getByLabel("分析问题")
      .fill(task + "：比较两个区域的销售额，请根据已授权数据库查询本期金额。");
    await page.getByRole("button", { name: "发送消息", exact: true }).click();
    const accepted = await receive;
    expect(accepted.status(), await accepted.text()).toBe(202);
    const { sessionId, runId } = await accepted.json();
    const query = new URLSearchParams({ tenantId: tenant, workspaceId: workspace });
    const final = page.getByRole("region", { name: "最终回答" });
    await expect
      .poll(
        async () => {
          const r = await page.request.get(
            "/api/web/v1/analysis/sessions/" + sessionId + "/messages?" + query,
          );
          expect(r.status(), await r.text()).toBe(200);
          const { packets } = await r.json();
          const latest = packets
            .filter(
              (p: { runId: string; event: { type: string } }) =>
                p.runId === runId && p.event.type === "run.upsert",
            )
            .at(-1)?.event.payload;
          if (latest && ["failed", "canceled", "paused"].includes(latest.status))
            throw Error("Runtime " + latest.status + " / " + latest.reasonCode);
          return latest?.status;
        },
        { timeout: 65000, intervals: [500, 1000] },
      )
      .toBe("completed");
    await expect(final).toContainText(/120|200/);
    await expect(page).toHaveURL(new RegExp(sessionId));
    const previousText = await final.innerText();
    if (!artifact) {
      await expect(page.getByRole("link", { name: /下载(报告|看板|数据)/ })).toHaveCount(0);
      await page.reload();
      await expect(final).toHaveText(previousText);
      return;
    }
    const label = artifact === "dashboard.json" ? "看板" : artifact === "report.md" ? "报告" : null;
    const link = page.getByRole("link", { name: label ? "下载" + label : /下载数据/ });
    await expect(link).toBeVisible();
    const href = await link.getAttribute("href");
    expect(href).toContain(runId);
    const response = await page.request.get(href!);
    expect(response.status()).toBe(200);
    expect(response.headers()["cache-control"]).toContain("no-store");
    expect(response.headers()["content-type"]).toContain(mime!);
    const bytes = await response.body(),
      content = bytes.toString("utf8");
    expect(content).toMatch(/120|200/);
    if (artifact === "analysis.csv") expect(content).toContain("80");
    if (artifact === "dashboard.json") {
      const doc = JSON.parse(content);
      expect(doc.schemaVersion).toBe(1);
      expect(doc.widgets[0].rows.length).toBeGreaterThan(0);
    }
    if (label) await attachment(page, label);
    const downloaded = page.waitForEvent("download");
    await link.click();
    const download = await downloaded;
    expect(download.suggestedFilename()).toBe(artifact);
    expect(await readFile((await download.path())!)).toEqual(bytes);
    await page.reload();
    await expect(final).toHaveText(previousText);
    if (label) await attachment(page, label);
    expect(await link.getAttribute("href")).toBe(href);
    const again = await page.request.get(href!);
    expect(
      createHash("sha256")
        .update(await again.body())
        .digest("hex"),
    ).toBe(createHash("sha256").update(bytes).digest("hex"));
  });
}
