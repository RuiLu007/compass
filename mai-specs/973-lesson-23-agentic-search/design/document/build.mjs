import { applyCourseReader } from "../../../../scripts/course-design/reader.mjs";
import { buildSupplements } from "./supplements.mjs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { renderReview } from "../../../../.agents/skills/my-design-to-html/scripts/render.mjs";

const here = fileURLToPath(new URL(".", import.meta.url));
const requireSkill = createRequire(
  new URL("../../../../.agents/skills/my-design-to-html/package.json", import.meta.url),
);
const MarkdownIt = requireSkill("markdown-it");
const md = new MarkdownIt({ html: false });
const configFile = path.join(here, "edition.json");
const config = JSON.parse(await fs.readFile(configFile, "utf8"));
const source = await fs.readFile(path.resolve(here, config.source), "utf8");
const lines = source.split("\n");
const tokens = md.parse(source, {});
const esc = (text) => md.utils.escapeHtml(String(text));
const rich = (text) => md.renderInline(text);
const clean = (text) =>
  text
    .replace(/<[^>]+>/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
const json = (value) => JSON.stringify(value).replaceAll("<", "\\u003c");

const headings = tokens.filter((token) => token.type === "heading_open" && token.tag === "h2");
const chapters = headings.map((heading, index) => {
  const start = heading.map[0];
  const title = lines[start].replace(/^##\s+/u, "");
  const end = headings[index + 1]?.map[0] ?? lines.length;
  const body = lines
    .slice(start + 1, end)
    .join("\n")
    .trim();
  return { title, body, tokens: md.parse(body, {}), id: `section-${index}` };
});
const guide = await fs.readFile(path.resolve(here, config.guide), "utf8");
const guideTitles = [...guide.matchAll(/^## (.+)$/gmu)].map((m) => m[1]);
if (
  chapters.length !== 7 ||
  JSON.stringify(guideTitles) !== JSON.stringify(chapters.map((c) => c.title))
) {
  throw new Error("Align the reading layout with all seven numbered Markdown chapters");
}

function table(chapter, firstHeading) {
  const found = [];
  let rows, cells;
  for (const token of chapters[chapter - 1].tokens) {
    if (token.type === "table_open") rows = [];
    if (!rows) continue;
    if (token.type === "tr_open") cells = [];
    if (token.type === "inline" && cells) cells.push(token.content);
    if (token.type === "tr_close") {
      rows.push(cells);
      cells = null;
    }
    if (token.type === "table_close") {
      if (rows[0][0] === firstHeading) found.push(rows);
      rows = null;
    }
  }
  if (found.length !== 1)
    throw new Error(`Expected one table in ` + chapter + " with heading " + firstHeading);
  return { headers: found[0][0], rows: found[0].slice(1) };
}

function fence(chapter, info) {
  const matches = chapters[chapter - 1].tokens.filter(
    (token) => token.type === "fence" && token.info.trim().startsWith(info),
  );
  if (matches.length !== 1) throw new Error("Missing or ambiguous fence " + info);
  return matches[0].content.trimEnd();
}

function referenceTable(data, label) {
  const head = data.headers.map((h) => `<th scope="col">${rich(h)}</th>`).join("");
  const body = data.rows
    .map(
      (row) =>
        `<tr>${row.map((v, i) => (i ? `<td>${rich(v)}</td>` : `<th scope="row">${rich(v)}</th>`)).join("")}</tr>`,
    )
    .join("");
  return `<div class="table-wrap" role="region" tabindex="0" aria-label="${esc(label)}"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function disclosure(label, content, cls = "") {
  return `<details class="read-more ${cls}" ${cls === "goals" ? "open" : ""}><summary>${esc(label)}</summary><div class="disclosure-body">${content}</div></details>`;
}

function switcher(id, label, entries) {
  const radios = entries
    .map(
      ([name], i) =>
        `<label><input type="radio" name="${id}" value="${i}" aria-controls="${id}-panel-${i}" ${i === 0 ? "checked" : ""}><span>${esc(name)}</span></label>`,
    )
    .join("");
  const panels = entries
    .map(
      ([name, body], i) =>
        `<div data-panel="${i}" id="${id}-panel-${i}" class="switch-panel" aria-label="${esc(name)}"><strong class="print-label">${esc(name)}</strong>${body}</div>`,
    )
    .join("");
  return `<div class="switcher" data-switcher><fieldset class="switch-options"><legend class="sr-only">${esc(label)}</legend>${radios}</fieldset>${panels}</div>`;
}

const goals = table(1, "核心问题");
const tools = table(1, "模型工具");
const metrics = table(1, "正式指标");
const stages = table(2, "何时组装");
const layers = table(2, "层");
const assets = table(3, "资产与写入位置");
const checks = table(4, "接入点");
const feedback = JSON.parse(fence(4, "json 进入下一模型帧"));
const sources = table(5, "逻辑来源 → 入口");
const runtimeEntries = table(5, "代码里的实际入口");
const stops = table(5, "停止原因");
const walk = table(6, "新取得的事实");
const steps = table(7, "顺序");
const extraSearch = [];
function searchEntry(id, title, copy) {
  extraSearch.push({ id, title, text: clean(copy) });
  return id;
}

function contractList(data, prefix) {
  return `<div class="contract-list">${data.rows
    .map(([name, change, returns]) => {
      const key = name.replaceAll(String.fromCharCode(96), "");
      const id = searchEntry(prefix + "-" + key.replaceAll(".", "-"), key, change + " " + returns);
      const added = change.startsWith("**新增**");
      const status = added ? "新增" : "复用";
      const purpose = change.replace(/^\*\*新增\*\*[。]?|^复用。/u, "").replace(/^并/u, "");
      return `<details class="contract"><summary id="${esc(id)}"><span class="status ${added ? "added" : ""}">${status}</span>${rich(name)}</summary><div class="contract-info"><p>${rich(purpose)}</p><p>${rich(returns)}</p></div></details>`;
    })
    .join("")}</div>`;
}

function scope() {
  const newlyAdded = (list) =>
    list.rows.filter(([, change]) => change.startsWith("**新增**")).length;
  const added = newlyAdded(tools);
  const plans = tools.rows.filter(([name]) => name === "`todo_write`").length;
  return `<div class="scope-pair"><div><h3>模型工具清单</h3><p class="subline">${tools.rows.length} 个工具（${tools.rows.length - plans} 个检索，${plans} 个计划）· 新增 ${added} 个，复用 ${tools.rows.length - added} 个</p>${contractList(tools, "tool")}</div>
    <div><h3>经营语义层</h3><p class="subline">${metrics.rows.length - newlyAdded(metrics)} 项已有口径，新增 ${newlyAdded(metrics)} 项履约缺货率</p>${contractList(metrics, "metric")}
    <div class="fact-note"><b>0/57 与零尝试</b><p>水位齐备、57 次尝试均未短缺，缺货率为 0%。零尝试返回 <code>NO_FACTS</code> 与空值。</p></div></div></div>
    <aside class="implementation-note"><b>两类查询怎样在同一 Run 共存</b><p>搜索计划中已接受的独立明细事项允许模型查询采购实收。明细结果标记为 <code>open_sql</code>，正式指标仍须由同范围的 <code>query_metric</code> 回执支撑。</p></aside>
    ${disclosure("课堂验收目标", referenceTable(goals, "课堂验收目标"), "goals")}`;
}

function context() {
  const labels = ["识别", "计划 / 首查", "查询 / 分析", "生成", "恢复"];
  if (stages.rows.length !== labels.length) throw new Error("Review the five input stages");
  const entries = stages.rows.map(([name, preload, read], i) => [
    labels[i],
    `<p class="phase-name">${rich(name)}</p><div class="phase-grid"><div><b>预置或保留</b><p>${rich(preload)}</p></div><div><b>按需检索或提交</b><p>${rich(read)}</p></div></div>`,
  ]);
  const layerList = layers.rows
    .map(
      ([name, content, timing, owner]) =>
        `<details class="layer"><summary><span class="layer-id">${esc(name.slice(0, 2))}</span>${esc(name.substring(3))}</summary><p>${rich(content)}</p><p>${rich(timing)}</p><p class="source-owner">${rich(owner)}</p></details>`,
    )
    .join("");
  return `<h3>沿一次 Run 看模型输入</h3>${switcher("stage", "选择运行阶段", entries)}
    <h3>六层上下文</h3><div class="layer-list">${layerList}</div>
    <p class="section-note">完整预置所选场景的方法包。指标目录只给身份、版本和名称等短索引，公式、历史值与原件按需取得。输入紧张时优先保留当前范围、单位、反证与引用。</p>`;
}

function prompts() {
  const base = fence(3, "text systemPrompt").split("\n");
  const skill = fence(3, "text agentic-search").split("\n");
  const skillLines = skill
    .map((line) => {
      const m = line.match(/^(Plan|Execute|Verify)　(.+)$/u);
      return m
        ? `<div class="skill-line"><b>${esc(m[1])}</b><p>${esc(m[2])}</p></div>`
        : `<p class="skill-tail">${esc(line)}</p>`;
    })
    .join("");
  return `<div class="prompt-pair"><div class="prompt-base"><h3>systemPrompt</h3><small>shared/fragments/base.md · 原文摘录</small>${base.map((t) => `<p>${esc(t)}</p>`).join("")}</div>
    <div class="prompt-skill"><h3>agentic-search Skill</h3><small>shared/execution.json · 已登记正文</small>${skillLines}</div></div>
    <div class="message-strip"><div><code>system</code><p>平台、场景与当前阶段的指引</p></div><div><code>tools[]</code><p>当前获准的声明，生成阶段为空</p></div><div><code>messages[]</code><p>用户问题、原生调用、观察与运行反馈</p></div></div>
    <p class="section-note">查询阶段可以选择原生工具调用，或提交 <code>targets / claimProposals / answerBlocks</code> 草稿。服务端核验后签发 <code>claimId/revision</code>，比较简报改用 comparisons 和 notes，表内数值引用已签收的行列。</p>
    ${disclosure("查看 5 处 Prompt 资产与加载时机", referenceTable(assets, "Prompt 资产与装配位置"))}`;
}

function verify() {
  const observation = feedback.newFacts[0],
    gap = feedback.openGaps[0];
  return `<div class="verify-pair"><div><code>verify_batch</code><h3>工具返回之后</h3><p><code>RunToolBindings.dispatch</code> 在记录工具消息前签收有序回执，核对范围、水位和权限，再给主模型新事实、缺口和预算。</p></div>
    <div><code>verify_draft</code><h3>答案发表之前</h3><p><code>RuntimeTurn.execute_turn</code> 的 <code>handle</code> 在问答或报告交付前复核断言、计算与引用。缺口可补就返回 <code>query</code>，由模型选下一步。</p></div></div>
    <aside class="feedback-preview"><h3>一次回到模型的反馈</h3><dl><div><dt>已取得</dt><dd>${esc(observation.locator)} · ${esc(observation.columns.join("、"))}</dd></div><div><dt>已签收</dt><dd>${esc(observation.kind)} · ${observation.rowCount} 行</dd></div><div><dt>仍缺什么</dt><dd>${esc(gap.goal)}</dd></div><div><dt>工具余量</dt><dd>${esc(feedback.remaining.toolCalls)} 次 · 本批示例</dd></div></dl></aside>
    <p class="section-note">草稿此时若写“可以试投”，Runtime 会返回缺少的正式缺货率回执。签发引用前还要复核每个数字的对象、窗口、单位和行或段落位置。交付与回看时重验父来源权限。两次校验都在 Agent 进程内完成，额外模型调用为 0。</p>
    ${disclosure("查看接入位置、输出与回看约束", referenceTable(checks, "Verify 接入点"))}`;
}

function governance() {
  const labels = {
    cancelled: "用户取消",
    sufficient: "决定条件已齐",
    budget_exhausted: "预算不足",
    blocked: "获准路径用尽",
    no_progress: "继续查没有新增",
  };
  const conditions = stops.rows
    .map(([name, when, action]) => {
      const key = name.replaceAll(String.fromCharCode(96), "");
      if (!labels[key]) throw new Error("Name the stop reason " + key);
      return `<details class="stop"><summary>${esc(labels[key])} ${rich(name)}</summary><p>${rich(when)}</p><p>${rich(action)}</p></details>`;
    })
    .join("");
  const sourceMap = sources.rows
    .map(([name], index) => {
      const label = name.split(" → ")[0];
      const entry = index < 2 ? ["4 个语义工具", "database_query"][index] : "文档发现 / 原文读取";
      return `<div><b>${esc(label)}</b><small>${esc(entry)}</small></div>`;
    })
    .join("");
  return `<h3>五类来源从哪里查</h3><div class="source-map">${sourceMap}</div>
    ${disclosure("查看五类来源的执行边界与回执", referenceTable(sources, "五类来源的执行回执"))}
    <h3>从什么事实判断该停</h3><div class="stops">${conditions}</div>
    <div class="budget-note"><b>预算在哪儿扣</b><p>并行派发逐项占额，给最终生成保留一步与输出余量。去重、拒绝和真实派发分开记录。接管后从持久记录恢复已占额度。</p></div>
    ${disclosure("查看现有代码入口和本课增量", referenceTable(runtimeEntries, "现有代码入口和本课增量"))}`;
}

const money = (value) => value.toLocaleString("en-US");
const number = (value) => Number(value.replaceAll(",", ""));
function matched(text, regex) {
  const m = text.match(regex);
  if (!m) throw new Error("Review the figures in the source case before rendering");
  return m;
}
const [business, decision, variant] = walk.rows.at(-1);
const balance = number(matched(business, /余额\s*([\d,]+)\s*元/u)[1]);
const fraction = number(matched(business, /比例\s*(\d+)%/u)[1]);
const offer = matched(business, /报价\s*([\d,]+)\s*\/\s*([\d,]+)\s*元/u)
  .slice(1)
  .map(number);
const newLow = number(matched(variant, /最低档为\s*([\d,]+)\s*元/u)[1]);
const cap = (balance * fraction) / 100;
const netExample = matched(chapters[5].body, /即可给出 ([\d,.]+) 元并收口/u)[1];
function offerPanel(low) {
  const tiers = [low, offer[1]],
    eligible = tiers.filter((n) => n <= cap);
  const price = eligible.length ? Math.max(...eligible) : 0;
  const max = Math.max(balance, cap, ...tiers);
  const bars = tiers
    .map(
      (value, i) =>
        `<div class="offer-line"><span>${i === 0 ? "最低档" : "较高档"}</span><div class="bar-track"><div class="offer-bar ${value <= cap ? "in-cap" : "over-cap"}" style="width:${(100 * value) / max}%"></div><i style="left:${(100 * cap) / max}%" aria-hidden="true"></i></div><b>${money(value)} 元</b><small>${value <= cap ? "额度内" : "超上限"}</small></div>`,
    )
    .join("");
  return `<div class="offer-grid"><div class="bar-chart" role="group" aria-label="两档报价与内部上限对比"><div class="bar-legend"><span></span>内部上限 ${money(cap)} 元</div>${bars}<p class="formula">额度 = ${money(balance)} 元 × ${fraction}%</p></div><div class="offer-result"><small>签批意见</small><strong>${price ? "建议试投，待签批" : "两档均超限，暂缓"}</strong><p>${price ? "拟用 " + money(price) + " 元" : "拟用 0 元"}，保留 <b>${money(balance - price)} 元</b>。</p></div></div>`;
}
if (
  cap !== number(matched(decision, /上限\s*([\d,]+)\s*元/u)[1]) ||
  balance - Math.max(...offer.filter((n) => n <= cap)) !==
    number(matched(decision, /余款\s*([\d,]+)\s*元/u)[1]) ||
  newLow <= cap ||
  balance !== number(matched(variant, /保留\s*([\d,]+)\s*元/u)[1])
) {
  throw new Error("Offer comparison no longer matches the two decisions in chapter 6");
}

function caseStudy() {
  const names = ["定位待办与规则", "核对实际收货", "检查恢复与归因", "核对额度和报价"];
  if (walk.rows.length !== names.length) throw new Error("Review the case timeline");
  const timeline = walk.rows
    .map(
      ([fact, change, next], i) =>
        `<li><span class="timeline-dot" aria-hidden="true"></span><div><h4>${esc(names[i])}</h4><p>${rich(fact)}</p><dl><div><dt>校验反馈</dt><dd>${rich(change)}</dd></div><div><dt>模型决策</dt><dd>${rich(next)}</dd></div></dl></div></li>`,
    )
    .join("");
  const weekly = chapters[5].body.split("### 普通 Chat 实跑")[1];
  return `<h3>华东区 GMV 为什么下降 12%？</h3>${md
    .render(weekly.slice(weekly.indexOf("\n")))
    .replace("<table>", "<div class='table-wrap' tabindex='0'><table>")
    .replace("</table>", "</table></div>")}<h3>最低报价变了，意见怎样变</h3>${switcher(
    "quote",
    "选择报价版本",
    [
      [`原报价 · ${money(offer[0])} 元起`, offerPanel(offer[0])],
      [`换价 · ${money(newLow)} 元起`, offerPanel(newLow)],
    ],
  )}
    <h3>从周会待办追到签批意见</h3><ol class="timeline">${timeline}</ol>
    <aside class="implementation-note"><b>简单查数的短路径</b><p>只问九月华东小程序扣退款后的成交额时，取得同范围的正式回执即可回答 ${esc(netExample)} 元并收口。</p></aside>`;
}

function delivery() {
  const list = steps.rows
    .map(([name, files, done]) => {
      const [index, title] = name.split(/\s*·\s*/u);
      return `<details class="milestone"><summary><span>${esc(index)}</span><strong>${esc(title)}</strong></summary><dl><dt>改动位置与交付物</dt><dd>${rich(files)}</dd><dt>完成判据</dt><dd>${rich(done)}</dd></dl></details>`;
    })
    .join("");
  return `<div class="milestones">${list}</div><p><a href="VECTOR_SEARCH.html">向量检索与华东 GMV</a> · <a href="CODE_DESIGN.html">代码实现</a> · <a href="EVIDENCE.html">引用校验</a> · <a href="RETAIL_CASE.html">零售数据与验收</a></p><div class="release-note"><b>发布与回切</b><p>先准备资料与语义增量，再发布支持新引用的 Agent/BFF，最后为工作区启用。Agent 复用运行表；Web 迁移 0009_document_vectors 保存向量。回切调整新 Run 的配置，已有 Run 由兼容读端按固定版本恢复，历史读取复核当前权限。</p></div>`;
}

const region = { scope, context, prompts, verify, governance, case: caseStudy, delivery };
const foldNames = {
  layers: "查看上下文分层图",
  decision: "查看两期比较的计算代码",
  feedback: "查看这一批的原始反馈 JSON",
  architecture: "查看主 Agent、Runtime 与数据源的架构图",
};
const chapterLinks = chapters.map((ch) => `<a href="#${ch.id}">${esc(ch.title)}</a>`).join("");

const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "compass-lesson23-"));
try {
  const base = path.join(tmp, "review.html");
  await renderReview(configFile, { output: base });
  const [shell, style, interactions] = await Promise.all([
    fs.readFile(base, "utf8"),
    fs.readFile(path.join(here, "reader.css"), "utf8"),
    fs.readFile(path.join(here, "reader.js"), "utf8"),
  ]);
  const used = new Set();
  let html = shell
    .replace(/<p>\{\{(view|fold):([\w-]+)\}\}<\/p>/gu, (_all, kind, key) => {
      if (kind === "fold") {
        if (!foldNames[key]) throw new Error("Unknown figure disclosure " + key);
        return `<details class="read-more figure-fold"><summary>${esc(foldNames[key])}</summary>`;
      }
      if (!region[key] || used.has(key)) throw new Error("Unexpected view " + key);
      used.add(key);
      return region[key]();
    })
    .replaceAll("<p>{{endfold}}</p>", "</details>");
  if (used.size !== chapters.length) throw new Error("Incomplete chapter layout");
  html = html.replaceAll(
    '<div class="diagram-stage">',
    '<div class="diagram-stage" tabindex="0" role="region" aria-label="设计图，可横向滚动">',
  );

  html = html.replace(
    /<a class="desktop-link"[\s\S]*?(?=<button class="search-trigger")/u,
    () =>
      `<a class="desktop-link" href="#section-1">上下文</a><a class="desktop-link" href="#section-3">Verify</a><a class="desktop-link" href="#section-5">零售案例</a><details class="chapter-nav mobile-nav"><summary>章节目录</summary><div>${chapterLinks}</div></details>`,
  );
  for (const [i, ch] of chapters.entries()) {
    const old = `<h2 id="${ch.id}">${esc(ch.title)}</h2>`;
    if (!html.includes(old)) throw new Error("Missing chapter heading " + ch.title);
    const match = ch.title.match(/^(\d+)\. (.+)$/u);
    html = html.replace(
      old,
      () =>
        `<div class="chapter-heading"><h2 id="${ch.id}"><span class="chapter-num">${match[1]}.</span> ${esc(match[2])}</h2><button type="button" data-original="${i}" aria-label="查看第 ${match[1]} 节主方案原文">对照原文</button></div>`,
    );
  }
  html = html.replace(
    /(<script type="application\/json" id="document-data">)([\s\S]+?)(<\/script>)/u,
    (_m, open, payload, close) => {
      const data = JSON.parse(payload);
      data.search = data.search.filter((x) => !chapters.some((ch) => ch.id === x.id));
      data.search.unshift(
        ...extraSearch,
        ...chapters.map((ch) => ({
          id: ch.id,
          title: ch.title,
          text: ch.tokens
            .filter((t) => t.type === "inline")
            .map((t) => t.content)
            .join(" ")
            .replace(/\s+/gu, " "),
        })),
      );
      return open + json(data) + close;
    },
  );
  const originals = chapters.map((ch) => ({
    title: ch.title,
    html: md.render(ch.body).replace(/<a [^>]*>([\s\S]*?)<\/a>/gu, "$1"),
  }));
  if (/\{\{(?:view|fold|endfold)/u.test(html)) throw new Error("Unresolved reading layout marker");
  const sourceDigest = createHash("sha256").update(source).digest("hex");
  html = html.replace(
    "</head>",
    () =>
      `<meta name="design-source-sha256" content="${sourceDigest}"><!-- prettier-ignore --><style>${style}</style></head>`,
  );
  html = html.replace(
    "</body>",
    () =>
      `<dialog id="original-dialog" aria-labelledby="original-heading"><div class="dialog-header"><h2 id="original-heading">主方案原文</h2><button type="button" class="close-button" aria-label="关闭原文">×</button></div><div class="original-copy"></div></dialog><!-- prettier-ignore --><script type="application/json" id="chapter-originals">${json(originals)}</script><!-- prettier-ignore --><script>${interactions}</script></body>`,
  );
  html = await applyCourseReader(html, {
    lesson: 23,
    title: "Agentic Search",
    subtitle: "主 Agent 怎样从原文和账本查明经营变化",
    cover: {
      file: path.resolve(here, "../../context/agentic-search-architecture.png"),
      alt: "跨源经营诊断的 Agentic Search 架构图",
    },
  });
  const target = path.resolve(here, config.output);
  const staged = target + "." + process.pid + ".tmp";
  try {
    const prettier = createRequire(import.meta.url)("prettier");
    const formatted = await prettier.format(html, {
      ...(await prettier.resolveConfig(target)),
      filepath: target,
    });
    await fs.writeFile(staged, formatted);
    await fs.rename(staged, target);
  } finally {
    await fs.rm(staged, { force: true });
  }
  console.log(
    `Rebuilt ${path.basename(target)} from ${chapters.length} Markdown chapters. Source ${sourceDigest.slice(0, 12)}`,
  );
} finally {
  await fs.rm(tmp, { recursive: true, force: true });
}

await buildSupplements(path.resolve(here, ".."));
