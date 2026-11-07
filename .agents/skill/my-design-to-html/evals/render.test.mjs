import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { renderReview } from "../scripts/render.mjs";
import { checkHtml } from "../scripts/check-html.mjs";
import { renderDiagrams } from "../scripts/diagrams.mjs";

const fixtures = fileURLToPath(new URL("./", import.meta.url));
const run = promisify(execFile);
async function workspace(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "design-html-eval-"));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  for (const name of ["source.md", "guide.md", "edition.json"])
    await fs.copyFile(path.join(fixtures, name), path.join(dir, name));
  return dir;
}

test("independent offline edition preserves source, code and all reading interactions", async (t) => {
  const dir = await workspace(t);
  const source = await fs.readFile(path.join(dir, "source.md"), "utf8");
  const rendered = await renderReview(path.join(dir, "edition.json"));
  assert.equal(await fs.readFile(path.join(dir, "source.md"), "utf8"), source);
  const html = await fs.readFile(rendered.output, "utf8");
  const payload = JSON.parse(html.match(/id="document-data">([\s\S]*?)<\/script>/)[1]);
  assert.ok(payload.codes.includes(source.match(/```python\n([\s\S]*?)```/)[1]));
  assert.ok(!html.includes("ExecutionGate"));
  assert.ok(!html.includes("尚未评审。生成阅读版不改变此状态。"));
  const report = await checkHtml(rendered.output, { reportDir: path.join(dir, "qa") });
  assert.equal(report.structure.diagrams, 3);
  assert.equal(report.structure.tables, 3);
  assert.equal(report.copy, true);
  assert.equal(report.expand, true);
  assert.equal(report.search, true);
  assert.equal(report.zoom, true);
  assert.equal(report.svgExport, true);
});

test("repeated diagrams and hero have distinct SVG references", async (t) => {
  const dir = await workspace(t);
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><defs><marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6"><path d="M0 0L10 5L0 10Z"/></marker></defs><path d="M10 50H180" stroke="green" marker-end="url(#a)"/></svg>';
  await fs.writeFile(path.join(dir, "hero.svg"), svg);
  await fs.writeFile(
    path.join(dir, "guide.md"),
    "## 目标\n\n|问题|目标|指标|\n|---|---|---|\n|重复图|独立显示|2 张|\n\n```diagram-svg\n" +
      svg +
      "\n```\n\n```diagram-svg\n" +
      svg +
      "\n```\n",
  );
  const config = JSON.parse(await fs.readFile(path.join(dir, "edition.json"), "utf8"));
  config.hero = "hero.svg";
  await fs.writeFile(path.join(dir, "edition.json"), JSON.stringify(config));
  const result = await renderReview(path.join(dir, "edition.json"));
  const report = await checkHtml(result.output, { reportDir: path.join(dir, "qa") });
  assert.equal(report.structure.diagrams, 3);
});

test("minimal design needs no diagrams, numeric cards or implementation code", async (t) => {
  const dir = await workspace(t);
  await fs.writeFile(
    path.join(dir, "guide.md"),
    "## 目标\n\n| 核心问题 | 实现目标 | 量化指标（设计目标） |\n| --- | --- | --- |\n| 标题不清楚 | 显示业务名称 | 未设定 |\n",
  );
  await fs.writeFile(
    path.join(dir, "edition.json"),
    JSON.stringify({
      title: "页面标题",
      source: "source.md",
      guide: "guide.md",
      output: "review.html",
    }),
  );
  const result = await renderReview(path.join(dir, "edition.json"));
  const report = await checkHtml(result.output, { reportDir: path.join(dir, "qa") });
  assert.equal(report.structure.diagrams, 0);
  assert.equal(report.copy, null);
  assert.equal(report.zoom, null);
});

test("code changes are picked up and ambiguous selection preserves the previous artifact", async (t) => {
  const dir = await workspace(t);
  const configPath = path.join(dir, "edition.json");
  await fs.writeFile(path.join(dir, "guide.md"), "## 目标\n\n{{code:export}}\n");
  const sourcePath = path.join(dir, "source.md");
  const source = await fs.readFile(sourcePath, "utf8");
  await fs.writeFile(sourcePath, source.replace("BATCH_SIZE = 500", "BATCH_SIZE = 750"));
  const result = await renderReview(configPath);
  const before = await fs.readFile(result.output, "utf8");
  const payload = JSON.parse(before.match(/id="document-data">([\s\S]*?)<\/script>/)[1]);
  assert.match(payload.codes[0], /BATCH_SIZE = 750/);
  await fs.appendFile(
    sourcePath,
    "\n```python\nasync def export_report_shadow():\n    return 1\n```\n",
  );
  await assert.rejects(renderReview(configPath), /matched 2 blocks/);
  assert.equal(await fs.readFile(result.output, "utf8"), before);
});

test("code text containing HTML cannot break the generated page", async (t) => {
  const dir = await workspace(t);
  await fs.writeFile(
    path.join(dir, "guide.md"),
    '## 目标\n\n```javascript\nconst payload = "</script><script>window.bad = true</script>";\n```\n',
  );
  const result = await renderReview(path.join(dir, "edition.json"));
  const report = await checkHtml(result.output, { reportDir: path.join(dir, "qa") });
  assert.equal(report.copy, true);
  assert.deepEqual(report.errors, []);
});

test("SVG scripts and external resources are rejected before output", async () => {
  for (const content of [
    "<script>alert(1)</script>",
    '<image href="https://example.com/a.png"/>',
    '<path onclick="alert(1)"/>',
    '<style>@import "https://example.com/a.css";</style>',
  ])
    await assert.rejects(
      renderDiagrams([
        {
          kind: "diagram-svg",
          title: "invalid",
          code: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">${content}</svg>`,
        },
      ]),
      /unsupported/i,
    );
});

test("CLI works through a discovered symlink from an unrelated working directory", async (t) => {
  const dir = await workspace(t);
  const link = path.join(dir, "installed-skill");
  await fs.symlink(fileURLToPath(new URL("../", import.meta.url)), link, "dir");
  await fs.writeFile(
    path.join(dir, "guide.md"),
    "## 目标\n\n|问题|目标|指标|\n|---|---|---|\n|目录变化|保持可重建|未设定|\n",
  );
  const output = path.join(dir, "out", "review.html");
  const result = await run(
    process.execPath,
    [path.join(link, "scripts/render.mjs"), path.join(dir, "edition.json"), "--output", output],
    { cwd: os.tmpdir() },
  );
  assert.equal(JSON.parse(result.stdout).output, output);
  assert.ok((await fs.stat(output)).size > 1000);
});
