import { mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { format, resolveConfig } from "prettier";
import { applyCourseReader } from "../../../../scripts/course-design/reader.mjs";
import { renderReview } from "../../../../.agents/skills/my-design-to-html/scripts/render.mjs";

import {
  loadDefinitions,
  loadRetailTermRevision,
} from "../../../../apps/web/scripts/retail-semantic-release.mjs";
import { loadSemanticPack } from "../../../../apps/web/scripts/retail-semantic-seed.mjs";

const directory = path.dirname(fileURLToPath(import.meta.url));
const pack = path.resolve(directory, "../../../../apps/web/db/main/seeds/retail-v1");
const read = (file) => readFile(file, "utf8");
const json = async (file) => JSON.parse(await read(file));
const code = (value) => "`" + value + "`";
const codes = (values) => values.map(code).join("、");
const domains = {
  organization: "组织与日历",
  product: "商品",
  sales: "交易",
  fulfillment: "履约与售后",
  finance: "财务",
  marketing: "营销",
  channel: "渠道",
  inventory: "库存",
  customer: "客户",
  experiment: "实验",
  quality: "数据完整度",
};

function table(headers, rows) {
  const row = (cells) => "| " + cells.map((cell) => cell.replaceAll("|", "\\|")).join(" | ") + " |";
  return [row(headers), row(headers.map(() => "---")), ...rows.map(row)].join("\n");
}

function entityCatalog(entities) {
  const groups = Object.groupBy(entities, (entity) => entity.domain);
  const sections = Object.entries(groups).map(([domain, items]) => {
    const parts = ["#### " + (domains[domain] ?? domain)];
    for (const { name, grain, summary, definition: d } of items) {
      const detail = [
        "##### " + name + " · " + code(d.table),
        summary + " 粒度为" + grain + "。主键为 " + codes(d.primary_key) + "。",
        "业务时间：" + d.event_time + "。可见时间：" + d.visible_time + "。",
        table(
          ["源字段", "业务含义"],
          Object.entries(d.fields).map(([field, meaning]) => [
            code(field) + (d.primary_key.includes(field) ? " · 主键" : ""),
            meaning,
          ]),
        ),
      ];
      if (d.joins.length)
        detail.push(
          table(
            ["目标实体", "本表键 → 目标键", "基数", "适用条件"],
            d.joins.map((join) => [
              code(join.target),
              codes(join.local) + " → " + codes(join.remote),
              join.cardinality + (join.nullable ? "，可空" : "，必填"),
              join.validity + "。" + join.use,
            ]),
          ),
        );
      parts.push(fold(name + " · " + d.table, detail.join("\n\n")));
    }
    return parts.join("\n\n");
  });
  const joinCount = entities.reduce((count, entity) => count + entity.definition.joins.length, 0);
  return fold(
    entities.length +
      " 种实体 · " +
      entities.reduce((n, e) => n + Object.keys(e.definition.fields).length, 0) +
      " 个字段 · " +
      joinCount +
      " 条关联",
    sections.join("\n\n"),
  );
}

const tenantNames = { 101: "远山零售", 102: "星河商贸" };
const scopeNames = { sales: "销售部", marketing: "营销部", finance: "财务部", personal: "个人" };
function scopeName(scope) {
  if (scope === "platform") return "平台公共";
  const [tenant, owner] = scope.split(".");
  return [tenantNames[tenant] ?? tenant, scopeNames[owner] ?? owner].filter(Boolean).join(" · ");
}

function termCatalog(terms, payload, revision, byRef) {
  const rows = terms.map((term) => {
    const aliases = payload.aliases.filter(
      (a) => a.ref === term.ref && a.display_alias !== term.name,
    );
    const targets = payload.relations.filter((r) => r.from === term.ref && r.kind === "denotes");
    return [
      term.name + " " + code(term.code),
      scopeName(term.scope),
      term.code === revision.code ? revision.summary : term.summary,
      aliases.map((a) => a.display_alias).join("、") || "—",
      targets
        .map(({ to }) => byRef.get(to).name + "（" + scopeName(byRef.get(to).scope) + "）")
        .join("、"),
      term.definition.question + " " + term.definition.counterexample,
    ];
  });
  const body =
    "#### 经营词汇索引\n\n" +
    table(["术语", "维护范围", "业务释义", "别名", "这个说法指向什么", "典型问法与适用边界"], rows);
  return fold(
    new Set(terms.map((t) => t.code)).size + " 种术语 · " + rows.length + " 个归属实例的释义与指向",
    body,
  );
}

function metricCatalog(metrics, payload) {
  const rows = Object.values(Object.groupBy(metrics, (asset) => asset.code)).map((assets) => {
    const metric = assets[0];
    const refs = new Set(assets.map((asset) => asset.ref));
    const bindings = payload.bindings.filter((binding) => refs.has(binding.ref));
    const sources = new Set([
      ...payload.relations
        .filter((relation) => relation.kind === "derived_from" && refs.has(relation.from))
        .map((relation) => relation.role),
      ...(metric.lineage ?? []),
    ]);
    return [
      metric.name + " " + code(metric.code),
      metric.definition.formula,
      metric.grain + "。" + metric.definition.event_time,
      code(metric.unit) + " ／ " + code(metric.definition.timezone),
      codes([...sources]),
      bindings.length
        ? bindings.map(({ source }) => scopeName(source.replace("retail:", ""))).join("、") +
          " 的候选 SQL"
        : metric.definition.blocked_reason,
    ];
  });
  return fold(
    rows.length + " 个指标的公式、粒度、来源与执行条件",
    table(["指标", "v2 公式", "粒度与业务时间", "单位与时区", "来源实体", "工作区执行条件"], rows),
  );
}

function fold(title, markdown) {
  return "[[catalog " + title + "]]\n\n" + markdown + "\n\n[[/catalog]]";
}

// Keep the shared renderer's diagrams, search and keyboard interactions; give this
// edition a compact title and link its opening dependency map to the three asset sections.
async function readingLayout(html, css) {
  const page = await applyCourseReader(
    html.replace(/<button class="search-trigger"[\s\S]*?<\/button>/, ""),
    cfg,
  );
  const layers = new Map(
    [...html.matchAll(/<h3 id="([^"]+)">L([1-3]) /g)].map((match) => [match[2], match[1]]),
  );
  if (layers.size !== 3) throw new Error("Each layer needs a detailed design section");
  return page
    .replace('href="#main-design"', 'href="TECH_DESIGN.html"')
    .replaceAll(
      '<div class="diagram-stage">',
      '<div class="diagram-stage" tabindex="0" role="region" aria-label="设计图，可横向滚动">',
    )
    .replace(
      /<svg\b[^>]*\bclass="erDiagram"[^>]*>/g,
      (svg) =>
        svg +
        "<style>.erDiagram .edgeLabel rect.background{fill:#ffffff!important}.erDiagram .edgeLabel text,.erDiagram .edgeLabel tspan{fill:#243e32!important}</style>",
    )
    .replace("</head>", "<!-- prettier-ignore -->\n<style>" + css + "</style>\n</head>")
    .replace('<article class="document"', '<!-- prettier-ignore -->\n<article class="document"')
    .replace(
      /<ol>\n(?=<li><a href="#layer-1")/,
      '<ol class="layer-index" aria-label="三类语义资产">\n',
    )
    .replace(
      /<li><a href="#layer-([1-3])">([^<]+)<\/a> · ([^<]+)<\/li>/g,
      (_, n, name, summary) =>
        '<li><a href="#' +
        layers.get(n) +
        '"><strong><small>L' +
        n +
        "</small>" +
        name +
        "</strong><span>" +
        summary +
        "</span></a></li>",
    )
    .replace(
      /<p>\[\[catalog ([^<\]]+)\]\]<\/p>/g,
      '<details class="asset-dictionary"><summary>$1</summary>',
    )
    .replaceAll("<p>[[/catalog]]</p>", "</details>");
}

