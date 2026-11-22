import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { format, resolveConfig } from "prettier";
import { renderReview } from "../../../../.agents/skills/my-design-to-html/scripts/render.mjs";
import { applyCourseReader } from "../../../../scripts/course-design/reader.mjs";
import { contextReading } from "./context-reading.mjs";

const directory = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(directory, "../../../..");
const read = (filename) => readFile(filename, "utf8");
const config = JSON.parse(await read(path.join(directory, "edition.json")));
const output = path.resolve(directory, config.output);
const codeBlock = (label, body) => ["```" + label, body.trimEnd(), "```"].join("\n");

const prompts = path.join(root, "apps/agent/src/compass_agent/prompts");
const [guide, design, policies, toolGuidance, executionRules, css, cover, tabScript] =
  await Promise.all([
    read(path.join(directory, config.guide)),
    read(path.resolve(directory, config.source)),
    read(path.join(prompts, "shared/policies.json")).then(JSON.parse),
    read(path.join(prompts, "shared/tool-guidance.json")).then(JSON.parse),
    read(path.join(prompts, "shared/execution.json")).then(JSON.parse),
    read(path.join(directory, "design-reading.css")),
    readFile(path.resolve(directory, config.artwork)),
    read(path.join(directory, "reading-tabs.js")),
  ]);

// Discover the registered semantic ToolSpecs and read the same factory declarations as PromptOps.
const inspectCatalog = [
  "import json",
  "from compass_agent.tools.catalog import TOOLS",
  "from compass_agent.context_runtime.profiles import SCENE_FILES, SCOPED_SCENE_FILES",
  "print(json.dumps({'capabilities': {s.name: s.capability for s in TOOLS",
  "    if s.capability.startswith('semantic_') or s.name == 'database_query'},",
  "    'scenes': {scene: file for (_, scene), file in SCENE_FILES.items()}, 'finance': SCOPED_SCENE_FILES[('business_report','SC-29','finance')]}))",
].join("\n");
const { capabilities, scenes, finance } = JSON.parse(
  execFileSync(
    "uv",
    ["run", "--frozen", "--no-sync", "--package", "compass-agent", "python", "-c", inspectCatalog],
    { cwd: root, encoding: "utf8" },
  ),
);
const names = Object.keys(capabilities);
const context = await contextReading({
  prompts,
  seed: path.join(root, "apps/web/db/main/seeds/retail-v1"),
  scenes,
  finance,
  policies,
  toolGuidance,
  executionRules,
});
const declarations = new Map(
  await Promise.all(
    names.map(async (name) => {
      const tool = JSON.parse(await read(path.join(prompts, "tools", name + ".json")));
      if (name !== tool.function.name) throw new Error("工具名称不一致：" + name);
      return [name, tool];
    }),
  ),
);

