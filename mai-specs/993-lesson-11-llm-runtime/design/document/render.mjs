import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import MarkdownIt from "markdown-it";
import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import json from "highlight.js/lib/languages/json";
import bash from "highlight.js/lib/languages/bash";
import { createLearnerDocument, createLearnerTemplate } from "./learner.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const design = path.dirname(here);
const root = path.resolve(here, "../../../..");
const require = createRequire(path.join(root, "apps/web/package.json"));
const { chromium } = require("@playwright/test");
const prettier = require("prettier");
const args = process.argv.slice(2);
const edition =
  args[0] === "--edition" && args.length === 2
    ? (await import(pathToFileURL(path.resolve(args[1])).href)).default
    : null;
const learner = !!edition || args.includes("--v3");
if (!edition && args.some((arg) => arg !== "--v3"))
  throw new Error("Usage: node render.mjs [--v3 | --edition <module>]");
const sourceFile = edition?.sourceFile ?? path.join(design, "TECH_DESIGN.v2.md");
const output =
  edition?.output ?? path.join(design, learner ? "TECH_DESIGN.v3.html" : "TECH_DESIGN.v2.html");
const original = await fs.readFile(sourceFile, "utf8");
const source = edition
  ? await edition.createDocument(original)
  : learner
    ? await createLearnerDocument(original)
    : original;
const implementationFile = path.resolve(design, "../IMPLEMENTATION.md");
const implementation = learner ? "" : await fs.readFile(implementationFile, "utf8");
const current = implementation.split("## 2026-11-04 V2 实施与验收\n")[1]?.split("\n## 后续事项")[0];
if (!learner && !current) throw new Error("Current implementation section missing");
hljs.registerLanguage("python", python);
hljs.registerLanguage("json", json);
hljs.registerLanguage("bash", bash);
const escape = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
const icons = {
  compass: '<circle cx="12" cy="12" r="10"/><path d="m16 8-2.5 5.5L8 16l2.5-5.5Z"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  "arrow-up": '<path d="M6 18 18 6M6 6h12v12"/>',
  database:
    '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  boxes:
    '<rect x="8" y="2" width="8" height="6" rx="1"/><rect x="2" y="16" width="8" height="6" rx="1"/><rect x="14" y="16" width="8" height="6" rx="1"/><path d="M12 8v4H6v4m6-4h6v4"/>',
  flow: '<path d="M6 3v18m12-18v18M6 7h12m-3-3 3 3-3 3M18 16H6m3-3-3 3 3 3"/>',
  code: '<path d="m7 6-6 6 6 6m10-12 6 6-6 6M14 3l-4 18"/>',
  folder: '<path d="M3 5h7l2 3h9v12H3Z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  expand: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
};
const icon = (name) =>
  '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">' + icons[name] + "</svg>";
const graphTitles = edition?.graphTitles ?? [
  learner ? "LLM Runtime 内部协作与依赖" : "整体架构与依赖方向",
  "数据库 ER · Agent / main",
  "UML · 组件与责任边界",
  "UML · 完整契约关系",
  "执行状态与交付阶段",
  "完整推理请求时序",
];
const graphCaptions = edition?.graphCaptions ?? [
  "管理与执行路径分离；模型调用统一进入 Runtime。",
  learner
    ? "字段附中文说明；可在图内滚动或放大阅读。三张表由 Agent 管理，身份标识使用逻辑引用。"
    : "三张业务表归 Agent 所有；跨应用标识使用逻辑引用。",
  "固定两种适配器；Runtime 与应用服务分别拥有执行和持久化职责。",
  "请求、策略、输出、取消结果与续接载荷的完整类型关系。",
  "completed 在协议验证之后形成，final 在记录收尾之后交付。",
  "从身份校验开始，经过快照、出站、流式输出、记录与取消收尾。",
];
const diagramCodes = [...source.matchAll(/^```mermaid\n([\s\S]*?)^```/gm)].map((match) => match[1]);
if (diagramCodes.length !== graphTitles.length || graphCaptions.length !== graphTitles.length)
  throw new Error("Diagram count does not match edition metadata");
