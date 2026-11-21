import { mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { format, resolveConfig } from "prettier";
import { renderReview } from "../../../../.agents/skills/my-design-to-html/scripts/render.mjs";

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
    const fields = items.map(({ name, grain, definition: d }) => [
      name + " " + code(d.table),
      grain + "。主键 " + codes(d.primary_key),
      d.event_time + " ／ " + d.visible_time,
      Object.entries(d.fields)
        .map(([field, meaning]) => code(field) + " " + meaning)
        .join("、"),
    ]);
    const joins = items.flatMap(({ definition: d }) =>
      d.joins.map((j) => [
        code(d.table) + " → " + code(j.target),
        codes(j.local) + " → " + codes(j.remote),
        j.cardinality + (j.nullable ? "，可空" : "，必填"),
        j.validity + "。" + j.use,
      ]),
    );
    const parts = [
      "#### " + (domains[domain] ?? domain),
      table(["实体与源表", "粒度与主键", "业务时钟／可见时钟", "登记字段"], fields),
    ];
    if (joins.length)
      parts.push(table(["关联方向", "对应键（按顺序）", "基数", "适用条件"], joins));
    return parts.join("\n\n");
  });
  const joinCount = entities.reduce((count, entity) => count + entity.definition.joins.length, 0);
  return fold(
    entities.length + " 种实体与 " + joinCount + " 条关联的完整字典",
    sections.join("\n\n"),
  );
}

function termCatalog(terms) {
  const rows = terms.map((term) => {
    const exceptions = Object.entries(term.targets_by_scope ?? {}).map(
      ([scope, targets]) => scope + " 采用 " + codes(targets),
    );
    return [
      term.name + " " + code(term.code),
      term.aliases.join("、") || "—",
      term.owners.join("、"),
      [codes(term.targets), ...exceptions].join("。"),
      term.definition.question + " " + term.definition.counterexample,
    ];
  });
  const body =
    "#### 经营词汇索引\n\n" +
    table(["术语", "别名", "维护范围", "所指指标或实体", "典型问法与适用边界"], rows);
  return fold(terms.length + " 种术语的名称、别名与指向", body);
}

function fold(title, markdown) {
  return "[[catalog " + title + "]]\n\n" + markdown + "\n\n[[/catalog]]";
}

// Keep the shared renderer's diagrams, search and keyboard interactions; give this
// edition a compact title and link its opening dependency map to the six design sections.
function readingLayout(html, css) {
  const hero = /<section class="hero" aria-labelledby="page-title">[\s\S]*?<\/section>/;
  const title = html.match(/<h1 id="page-title">([^<]+)<span>/)?.[1];
  if (!title || !hero.test(html)) throw new Error("The reading template title is missing");
  const layers = new Map(
    [...html.matchAll(/<h3 id="([^"]+)">L([1-6]) /g)].map((match) => [match[2], match[1]]),
  );
  if (layers.size !== 6) throw new Error("Each layer needs a detailed design section");
  const menu = [...html.matchAll(/<h([23]) id="(section-\d+)">([^<]+)<\/h\1>/g)]
    .map(
      ([, level, id, label]) =>
        '<a class="level-' + level + '" href="#' + id + '">' + label + "</a>",
    )
    .join("");
  return html
    .replace(hero, '<header class="edition-title"><h1 id="page-title">' + title + "</h1></header>")
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
    .replace(
      '<nav class="top-links" aria-label="快捷导航">',
      (nav) =>
        nav +
        '<details class="mobile-nav"><summary>目录</summary><div>' +
        menu +
        "</div></details>",
    )
    .replace("</head>", "<!-- prettier-ignore -->\n<style>" + css + "</style>\n</head>")
    .replace('<article class="document"', '<!-- prettier-ignore -->\n<article class="document"')
    .replace(/<ol>\n(?=<li><a href="#layer-1")/, '<ol class="layer-index" aria-label="六层设计">\n')
    .replace(
      /<li><a href="#layer-([1-6])">([^<]+)<\/a> · ([^<]+)<\/li>/g,
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
const [guide, css, entities, terms, installer] = await Promise.all([
  read(path.join(directory, cfg.guide)),
  read(path.join(directory, "retail-reading.css")),
  json(path.join(pack, "entities.json")),
  json(path.join(pack, "terms.json")),
  read(path.join(pack, "install.sql")),
]);
const temporary = await mkdtemp(path.join(tmpdir(), "compass-retail-design-"));
const staging = output + "." + process.pid + ".tmp";
try {
  const assetWrites = installer.match(
    /^    INSERT INTO web\.semantic_assets\([\s\S]+?;\n\n    INSERT INTO web\.semantic_versions\([\s\S]+?;/m,
  );
  if (!assetWrites) throw new Error("Check the asset and version INSERTs in retail-v1/install.sql");
  const prepared = guide
    .replace("{{catalog:entities}}", () => entityCatalog(entities))
    .replace("{{catalog:terms}}", () => termCatalog(terms))
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
  const html = readingLayout(await read(rendered), css);
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