function diagramUnder(heading) {
  const index = design.split("\n" + heading + "\n");
  if (index.length !== 2) throw new Error("核对原稿章节：" + heading);
  const section = index[1].split(/\n#{2,3} /)[0];
  const diagrams = [...section.matchAll(/^```mermaid\n([\s\S]*?)\n```$/gm)];
  if (diagrams.length !== 1) throw new Error("核对原稿图表：" + heading);
  return diagrams[0][1];
}
function replaceOnce(value, before, after) {
  if (value.split(before).length !== 2) throw new Error("核对原文定位：" + before);
  return value.replace(before, () => after);
}
const overview = await read(path.join(directory, "agent-semantic-architecture.svg"));
const choice = diagramUnder("### 4.2 Chat 中选择口径并恢复原 Run");

function passage(rule, first, last) {
  const value = policies.rules[rule];
  if (!first) return value.trim();
  const start = value.indexOf(first),
    end = value.indexOf(last, start);
  if (start < 0 || value.indexOf(first, start + 1) >= 0 || end < 0)
    throw new Error("核对 Prompt 原文：" + rule + " / " + first);
  return value.slice(start, end + last.length);
}
const excerpts = {
  save: passage("semantics", "查询通常只需交付答案。", "保存、审批和发布各自遵循授权流程。"),
  governance: passage("semantic-governance"),
};
const inline = (value) => "`" + value + "`";
const quote = (value) =>
  value
    .trim()
    .split("\n")
    .map((line) => "> " + line.replace(/[\\`*_<>\[\]]/g, "\\$&"))
    .join("\n");

function parameterList(schema) {
  if (schema.properties)
    return Object.keys(schema.properties)
      .map((key) => inline(key + ((schema.required ?? []).includes(key) ? "" : "?")))
      .join("、");
  return Object.values(schema.$defs ?? {})
    .filter((op) => op.properties?.op?.const)
    .map((op) => inline(op.properties.op.const))
    .join("、");
}
const uses = new Map();
function toolEntry(_, name, when) {
  const tool = declarations.get(name);
  if (!tool || uses.has(name)) throw new Error("核对工具条目：" + name);
  uses.set(name, when);
  const { description, parameters } = tool.function;
  const args = parameterList(parameters);
  if (!args || !description) throw new Error("工具参数或描述为空：" + name);
  return [
    "[[tool " + inline(name) + " | " + when + "]]",
    "#### tools/" + name + ".json · function.description",
    quote(description),
    "注册能力　" + inline(capabilities[name]),
    (parameters.properties ? "参数（? 可省略）" : "操作值") + "　" + args,
    ...(tool.examples?.length
      ? [codeBlock("json 调用示例", JSON.stringify(tool.examples, null, 2))]
      : []),
    "[[/fold]]",
  ].join("\n\n");
}
const routedScenes = JSON.parse(
  await read(path.join(prompts, "support/routing/scenes.json")),
).scenes;
const sceneTable = [
  "| 场景 | 本场景的工作方法 |",
  "| --- | --- |",
  ...Object.values(routedScenes).map(
    (row) => `| ${row.scene_id} · ${row.name} | ${row.sop.replaceAll("|", "\\|")} |`,
  ),
].join("\n");

const prepared = guide
  .replace("{{scene-sops}}", () => sceneTable)
  .replace("{{overview}}", () => codeBlock("diagram-svg Agent 语义应用总体架构", overview))
  .replace("{{choice}}", () => codeBlock("mermaid 选择口径并恢复原 Run", choice))
  .replace(/\{\{tool:(\w+)\|([^\n}]+)\}\}/g, toolEntry)
  .replace(/\{\{quote:(\w+)\}\}/g, (_, id) => quote(excerpts[id]));
if (uses.size !== declarations.size || /\{\{/.test(prepared))
  throw new Error("阅读稿与注册工具或 Prompt 不一致。");

function updateSearch(data) {
  for (const entry of data.search) {
    if (!entry.id.startsWith("section-")) continue;
    const toolName = /^tools\/(\w+)\.json/.exec(entry.title)?.[1];
    const cleanText = entry.text
      .replace(/\[\[(?:tool |fold |\/fold)[\s\S]*?\]\]/g, "")
      .replace(/\[\[(?:context-stacks|scene-prompts|query-prompts)\]\]/g, "")
      .replace(/\\([\\`*_<>\[\]])/g, "$1");
    entry.text = [uses.get(toolName), cleanText.trim()].filter(Boolean).join(" ");
  }
  for (const { id, label, file, key, raw } of context.sources) {
    data.search.push({ id, title: label + " · " + (key ?? file), text: file + " " + raw });
    data.codes.push(raw);
  }
  data.search.push(...context.search);
}