const layeredSvg =
  learner && !edition ? await fs.readFile(path.join(here, "layered-architecture.svg"), "utf8") : "";

const server = http.createServer(async (req, res) => {
  const name = decodeURIComponent(new URL(req.url, "http://127.0.0.1").pathname);
  if (name === "/") {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.end('<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>');
    return;
  }
  const file = path.resolve(here, "." + name);
  if (!file.startsWith(here + path.sep)) {
    res.writeHead(403).end();
    return;
  }
  try {
    res.setHeader(
      "Content-Type",
      file.endsWith(".js") || file.endsWith(".mjs") ? "text/javascript" : "text/plain",
    );
    res.end(await fs.readFile(file));
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
const diagrams = [];
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto("http://127.0.0.1:" + server.address().port);
  await page.evaluate(async () => {
    const { default: mermaid } = await import("/node_modules/mermaid/dist/mermaid.esm.min.mjs");
    window.mermaid = mermaid;
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
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
        background: "#ffffff",
        mainBkg: "#eef4e7",
        clusterBkg: "#f6f8f1",
        clusterBorder: "#b4c6aa",
        edgeLabelBackground: "#ffffff",
        fontSize: "15px",
        actorBkg: "#e8f1df",
        actorBorder: "#8ba67c",
        actorTextColor: "#20372a",
        signalColor: "#56704d",
        noteBkgColor: "#f6f3df",
        noteBorderColor: "#c9c09c",
      },
      flowchart: { htmlLabels: false, curve: "linear", padding: 16 },
      class: { htmlLabels: false },
      sequence: { useMaxWidth: false, actorMargin: 38, messageMargin: 34, wrap: true },
      er: { layoutDirection: "LR", useMaxWidth: false },
    });
  });
  for (let index = 0; index < diagramCodes.length; index++) {
    const svg = await page.evaluate(
      async ({ code, index, title, learner, namespaceIds }) => {
        const { svg } = await window.mermaid.render("compass-diagram-" + index, code);
        document.body.innerHTML = svg;
        const node = document.querySelector("svg");
        node.setAttribute("role", "img");
        node.setAttribute("aria-label", title);
        node.setAttribute("xmlns", "http://www.w3.org/2000/svg");
        if (namespaceIds) {
          const elements = [node, ...node.querySelectorAll("*")];
          const ids = new Map(
            elements
              .filter((element) => element.id)
              .map((element) => [element.id, `figure-${index}-${element.id}`]),
          );
          for (const element of elements) {
            for (const attribute of [...element.attributes]) {
              let value = attribute.value.replace(/url\(#([^)]+)\)/g, (match, id) =>
                ids.has(id) ? `url(#${ids.get(id)})` : match,
              );
              if (attribute.name === "id") value = ids.get(value);
              else if (attribute.name === "href" || attribute.name === "xlink:href") {
                if (value.startsWith("#") && ids.has(value.slice(1)))
                  value = "#" + ids.get(value.slice(1));
              } else if (
                attribute.name === "aria-labelledby" ||
                attribute.name === "aria-describedby"
              ) {
                value = value
                  .split(/\s+/)
                  .map((id) => ids.get(id) ?? id)
                  .join(" ");
              }
              element.setAttribute(attribute.name, value);
            }
            if (element.tagName.toLowerCase() === "style")
              element.textContent = element.textContent.replace(
                /#([A-Za-z_][\w:-]*)/g,
                (match, id) => (ids.has(id) ? "#" + ids.get(id) : match),
              );
          }
        }
        if (learner && code.startsWith("erDiagram")) {
          for (const background of node.querySelectorAll(".edgeLabel .label rect.background"))
            background.style.fill = "#f7f8ee";
          for (const text of node.querySelectorAll(
            ".edgeLabel .label text, .edgeLabel .label tspan",
          ))
            text.style.fill = "#20372a";
        }
        // Mermaid can emit empty declarations; normalize before HTML formatting.
        for (const element of node.querySelectorAll("[style]")) {
          const css = element.style.cssText;
          if (css) element.setAttribute("style", css);
          else element.removeAttribute("style");
        }
        return node.outerHTML;
      },
      {
        code: diagramCodes[index],
        index,
        title: graphTitles[index],
        learner,
        namespaceIds: !!edition,
      },
    );
    diagrams.push(svg);
  }
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}