const cfg = await json(path.join(directory, "retail-assets.json"));
const output = path.resolve(directory, cfg.output);
const [guide, css, edition, { payload }, installer, revision] = await Promise.all([
  read(path.join(directory, cfg.guide)),
  read(path.join(directory, "retail-reading.css")),
  loadDefinitions(),
  loadSemanticPack(),
  read(path.join(pack, "install.sql")),
  loadRetailTermRevision(),
]);
// The same entity definitions are instantiated once per teaching tenant.
const entities = [
  ...new Map(edition.assets.filter((a) => a.kind === "entity").map((a) => [a.code, a])).values(),
];
const temporary = await mkdtemp(path.join(tmpdir(), "compass-retail-design-"));
const staging = output + "." + process.pid + ".tmp";
try {
  const assetWrites = installer.match(
    /^    INSERT INTO web\.semantic_assets\([\s\S]+?;\n\n    INSERT INTO web\.semantic_versions\([\s\S]+?;/m,
  );
  if (!assetWrites) throw new Error("Check the asset and version INSERTs in retail-v1/install.sql");
  const model = await read(path.join(directory, "asset-model.mmd"));
  const prepared = guide
    .replace(
      "{{asset-model}}",
      () => "```mermaid 从资产身份读到指标的工作区绑定\n" + model.trimEnd() + "\n```",
    )
    .replace("{{catalog:entities}}", () => entityCatalog(entities))
    .replace("{{catalog:terms}}", () =>
      termCatalog(
        edition.assets.filter((a) => a.kind === "term"),
        payload,
        revision,
        new Map(edition.assets.map((asset) => [asset.ref, asset])),
      ),
    )
    .replace("{{catalog:metrics}}", () =>
      metricCatalog(
        edition.assets.filter((a) => a.kind === "metric"),
        payload,
      ),
    )
    .replace("{{sql:asset-versions}}", () =>
      [
        "```sql install.sql · 写入身份与初始版本",
        assetWrites[0].replace(/^    /gm, ""),
        "```",
      ].join("\n"),
    );
  const tempConfig = {
    ...cfg,
    source: path.resolve(directory, cfg.source),
    guide: "guide.md",
    output: "review.html",
    code: Object.fromEntries(
      Object.entries(cfg.code).map(([key, spec]) => [
        key,
        { ...spec, file: path.resolve(directory, spec.file) },
      ]),
    ),
  };
  const configFile = path.join(temporary, "config.json");
  await Promise.all([
    writeFile(configFile, JSON.stringify(tempConfig)),
    writeFile(path.join(temporary, tempConfig.guide), prepared),
  ]);
  const { output: rendered } = await renderReview(configFile);
  const html = await readingLayout(await read(rendered), css);
  await writeFile(
    staging,
    await format(html, { ...(await resolveConfig(output)), filepath: output }),
  );
  await rename(staging, output);
  process.stdout.write(output + "\n");
} finally {
  await rm(staging, { force: true });
  await rm(temporary, { recursive: true, force: true });
}