async function courseLayout(html) {
  const payload = /(<script type="application\/json" id="document-data">)([\s\S]*?)(<\/script>)/;
  const data = JSON.parse(html.match(payload)[2]);
  const components = context.render(data.codes.length);
  for (const [name, markup] of Object.entries(components))
    html = replaceOnce(html, "<p>[[" + name + "]]</p>", markup);
  updateSearch(data);
  // Preserve links shared from the course reader before the chapter order changed.
  const bookmarks = new Map([
    ["总体架构", ["architecture"]],
    ["上下文工程", ["registry", "default-context"]],
    ["工具契约参考", ["tools"]],
    ["取数与开放查询的完整 Prompt", ["query-prompts"]],
    ["消歧与原任务恢复", ["choice"]],
    ["自然语言创建与修订指标", ["governance"]],
    ["服务间 API", ["api"]],
    ["代码变更", ["core-code", "file-changes"]],
    ...names.map((name) => ["tools/" + name + ".json · function.description", ["tool-" + name]]),
  ]);
  const width = cover.readUInt32BE(16),
    height = cover.readUInt32BE(20);
  // Keep the offline cover beside the shared course heading.
  const page = await applyCourseReader(
    html.replace(/<button class="search-trigger"[\s\S]*?<\/button>/, ""),
    config,
  );
  const illustration = `<details class="lesson-cover"><summary>
    <img alt="课程封面，展示指标起草、发布和授权查询" width="${width}" height="${height}" srcset="data:image/png;base64,${cover.toString("base64")} ${width}w" sizes="(max-width: 760px) 90vw, 275px">
    <span class="cover-open">展开课程封面</span><span class="cover-close">收起课程封面</span></summary></details>`;
  return page
    .replace('<div class="lesson-controls">', () => illustration + '<div class="lesson-controls">')
    .replace(
      payload,
      (_, opening, raw, closing) =>
        "<!-- prettier-ignore -->\n<script>" +
        tabScript +
        "</script>\n" +
        opening +
        JSON.stringify(data).replaceAll("<", "\\u003c") +
        closing,
    )
    .replace(/(<h[234] id="section-\d+">)([^<]+)/g, (heading, open, name) =>
      bookmarks.has(name)
        ? open +
          bookmarks
            .get(name)
            .map((id) => '<span id="' + id + '" aria-hidden="true"></span>')
            .join("") +
          name
        : heading,
    )
    .replace(
      /<p>\[\[tool (<code>\w+<\/code>) \| ([^\n]+?)\]\]<\/p>/g,
      '<details class="lesson-fold tool-rule"><summary>$1<span>$2</span></summary>',
    )
    .replace(
      /<p>\[\[fold ([^\n]+?)\]\]<\/p>/g,
      '<details class="lesson-fold"><summary>$1</summary>',
    )
    .replaceAll("<p>[[/fold]]</p>", "</details>")
    .replace('href="#retail-assets"', 'href="RETAIL_SEMANTIC_ASSETS.html"')
    .replaceAll(
      '<div class="diagram-stage">',
      '<div class="diagram-stage" role="region" tabindex="0" aria-label="设计图，可横向滚动">',
    )
    .replace("</head>", () => "<!-- prettier-ignore -->\n<style>" + css + "</style>\n</head>")
    .replace('<article class="document"', '<!-- prettier-ignore -->\n<article class="document"');
}

const temp = await mkdtemp(path.join(tmpdir(), "compass-semantic-design-"));
const staging = output + "." + process.pid + ".tmp";
try {
  const edition = path.join(temp, "edition.json");
  await Promise.all([
    writeFile(
      edition,
      JSON.stringify({
        ...config,
        guide: "guide.md",
        source: path.resolve(directory, config.source),
        output: "review.html",
      }),
    ),
    writeFile(path.join(temp, "guide.md"), prepared),
  ]);
  const { output: rendered } = await renderReview(edition);
  const finished = await courseLayout(await read(rendered));
  await writeFile(
    staging,
    await format(finished, { ...(await resolveConfig(output)), filepath: output }),
  );
  await rename(staging, output);
  console.log(output);
} finally {
  await rm(staging, { force: true });
  await rm(temp, { recursive: true, force: true });
}