const md = new MarkdownIt({ html: false, linkify: false, typographer: false });
if (learner) {
  const images = new Map();
  for (const match of source.matchAll(/!\[[^\]]*\]\(([^)]+)\)/g)) {
    const file = path.resolve(here, match[1]);
    if (
      !file.startsWith(path.resolve(design, "../spec/visuals") + path.sep) ||
      !file.endsWith(".png")
    )
      throw new Error("Unexpected screenshot source: " + match[1]);
    images.set(match[1], "data:image/png;base64," + (await fs.readFile(file)).toString("base64"));
  }
  md.renderer.rules.image = (tokens, index) => {
    const token = tokens[index];
    const data = images.get(token.attrGet("src"));
    if (!data) throw new Error("Screenshot was not embedded");
    const alt = escape(token.content);
    return `<span class="product-shot"><button type="button" data-screenshot aria-label="放大截图：${alt}"><img src="${data}" alt="${alt}" width="1440" height="1110" loading="lazy" /></button><span class="product-caption">${alt}</span></span>`;
  };
  md.renderer.rules.paragraph_open = (tokens, index, options, env, self) => {
    if (tokens[index + 1]?.children?.some((token) => token.type === "image"))
      tokens[index].attrSet("class", "product-gallery");
    return self.renderToken(tokens, index, options);
  };
}
const searchIndex = [];
const headings = [];
let headingCount = 0,
  graphCount = 0,
  codeCount = 0;
