import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import MarkdownIt from "markdown-it";
import hljs from "highlight.js";
import { renderDiagrams } from "./diagrams.mjs";

const assets = fileURLToPath(new URL("../assets/", import.meta.url));
const escape = (value = "") =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
const json = (value) => JSON.stringify(value).replaceAll("<", "\\u003c");

function linesWithHighlight(code, language) {
  const markup = hljs.getLanguage(language)
    ? hljs.highlight(code, { language, ignoreIllegals: true }).value
    : escape(code);
  const stack = [],
    lines = [];
  let line = "";
  for (const token of markup.split(/(<span\b[^>]*>|<\/span>|\n)/)) {
    if (token === "\n") {
      lines.push(line + "</span>".repeat(stack.length));
      line = stack.join("");
    } else {
      line += token;
      if (token.startsWith("<span ")) stack.push(token);
      else if (token === "</span>") stack.pop();
    }
  }
  lines.push(line + "</span>".repeat(stack.length));
  return lines;
}

export async function renderReview(configFile, options = {}) {
  const configPath = await fs.realpath(configFile);
  const config = JSON.parse(await fs.readFile(configPath, "utf8"));
  for (const name of ["title", "source", "guide", "output"])
    if (typeof config[name] !== "string" || !config[name].trim())
      throw new Error(`Missing config: ${name}`);
  const resolve = (name) => path.resolve(path.dirname(configPath), name);
  const output = options.output ? path.resolve(options.output) : resolve(config.output);
  if (path.extname(output).toLowerCase() !== ".html")
    throw new Error("Output must be an HTML file");
  const sourceFile = await fs.realpath(resolve(config.source));
  const guideFile = await fs.realpath(resolve(config.guide));
  await fs.readFile(sourceFile, "utf8");
  const inputs = new Set([configPath, sourceFile, guideFile]);
  const md = new MarkdownIt({ html: false, linkify: false, typographer: false });
  let guide = await fs.readFile(guideFile, "utf8");
  for (const match of [...guide.matchAll(/^\{\{code:([\w-]+)\}\}$/gm)]) {
    const spec = config.code?.[match[1]];
    if (!spec?.file || !spec.contains) throw new Error(`Missing code selector: ${match[1]}`);
    const filename = await fs.realpath(resolve(spec.file));
    inputs.add(filename);
    const blocks = md
      .parse(await fs.readFile(filename, "utf8"), {})
      .filter(
        (token) =>
          token.type === "fence" &&
          token.content.includes(spec.contains) &&
          (!spec.language || token.info.trim().split(/\s+/)[0] === spec.language),
      );
    if (blocks.length !== 1)
      throw new Error(`Code selector ${match[1]} matched ${blocks.length} blocks; expected one`);
    const token = blocks[0];
    const fence = "`".repeat(
      Math.max(3, ...[...token.content.matchAll(/`+/g)].map((item) => item[0].length + 1)),
    );
    guide = guide.replace(match[0], () => `${fence}${token.info}\n${token.content}${fence}`);
  }
  if (/\{\{code:/.test(guide))
    throw new Error("Unresolved code inclusion; put each reference on its own line");
  const tokens = md.parse(guide, {});
  const headings = [],
    graphs = [],
    search = [],
    codes = [];
  let currentTitle = config.title;
  for (const [index, token] of tokens.entries()) {
    if (token.type === "heading_open") {
      const title = tokens[index + 1].content;
      const id = `section-${headings.length}`;
      token.attrSet("id", id);
      headings.push({ id, title, level: Number(token.tag.slice(1)) });
      const body = [];
      for (
        let next = index + 2;
        next < tokens.length && tokens[next].type !== "heading_open";
        next++
      )
        if (tokens[next].content) body.push(tokens[next].content);
      search.push({ id, title, text: body.join(" ") });
      currentTitle = title;
    }
    if (token.type === "fence") {
      const [language, ...label] = token.info.trim().split(/\s+/);
      token.meta = { language, title: label.join(" ") || currentTitle };
      if (["mermaid", "diagram-svg"].includes(language)) {
        token.meta.graph = graphs.length;
        graphs.push({ kind: language, title: token.meta.title, code: token.content });
      }
    }
    for (const child of token.children ?? []) {
      if (child.type === "image")
        throw new Error("Use Mermaid or diagram-svg for self-contained graphics");
      if (child.type === "link_open") {
        const href = child.attrGet("href");
        if (!href?.startsWith("#"))
          throw new Error(
            "Keep source links in the maintenance README; reading links must be in-page anchors",
          );
      }
    }
  }
  if (!headings.length) throw new Error("The reading guide needs section headings");
  if (config.hero) {
    const heroPath = await fs.realpath(resolve(config.hero));
    inputs.add(heroPath);
    graphs.push({
      kind: "diagram-svg",
      title: config.title,
      code: await fs.readFile(heroPath, "utf8"),
    });
  }
  const existingOutput = await fs.realpath(output).catch(() => output);
  if (inputs.has(existingOutput)) throw new Error("Output would overwrite a source file");
  const svgs = await renderDiagrams(graphs);
  function codeBlock(code, language, label) {
    const index = codes.length;
    codes.push(code);
    const id = `code-${index}`;
    const lines = linesWithHighlight(code.replace(/\n$/, ""), language);
    search.push({ id, title: label, text: code });
    return `<div class="code-block" id="${id}"><div class="code-bar"><span>${escape(label)} · ${escape(language || "text")} / ${lines.length} LINES</span><div class="code-tools">${lines.length > 22 ? `<button type="button" data-expand aria-expanded="false" aria-controls="${id}-pre">展开代码</button>` : ""}<button type="button" data-copy="${index}">复制代码</button></div></div><pre id="${id}-pre" tabindex="0"><code>${lines.map((line, number) => `<span class="code-line"><span class="line-num" aria-hidden="true">${number + 1}</span><span class="line-content">${line}</span></span>`).join("")}</code></pre></div>`;
  }
  function figure(index, title, source = true) {
    const graph = graphs[index];
    const label = source ? `FIG. ${String(index + 1).padStart(2, "0")}` : "OVERVIEW";
    return `<figure class="diagram" id="figure-${index}" data-title="${escape(title)}"><div class="figure-bar"><span class="figure-label"><small>${label}</small>${escape(title)}</span><button class="plain-button" type="button" data-diagram>放大图表 ↗</button></div><div class="diagram-stage">${svgs[index]}</div>${source ? `<details class="source-details"><summary>查看 ${graph.kind === "mermaid" ? "Mermaid" : "SVG"} 源码</summary>${codeBlock(graph.code, graph.kind === "mermaid" ? "text" : "xml", title + " · 图表源")}</details>` : ""}</figure>`;
  }
  md.renderer.rules.fence = (list, index) => {
    const token = list[index];
    return token.meta.graph === undefined
      ? codeBlock(token.content, token.meta.language, token.meta.title)
      : figure(token.meta.graph, token.meta.title);
  };
  md.renderer.rules.table_open = () =>
    '<div class="table-wrap" tabindex="0" role="region" aria-label="设计表格"><table>';
  md.renderer.rules.table_close = () => "</table></div>";
  const article = md.renderer.render(tokens, md.options, {});
  const navigation = headings
    .filter((item) => item.level === 2)
    .slice(0, 4)
    .map((item) => `<a class="desktop-link" href="#${item.id}">${escape(item.title)}</a>`)
    .join("");
  const [shell, styles, interactions] = await Promise.all(
    ["page.html", "reading.css", "interactions.js"].map((name) =>
      fs.readFile(path.join(assets, name), "utf8"),
    ),
  );
  const values = {
    title: escape(config.title),
    subtitle: escape(config.subtitle ?? ""),
    brand: escape(config.brand ?? "技术方案"),
    label: escape(config.label ?? "人工阅读版 · 设计示意"),
    topics: (config.topics ?? []).map((topic) => `<span>${escape(topic)}</span>`).join(""),
    hero: config.hero ? figure(graphs.length - 1, config.title, false) : "",
    heroClass: config.hero ? "" : "hero-grid--solo",
    metrics: (config.metrics ?? []).length
      ? `<div class="metrics" aria-label="设计目标">${config.metrics.map((item) => `<div class="metric"><strong>${escape(item.value)}</strong><small>${escape(item.label)}</small><p>${escape(item.detail)}</p></div>`).join("")}</div>`
      : "",
    start: `#${headings[0].id}`,
    navigation,
    article,
    styles,
    payload: json({ search, codes }),
    interactions,
  };
  const html = shell.replace(/\{\{([A-Za-z]+)\}\}/g, (_, name) => {
    if (!(name in values)) throw new Error(`Unknown template field: ${name}`);
    return values[name];
  });
  await fs.mkdir(path.dirname(output), { recursive: true });
  const temporary = `${output}.${process.pid}.tmp`;
  try {
    await fs.writeFile(temporary, html);
    await fs.rename(temporary, output);
  } finally {
    await fs.rm(temporary, { force: true });
  }
  return {
    output,
    diagrams: graphs.length,
    codeBlocks: codes.length,
    headings: headings.length,
    bytes: Buffer.byteLength(html),
  };
}

if (
  process.argv[1] &&
  fileURLToPath(import.meta.url) === (await fs.realpath(process.argv[1]).catch(() => ""))
) {
  try {
    const { values, positionals } = parseArgs({
      options: { output: { type: "string" } },
      allowPositionals: true,
    });
    if (positionals.length !== 1)
      throw new Error("Usage: node render.mjs <edition.json> [--output <file.html>]");
    console.log(JSON.stringify(await renderReview(positionals[0], values)));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
