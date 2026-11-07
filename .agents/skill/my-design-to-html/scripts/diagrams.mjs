import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const packages = fileURLToPath(new URL("../node_modules/", import.meta.url));

export async function renderDiagrams(graphs) {
  if (!graphs.length) return [];
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://127.0.0.1").pathname);
      if (pathname === "/") {
        response.setHeader("Content-Type", "text/html; charset=utf-8");
        response.end('<!doctype html><html><meta charset="utf-8"><body></body></html>');
        return;
      }
      const filename = path.resolve(packages, "." + pathname);
      if (!filename.startsWith(packages)) {
        response.writeHead(403).end();
        return;
      }
      response.setHeader("Content-Type", "text/javascript; charset=utf-8");
      response.end(await fs.readFile(filename));
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.route("**/*", (route) =>
      route
        .request()
        .url()
        .startsWith(origin + "/")
        ? route.continue()
        : route.abort(),
    );
    await page.goto(origin);
    if (graphs.some((graph) => graph.kind === "mermaid")) {
      await page.evaluate(async () => {
        const { default: mermaid } = await import("/mermaid/dist/mermaid.esm.min.mjs");
        window.mermaid = mermaid;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          htmlLabels: false,
          theme: "base",
          deterministicIds: true,
          maxTextSize: 100000,
          fontFamily: "-apple-system, BlinkMacSystemFont, PingFang SC, Microsoft YaHei, sans-serif",
          themeVariables: {
            primaryColor: "#e9f2df",
            primaryTextColor: "#20372a",
            primaryBorderColor: "#8ba67c",
            lineColor: "#6c8265",
            secondaryColor: "#f7f8ee",
            tertiaryColor: "#e9eff0",
            mainBkg: "#eef4e7",
            clusterBkg: "#f6f8f1",
            clusterBorder: "#b4c6aa",
            edgeLabelBackground: "#fff",
            fontSize: "15px",
            actorBkg: "#e8f1df",
            actorBorder: "#8ba67c",
            actorTextColor: "#20372a",
            signalColor: "#56704d",
          },
          flowchart: { htmlLabels: false, curve: "linear", padding: 16 },
          class: { htmlLabels: false },
          sequence: { useMaxWidth: false, actorMargin: 38, messageMargin: 34, wrap: true },
          er: { useMaxWidth: false },
        });
      });
    }
    const rendered = [];
    for (const [index, graph] of graphs.entries()) {
      rendered.push(
        await page.evaluate(
          async ({ graph, index }) => {
            const markup =
              graph.kind === "mermaid"
                ? (await window.mermaid.render(`render-${index}`, graph.code)).svg
                : graph.code;
            const doc = new DOMParser().parseFromString(markup, "image/svg+xml");
            const svg = doc.documentElement;
            if (doc.querySelector("parsererror") || svg.localName !== "svg")
              throw new Error(`Invalid SVG: ${graph.title}`);
            const elements = [svg, ...svg.querySelectorAll("*")];
            for (const element of elements) {
              if (
                ["script", "foreignobject", "iframe", "image", "animate", "set"].includes(
                  element.localName.toLowerCase(),
                )
              )
                throw new Error(`Unsupported SVG element: ${element.localName}`);
              for (const attribute of [...element.attributes]) {
                if (/^on/i.test(attribute.name))
                  throw new Error("SVG event handlers are unsupported");
                if (/^(?:xlink:)?href$/i.test(attribute.name) && !attribute.value.startsWith("#"))
                  throw new Error("SVG external resources are unsupported");
                for (const match of attribute.value.matchAll(/url\(\s*['"]?([^'"\s)]+)/gi))
                  if (!match[1].startsWith("#"))
                    throw new Error("SVG external URLs are unsupported");
              }
              if (element.localName === "style") {
                if (/@import/i.test(element.textContent))
                  throw new Error("SVG CSS imports are unsupported");
                for (const match of element.textContent.matchAll(/url\(\s*['"]?([^'"\s)]+)/gi))
                  if (!match[1].startsWith("#"))
                    throw new Error("SVG CSS external URLs are unsupported");
              }
            }
            const ids = new Map();
            for (const element of elements) {
              if (!element.id) continue;
              if (ids.has(element.id)) throw new Error(`Duplicate SVG id: ${element.id}`);
              ids.set(element.id, `drawing-${index}-${element.id}`);
            }
            for (const element of elements) {
              for (const attribute of [...element.attributes]) {
                let value = attribute.value.replace(/url\(['"]?#([^)'"]+)['"]?\)/g, (match, id) =>
                  ids.has(id) ? `url(#${ids.get(id)})` : match,
                );
                if (attribute.name === "id") value = ids.get(value);
                else if (/^(?:xlink:)?href$/.test(attribute.name) && ids.has(value.slice(1)))
                  value = "#" + ids.get(value.slice(1));
                else if (/^aria-(?:labelledby|describedby)$/.test(attribute.name))
                  value = value
                    .split(/\s+/)
                    .map((id) => ids.get(id) ?? id)
                    .join(" ");
                element.setAttribute(attribute.name, value);
              }
              if (element.localName === "style")
                element.textContent = element.textContent.replace(
                  /#([A-Za-z_][\w:-]*)/g,
                  (match, id) => (ids.has(id) ? "#" + ids.get(id) : match),
                );
              if (element.hasAttribute("style")) {
                const style = element.style.cssText;
                if (style) element.setAttribute("style", style);
                else element.removeAttribute("style");
              }
            }
            const box = svg
              .getAttribute("viewBox")
              ?.trim()
              .split(/[\s,]+/)
              .map(Number);
            if (
              !box ||
              box.length !== 4 ||
              !box.every(Number.isFinite) ||
              box[2] <= 0 ||
              box[3] <= 0
            )
              throw new Error(`SVG needs a positive viewBox: ${graph.title}`);
            svg.setAttribute("role", "img");
            svg.setAttribute("aria-label", graph.title);
            svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
            svg.style.width = `${box[2]}px`;
            svg.style.maxWidth = "100%";
            svg.style.height = "auto";
            return svg.outerHTML;
          },
          { graph, index },
        ),
      );
    }
    return rendered;
  } finally {
    await browser?.close();
    await new Promise((resolve) => server.close(resolve));
  }
}