function highlightedLines(code, language) {
  const html = hljs.getLanguage(language)
    ? hljs.highlight(code, { language, ignoreIllegals: true }).value
    : escape(code);
  const stack = [],
    lines = [];
  let line = "";
  for (const token of html.split(/(<span\b[^>]*>|<\/span>|\n)/)) {
    if (token === "\n") {
      lines.push(line + "</span>".repeat(stack.length));
      line = stack.join("");
    } else {
      line += token;
      if (token.startsWith("<span ")) stack.push(token);
      else if (token === "</span>") stack.pop();
    }
  }
  if (line) lines.push(line + "</span>".repeat(stack.length));
  return lines;
}
function codeBlock(code, language, label) {
  const id = "code-" + codeCount++;
  const raw = code.trimEnd();
  const sourceLines = raw.split("\n");
  const highlighted = highlightedLines(raw, language);
  const types = [];
  for (let index = 0; index < sourceLines.length; index++) {
    const match =
      language === "python" && sourceLines[index].match(/^(?:class|type)\s+([A-Za-z_]\w*)/);
    if (match) {
      const anchor = id + "-line-" + (index + 1);
      types.push('<a href="#' + anchor + '">' + escape(match[1]) + "</a>");
      searchIndex.push({
        id: anchor,
        title: match[1],
        text: label + " · " + sourceLines.slice(index, index + 14).join(" "),
      });
    }
  }
  return (
    (types.length
      ? '<nav class="type-index" aria-label="类型快速跳转">' + types.join("") + "</nav>"
      : "") +
    '<div class="code-block" id="' +
    id +
    '"><div class="code-bar"><span>' +
    escape(label) +
    " / " +
    sourceLines.length +
    ' LINES</span><div class="code-tools">' +
    (sourceLines.length > 22
      ? '<button type="button" data-expand aria-expanded="false" aria-controls="' +
        id +
        '-pre">展开代码</button>'
      : "") +
    '<button type="button" data-copy>复制代码</button></div></div><pre id="' +
    id +
    '-pre" tabindex="0" aria-label="' +
    escape(label) +
    '"><code>' +
    highlighted
      .map(
        (line, index) =>
          '<span class="code-line" id="' +
          id +
          "-line-" +
          (index + 1) +
          '"><span class="line-num" aria-hidden="true">' +
          (index + 1) +
          '</span><span class="line-content">' +
          line +
          "</span></span>",
      )
      .join("") +
    "</code></pre></div>"
  );
}
md.renderer.rules.fence = (tokens, index) => {
  const token = tokens[index];
  const language = token.info.trim();
  if (language === "architecture-svg" && learner) {
    if (token.content.trim() !== "layered-architecture") throw new Error("Unknown SVG diagram");
    searchIndex.push({
      id: "system-overview",
      title: "分层架构与应用协同",
      text: "展示层 Home Web BFF Agent LLM Runtime Memory Worker 数据库 模型服务 已接入 规划",
    });
    return `<figure class="diagram diagram--overview" id="system-overview" data-title="分层架构与应用协同"><div class="figure-bar"><span class="figure-label"><small>FIG. 01</small>分层架构与应用协同</span><button class="plain-button" type="button" data-diagram aria-label="放大分层架构与应用协同">${icon("expand")}放大图表</button></div><div class="diagram-stage">${layeredSvg}</div><figcaption><span>从展示层到应用和资源层；实线表示已接入关系，虚线表示规划中的业务协同。</span><span>SVG · 分层总览</span></figcaption></figure>`;
  }
  if (language === "mermaid") {
    const graph = graphCount++;
    return (
      '<figure class="diagram' +
      (learner && !edition && graph === 1 ? " diagram--er" : "") +
      '" id="figure-' +
      graph +
      '" data-title="' +
      escape(graphTitles[graph]) +
      '"><div class="figure-bar"><span class="figure-label"><small>FIG. 0' +
      (graph + (learner && !edition ? 2 : 1)) +
      "</small>" +
      escape(graphTitles[graph]) +
      '</span><button class="plain-button" type="button" data-diagram aria-label="放大' +
      escape(graphTitles[graph]) +
      '">' +
      icon("expand") +
      '放大图表</button></div><div class="diagram-stage">' +
      diagrams[graph] +
      "</div><figcaption><span>" +
      graphCaptions[graph] +
      '</span><span>MERMAID → SVG</span></figcaption><details class="source-details"><summary>查看 Mermaid 源码</summary>' +
      codeBlock(token.content, "text", "MERMAID SOURCE") +
      "</details></figure>"
    );
  }
  return codeBlock(
    token.content,
    language,
    language === "python"
      ? "PYTHON / TYPE CONTRACT"
      : language === "text"
        ? "TEXT / DESIGN"
        : language.toUpperCase(),
  );
};
md.renderer.rules.table_open = () =>
  '<div class="table-wrap" tabindex="0" role="region" aria-label="设计表格"><table>';
