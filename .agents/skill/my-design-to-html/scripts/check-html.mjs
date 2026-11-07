import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";
import { chromium } from "playwright";

export async function checkHtml(filename, options = {}) {
  const reportDir = options.reportDir
    ? path.resolve(options.reportDir)
    : await fs.mkdtemp(path.join(os.tmpdir(), "design-html-qa-"));
  const isolated = path.join(reportDir, "standalone", "review.html");
  await fs.mkdir(path.dirname(isolated), { recursive: true });
  assert.notEqual(path.resolve(filename), isolated, "Check an independent copy of the artifact");
  await fs.copyFile(filename, isolated);
  const browser = await chromium.launch({ headless: true });
  const errors = [],
    requests = [];
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1050 },
      permissions: ["clipboard-read", "clipboard-write"],
      reducedMotion: "reduce",
      acceptDownloads: true,
    });
    await context.setOffline(true);
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (
        request.url() !== pathToFileURL(isolated).href &&
        !request.url().startsWith("data:") &&
        !request.url().startsWith("blob:")
      )
        requests.push(request.url());
    });
    await page.goto(pathToFileURL(isolated).href);
    const structure = await page.evaluate(() => {
      const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
      const seen = new Set();
      const duplicateIds = ids.filter((id) => seen.has(id) || !seen.add(id));
      const anchors = [...document.querySelectorAll('a[href^="#"]')];
      return {
        diagrams: document.querySelectorAll(".diagram").length,
        tables: document.querySelectorAll(".document table").length,
        codes: document.querySelectorAll(".code-block").length,
        headings: [...document.querySelectorAll(".document h2,.document h3,.document h4")].map(
          (node) => node.textContent,
        ),
        duplicateIds,
        brokenLinks: anchors
          .filter((link) => !document.getElementById(decodeURIComponent(link.hash.slice(1))))
          .map((link) => link.hash),
        externalResources: [
          ...document.querySelectorAll("script[src],link[href],img[src],iframe[src]"),
        ].map((node) => node.outerHTML),
      };
    });
    assert.deepEqual(structure.duplicateIds, [], "IDs must be unique across diagrams and sections");
    assert.deepEqual(structure.brokenLinks, [], "Internal links must resolve");
    assert.deepEqual(structure.externalResources, [], "Artifact must be self-contained");
    await page.screenshot({ path: path.join(reportDir, "desktop.png") });
    const table = page.locator(".document .table-wrap").first();
    if (structure.tables)
      await table.screenshot({ path: path.join(reportDir, "table-desktop.png") });
    const codeBlocks = page.locator(".document > .code-block");
    const longestIndex = await codeBlocks.evaluateAll((blocks) => {
      let best = 0;
      blocks.forEach((block, index) => {
        if (
          block.querySelectorAll(".code-line").length >
          blocks[best].querySelectorAll(".code-line").length
        )
          best = index;
      });
      return best;
    });
    const directCode = codeBlocks.nth(longestIndex);
    const copyButton = directCode.locator("[data-copy]");
    let copy = null,
      expand = null,
      zoom = null,
      svgExport = null;
    const mismatch = await page.locator(".code-block").evaluateAll((blocks) => {
      const { codes } = JSON.parse(document.getElementById("document-data").textContent);
      return blocks
        .filter((block) => {
          const index = Number(block.querySelector("[data-copy]").dataset.copy);
          const displayed = [...block.querySelectorAll(".line-content")]
            .map((line) => line.textContent)
            .join("\n");
          return displayed !== codes[index].replace(/\n$/, "");
        })
        .map((block) => block.id);
    });
    assert.deepEqual(mismatch, [], "Syntax highlighting must preserve the complete code text");
    if (await copyButton.count()) {
      const expected = await copyButton.evaluate(
        (button) =>
          JSON.parse(document.getElementById("document-data").textContent).codes[
            Number(button.dataset.copy)
          ],
      );
      await copyButton.click();
      await page.waitForFunction(
        () => document.querySelector(".toast").textContent === "代码已复制",
      );
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), expected);
      copy = true;
    }
    const expandButton = directCode.locator("[data-expand]");
    if (await expandButton.count()) {
      await expandButton.click();
      assert.equal(await expandButton.getAttribute("aria-expanded"), "true");
      await directCode.screenshot({ path: path.join(reportDir, "code-desktop.png") });
      await expandButton.click();
      assert.equal(await expandButton.getAttribute("aria-expanded"), "false");
      expand = true;
    }
    await page.locator("[data-search]").click();
    await page.locator("#search-input").fill(structure.headings[0]);
    assert.ok(await page.locator(".search-result").count());
    await page.locator(".search-result").first().click();
    assert.equal(await page.locator("#search-dialog").evaluate((node) => node.open), false);
    assert.ok(await page.evaluate(() => location.hash.startsWith("#")));
    if (structure.diagrams) {
      const drawings = page.locator(".diagram-stage > svg");
      const largest = await drawings.evaluateAll((nodes) => {
        const sizes = nodes.map(
          (node) => node.getBoundingClientRect().width * node.getBoundingClientRect().height,
        );
        return sizes.indexOf(Math.max(...sizes));
      });
      await drawings.nth(largest).screenshot({ path: path.join(reportDir, "diagram-desktop.png") });
      await page.locator("[data-diagram]").nth(largest).click();
      assert.equal(await page.locator("#diagram-dialog").evaluate((node) => node.open), true);
      await page.locator('[data-zoom="actual"]').click();
      const before = await page.locator("#zoom-level").textContent();
      await page.locator('[data-zoom="out"]').click();
      assert.notEqual(await page.locator("#zoom-level").textContent(), before);
      await page.locator('[data-zoom="actual"]').click();
      assert.equal(await page.locator("#zoom-level").textContent(), "100%");
      await page.locator('[data-zoom="fit"]').click();
      assert.equal(
        await page.evaluate(() => {
          const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
          return new Set(ids).size === ids.length;
        }),
        true,
        "Zoom must not duplicate SVG IDs",
      );
      const downloadPromise = page.waitForEvent("download");
      await page.locator("#download-svg").click();
      const download = await downloadPromise;
      await download.saveAs(path.join(reportDir, "diagram.svg"));
      assert.match(await fs.readFile(path.join(reportDir, "diagram.svg"), "utf8"), /<svg[\s>]/);
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("#diagram-dialog").evaluate((node) => node.open), false);
      zoom = svgExport = true;
    }
    const layouts = [];
    for (const [width, height] of [
      [375, 812],
      [768, 1024],
      [844, 390],
      [1440, 1050],
    ]) {
      await page.setViewportSize({ width, height });
      await page.evaluate(() => scrollTo(0, 0));
      const layout = await page.evaluate(() => ({
        width: innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));
      assert.ok(layout.scrollWidth <= width + 1, `Page overflow at ${width}px`);
      layouts.push(layout);
      await page.screenshot({ path: path.join(reportDir, `viewport-${width}.png`) });
    }
    await page.setViewportSize({ width: 375, height: 812 });
    if (structure.tables) {
      await table.scrollIntoViewIfNeeded();
      await table.evaluate((node) => {
        node.scrollLeft = node.scrollWidth;
      });
      await page.screenshot({ path: path.join(reportDir, "table-mobile.png") });
    }
    if (await directCode.count()) {
      await directCode.scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(reportDir, "code-mobile.png") });
    }
    await page.emulateMedia({ media: "print" });
    for (const pre of await page.locator(".document > .code-block pre").all())
      assert.equal(await pre.evaluate((node) => getComputedStyle(node).maxHeight), "none");
    assert.deepEqual(errors, [], "Page scripts must run without errors");
    assert.deepEqual(requests, [], "Offline copy must not depend on other files or network");
    const report = {
      structure,
      copy,
      expand,
      search: true,
      zoom,
      svgExport,
      print: true,
      layouts,
      errors,
      requests,
      reportDir,
    };
    await fs.writeFile(path.join(reportDir, "report.json"), JSON.stringify(report, null, 2) + "\n");
    return report;
  } finally {
    await browser.close();
  }
}

if (
  process.argv[1] &&
  fileURLToPath(import.meta.url) === (await fs.realpath(process.argv[1]).catch(() => ""))
) {
  try {
    const { values, positionals } = parseArgs({
      options: { "report-dir": { type: "string" } },
      allowPositionals: true,
    });
    if (positionals.length !== 1)
      throw new Error("Usage: node check-html.mjs <review.html> [--report-dir <directory>]");
    console.log(
      JSON.stringify(await checkHtml(positionals[0], { reportDir: values["report-dir"] })),
    );
  } catch (error) {
    console.error(error.stack);
    process.exitCode = 1;
  }
}
