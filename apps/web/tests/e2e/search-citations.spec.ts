import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import pg from "pg";

test.skip(!process.env.SEARCH_CITATION_FIXTURE, "Requires the isolated citation report fixture.");
test("经营报告角标逐次验权，撤回报价后隐藏历史正文、下载与流回放", async ({ page }) => {
  const f = JSON.parse(await readFile(process.env.SEARCH_CITATION_FIXTURE!, "utf8")) as {
    tenantId: string;
    workspaceId: string;
    runId: string;
    sessionId: string;
    loginName: string;
    groupGrant: string;
    member: string;
  };
  const database = process.env.WEB_TEST_MIGRATION_DATABASE_URL!;
  expect(new URL(database).pathname).toMatch(/^\/compass_web_test_/);
  expect(f.loginName).toMatch(/^citation\./);
  const origin = "http://127.0.0.1:" + process.env.WEB_TEST_PORT;
  const scope = new URLSearchParams({ tenantId: f.tenantId, workspaceId: f.workspaceId });
  const base = "/api/web/v1/analysis/runs/" + f.runId;
  const signIn = await page.request.post("/api/web/v1/auth/login", {
    headers: { origin },
    data: { loginName: f.loginName, password: process.env.WEB_TEST_PASSWORD },
  });
  expect(signIn.status(), await signIn.text()).toBe(200);
  const list = await page.request.get(base + "/citations?" + scope);
  expect(list.status(), await list.text()).toBe(200);
  expect(list.headers()["cache-control"]).toContain("no-store");
  const { anchors } = await list.json();
  expect(anchors).toHaveLength(2);
  const detailPath = base + "/citations/" + anchors[0].citationId + "?" + scope;
  const unknown = await page.request.get(
    base + "/citations/00000000-0000-4000-8000-000000000001?" + scope,
  );
  expect(unknown.status()).toBe(404);
  const detail = await page.request.get(detailPath);
  expect(detail.status()).toBe(200);
  expect((await detail.json()).sources[0].locator).toContain("E-1009");
  await page.goto("/t/" + f.tenantId + "/w/" + f.workspaceId + "/analysis?session=" + f.sessionId);
  const button = page.getByRole("button", { name: "查看引用 1" });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(button).toBeVisible();
    await button.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "引用 1" });
    await expect(dialog).toContainText("E-1009");
    await expect(dialog).toContainText("6,000 元");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(button).toBeFocused();
    const table = page.getByRole("region", { name: "数据表格" });
    await expect(table.getByRole("cell", { name: "体验档", exact: true })).toBeVisible();
    await expect(table.getByRole("cell", { name: "15,000.00 元", exact: true })).toBeVisible();
    await table.focus();
    await page.keyboard.press("Tab");
    const tableCitation = page.getByRole("button", { name: "查看引用 2" });
    await expect(tableCitation).toBeFocused();
    await page.keyboard.press("Enter");
    const amounts = page.getByRole("dialog", { name: "引用 2" });
    await expect(amounts).toContainText("15,000 元");
    await page.keyboard.press("Escape");
    await expect(tableCitation).toBeFocused();
  }
  const download = page.getByRole("link", { name: "下载报告" });
  const href = await download.getAttribute("href");
  expect(href).toBeTruthy();
  expect((await page.request.get(href!)).status()).toBe(200);
  const db = new pg.Client({ connectionString: database });
  await db.connect();
  try {
    // Remove only the new test identity's private quotation group.
    const removed = await db.query(
      "DELETE FROM web.workspace_member WHERE id=$1 AND tenant_member_id=$2 RETURNING *",
      [f.groupGrant, f.member],
    );
    expect(removed.rowCount).toBe(1);
    try {
      const denied = await page.request.get(detailPath);
      expect(denied.status()).toBe(unknown.status());
      expect(await denied.text()).not.toContain("6,000");
      await button.click();
      await expect(page.getByRole("alert")).toContainText("当前无法查看");
      await page.keyboard.press("Escape");
      expect((await page.request.get(href!)).status()).toBe(403);
      const stream = await page.request.get(base + "/events?after=0-0&" + scope);
      expect(stream.status()).toBe(200);
      const replay = await stream.text();
      expect(replay).toContain("来源当前不可读取");
      expect(replay).not.toMatch(/6,000|E-1009/);
      await page.reload();
      await expect(page.getByRole("region", { name: "最终回答" })).toContainText(
        "来源当前不可读取",
      );
      await expect(button).toHaveCount(0);
      await expect(download).toHaveCount(0);
    } finally {
      const row = removed.rows[0];
      await db.query(
        "INSERT INTO web.workspace_member(id,tenant_id,workspace_id,tenant_member_id,role_key,permission_keys,version,created_at,updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        [
          row.id,
          row.tenant_id,
          row.workspace_id,
          row.tenant_member_id,
          row.role_key,
          row.permission_keys,
          row.version,
          row.created_at,
          row.updated_at,
        ],
      );
    }
    expect((await page.request.get(detailPath)).status()).toBe(200);
  } finally {
    await db.end();
  }
});