md.renderer.rules.table_close = () => "</table></div>";
md.renderer.rules.link_open = (tokens, index, options, env, self) => {
  const href = tokens[index].attrGet("href");
  if (href && !/^(?:[a-z]+:|#|\/)/i.test(href)) {
    const [file, anchor] = href.split("#");
    tokens[index].attrSet(
      "href",
      path.relative(design, path.resolve(path.dirname(env.file), file)) +
        (anchor ? "#" + anchor : ""),
    );
  }
  return self.renderToken(tokens, index, options);
};
function render(text, file, includeNav = true) {
  const env = { file };
  const tokens = md.parse(text, env);
  for (let index = 0; index < tokens.length; index++) {
    if (tokens[index].type !== "heading_open") continue;
    const title = tokens[index + 1].content;
    const level = Number(tokens[index].tag.slice(1));
    const id = "section-" + headingCount++;
    tokens[index].attrSet("id", id);
    const body = [];
    for (let next = index + 3; next < tokens.length && tokens[next].type !== "heading_open"; next++)
      if (tokens[next].content) body.push(tokens[next].content);
    searchIndex.push({ id, title, text: body.join(" ").replace(/\s+/g, " ") });
    if (includeNav) headings.push({ id, title, level });
  }
  return md.renderer.render(tokens, md.options, env);
}
const article = render(
  learner ? source : source.slice(source.indexOf("## 需求依据与验收")),
  sourceFile,
);
const implementationHtml = learner ? "" : render(current.trim(), implementationFile, false);
if (!learner)
  searchIndex.push({
    id: "acceptance",
    title: "实施结果与验收",
    text: current.replace(/\s+/g, " "),
  });
const destination = (prefix) => {
  const heading = headings.find((item) => item.title.startsWith(prefix));
  if (!heading) throw new Error("Missing heading: " + prefix);
  return "#" + heading.id;
};
const navHeadings = headings.filter(
  (item) =>
    item.level === 2 || item.level === 3 || /^2\.1\.[123]|^2\.3\.7|^2\.4\.[1234]/.test(item.title),
);
const toc =
  navHeadings
    .map(
      (item) =>
        '<a href="#' +
        item.id +
        '"' +
        (item.level > 2 ? ' class="sub"' : "") +
        ">" +
        escape(
          item.title
            .replace("2.4 供评审的代码设计（for review check）", "2.4 代码与类型契约")
            .replace("2.1.3 数据流设计：完整推理请求时序", "2.1.3 完整推理时序")
            .replace("2.4.1 公开契约完整定义", "2.4.1 公开契约")
            .replace("2.4.2 Runtime 内部完整类型定义", "2.4.2 Runtime 类型"),
        ) +
        "</a>",
    )
    .join("") + (learner ? "" : '<a href="#acceptance">实施结果与验收</a>');
const values = {
  styles:
    (await fs.readFile(path.join(here, "styles.css"), "utf8")) +
    (learner ? "\n" + (await fs.readFile(path.join(here, "learner.css"), "utf8")) : ""),
  interactions:
    (await fs.readFile(path.join(here, "interactions.js"), "utf8")) +
    (learner ? "\n" + (await fs.readFile(path.join(here, "learner-interactions.js"), "utf8")) : ""),
  article,
  implementation: implementationHtml,
  toc,
  architecture: destination("1.2"),
  er: destination("2.1.1"),
  uml: destination("2.1.2"),
  sequence: destination("2.1.3"),
  types: destination("2.4.1"),
  directory: destination(edition?.directoryHeading ?? "2.3.7"),
  requirements: learner ? destination(edition?.overviewHeading ?? "需求简述") : "#document",
  searchIndex: JSON.stringify(searchIndex).replaceAll("<", "\\u003c"),
  mermaidVersion: "11.12.0",
  highlightVersion: "11.11.1",
};
let template = await fs.readFile(path.join(here, "page.html"), "utf8");
if (learner) template = createLearnerTemplate(template);
if (edition) template = edition.createTemplate(template);
template = template.replace(/\{\{([\w-]+)\}\}/g, (_, key) => {
  if (key.startsWith("icon-")) return icon(key.slice(5));
  if (!(key in values)) throw new Error("Unknown placeholder: " + key);
  return values[key];
});
template = template.replace("/* DOCUMENT_INTERACTIONS */", () => values.interactions);
template = await prettier.format(template, {
  ...(await prettier.resolveConfig(output)),
  filepath: output,
});
await fs.writeFile(output, template);
console.log(
  JSON.stringify({
    output: path.relative(root, output),
    diagrams: graphCount + (learner && !edition ? 1 : 0),
    mermaidDiagrams: graphCount,
    codeBlocks: codeCount,
    headings: headings.length,
    searchEntries: searchIndex.length,
    bytes: Buffer.byteLength(template),
  }),
);
