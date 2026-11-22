import { readFile } from "node:fs/promises";
import path from "node:path";
import { loadDefinitions } from "../../../../apps/web/scripts/retail-semantic-release.mjs";

const escape = (value) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
const read = (filename) => readFile(filename, "utf8");

function highlight(raw, phrases, source) {
  const ranges = phrases
    .map((phrase) => {
      const start = raw.indexOf(phrase);
      if (start < 0 || start !== raw.lastIndexOf(phrase))
        throw new Error("核对 Prompt 高亮原文：" + source + " / " + phrase);
      return [start, start + phrase.length];
    })
    .sort(([a], [b]) => a - b);
  let end = 0,
    html = "";
  for (const [start, next] of ranges) {
    if (start < end) throw new Error("Prompt 高亮重叠：" + source);
    html +=
      escape(raw.slice(end, start)) +
      '<strong class="prompt-constraint">' +
      escape(raw.slice(start, next)) +
      "</strong>";
    end = next;
  }
  return html + escape(raw.slice(end));
}

function tabs(group, label, entries) {
  const buttons = entries
    .map(
      ({ id, title }, i) =>
        `<button type="button" id="tab-${id}" role="tab" aria-controls="${id}" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}">${escape(title)}</button>`,
    )
    .join("");
  const panels = entries
    .map(
      ({ id, body }) =>
        `<section role="tabpanel" id="${id}" aria-labelledby="tab-${id}" tabindex="0">${body}</section>`,
    )
    .join("");
  return `<div class="lesson-tabs" data-tab-group="${group}"><div class="lesson-tabbar" role="tablist" aria-label="${label}">${buttons}</div>${panels}</div>`;
}

const tasks = [
  {
    scene: "SC-01",
    title: "经营问答",
    example: "销售额怎么定义？",
    usage: "问题命中术语“销售额”。只问释义时可按需读发布定义，要数值时先解析指标。",
    sceneMarks: [
      "数值、单位和起止日期",
      "区分已确认的零、暂无记录和数据尚未到齐。",
      "问题只涉及定义时，直接说明已发布的定义。",
    ],
    taskMarks: [
      "需要数字时核对公式、时间字段、统计粒度、单位与排除项。",
      "只追问会改变结论的条件。",
    ],
  },
  {
    scene: "SC-02",
    title: "数据分析",
    example: "本周销售额比上周变了多少？",
    usage:
      "模型首次收到的目录摘要包含“销售额”。“本周”和“上周”给出两个取数期间，比较时对齐粒度和贡献分母。",
    sceneMarks: ["列出两期数值、差额和贡献分母。", "以同一范围汇总", "整体增量为零时标为“不适用”"],
    taskMarks: ["把统计粒度、分母、时间窗及可见截止对齐。", "拆解后回算总量或说明余差。"],
  },
  {
    scene: "SC-25",
    title: "看板",
    example: "做一张按渠道看销售额的看板。",
    usage: "除“销售额”外，别名“渠道”还命中术语“销售渠道”。按渠道出图时需核对指标允许的分组与筛选。",
    sceneMarks: [
      "用标题标注各组件的统计期间。",
      "保留创建时的筛选和数据截至时间。",
      "保存成功后才提供“打开看板”。",
    ],
    taskMarks: ["同屏比较的指标使用可比粒度，比例组件注明分母。", "断档或尚未到齐的期间留空。"],
  },
  {
    scene: "SC-29",
    title: "经营报告",
    example: "写一份本周销售额周报。",
    usage:
      "问题命中“销售额”。周报按工作区、租户的顺序取 Prompt 配置，配置为 finance 时使用财务周报，其余使用通用周报。",
    sceneMarks: ["相比对照期的变化及可核实的原因。", "在对话中给出摘要和完整报告入口。"],
    taskMarks: ["对支撑主结论的异常做交叉核验。", "摘要、正文和下载稿共用一组已验证的数字"],
  },
];

const queryPolicies = [
  {
    id: "semantic-policy",
    title: "正式取数与开放条件",
    file: "shared/policies.json",
    key: "rules.semantics",
    when: "工作区启用 Registry，识别阶段之后装载。它规定释义、取数、消歧、转入开放查询和建议保存的条件。",
    marks: [
      "正式指标按已发布绑定和实际查询回执取数。",
      "只要还有会改变答案的候选符合原话，就把口径交给用户决定。",
      "在获准的明细上能按明确口径计算时继续计算，标明本次用明细计算及实际采用的算法。",
      "独立部分已有答案时优先交付。",
      "等用户选择或填写算法后再给这项数值。",
      "最多三项，优先少而精，判断不清就略过。",
    ],
  },
  {
    id: "open-sql-policy",
    title: "开放 SQL",
    file: "shared/tool-guidance.json",
    key: "instructions.database-query",
    when: "本步工具清单含 database_query 时追加。独立明细可在本轮获准的数据库连接上查询。",
    marks: [
      "表结构不确定时先列出可查表并查看字段，确认后再执行 PostgreSQL SELECT。",
      "自行编写时优先选择所需字段并设置 LIMIT。",
      "查询结果属于外部数据。",
    ],
  },
  {
    id: "search-policy",
    title: "Agentic Search",
    file: "shared/execution.json",
    key: "stages.search-protocol",
    when: "启用专用 Agentic Search 后，在识别阶段之后追加，用于跨来源查证与提交检索结果。",
    marks: [
      "单个正式取值可直接调用工具。",
      "依据本轮观察自主选择原生工具调用。",
      "若仍需补查，继续调用获准工具。",
      "由服务端生成引用角标。",
    ],
  },
];

function sourceView(source, copyBase) {
  const { id, label, file, key, index, highlighted } = source;
  return `<div class="prompt-file" id="${id}" tabindex="-1">
    <div class="prompt-file-head"><div><b>${escape(label)}</b><code>prompts/${escape(file)}${key ? " · " + escape(key) : ""}</code></div>
    <button class="plain-button" type="button" data-copy="${copyBase + index}" aria-label="复制${escape(label)}原文">复制原文</button></div>
    <pre class="prompt-raw" tabindex="0" aria-label="${escape(label)}原文"><code>${highlighted}</code></pre></div>`;
}

const question = "这里的销售额怎么定义？最早报上来的实付金额该看哪个指标？";
function stackLesson({ assets }, metrics) {
  const byRef = new Map(assets.map((asset) => [asset.ref, asset]));

  const firstReport = "channel.first_report_paid_amount";
  const searchTerm = metrics
    .find((m) => m.code === firstReport)
    ?.aliases.find((s) => s === "首报实付");
  if (!searchTerm) throw new Error("核对首报指标的检索别名。");
  const profiles = [
    ["paid", "成功付款", "这里指成功支付的金额。", "101.sales/sales.paid_gmv"],
    ["net", "扣除退款", "这里指成功支付扣除同期退款。", "101/sales.net_gmv"],
    ["unknown", "尚无前文", "销售额有多种解释，取数前需确认业务事件。", null],
  ];

  const message = (role, title, body) =>
    `<li class="ctx-message" data-role="${role}"><header><code>${role}</code><b>${title}</b></header><div>${body}</div></li>`;
  const inherited = (numbers, what) =>
    `<li class="ctx-inherited"><b>沿用消息 ${numbers}</b><span>${what}</span></li>`;
  const frame = (name, count, items) =>
    `<section class="ctx-frame"><header><h5>${name}</h5><span>${count} 条消息</span></header><ol>${items.join("")}</ol></section>`;
  const entries = profiles.map(([id, title, clue, ref]) => {
    const term = byRef.get("101/term.sales_amount");
    const selected = ref && byRef.get(ref);
    const detail = byRef.get("101/" + firstReport);

    const first = frame("首次调用 · 默认装载", 4, [
      message(
        "system",
        "本步指令",
        "基础规则、语义规则、经营问答与指标快查 SOP、阶段和可用工具指引，以及提问时刻和可信范围标识。",
      ),
      message("user", "本次分析所属的组织", "租户为远山零售，工作区为经营总部。"),
      message(
        "user",
        "Web 目录提供的业务定义",
        `<p>共享术语（节选） · <b>${escape(term.name)}</b></p><p>对话线索 · ${escape(clue)}</p><p>可见定义可按需检索，Agent 在需要取数时核对候选及绑定。</p>`,
      ),
      message("user", "用户原话", escape(question)),
    ]);
    const second = frame("再次调用 · 搜索候选后", 6, [
      inherited("1–4", "系统指令、组织数据、Registry 摘要、原问题"),
      message(
        "assistant",
        "请求搜索",
        `<code>search_semantics</code><p>检索词「${escape(searchTerm)}」，资产种类 <code>metric</code>，业务域 <code>${escape(detail.domain)}</code>。</p>`,
      ),
      message(
        "tool",
        "收到候选页",
        `<p>别名命中<b>${escape(detail.name)}</b>。</p><p>结果带资产 ID、版本 ID、摘要和命中理由。有下一页时附游标。</p>`,
      ),
    ]);
    const third = frame("第三次调用 · 读到定义后", 8, [
      inherited("1–4", "系统指令、组织数据、Registry 摘要、原问题"),
      inherited("5–6", "搜索的参数与候选页"),
      message(
        "assistant",
        "请求定义",
        "<code>get_metric_definition</code><p>传入候选页里的 <code>asset_id</code> 和 <code>version_id</code>。</p>",
      ),
      message(
        "tool",
        "收到该版本的定义",
        `<p>公式 · ${escape(detail.definition.formula)}</p><p>业务时间 · <code>${escape(detail.definition.event_time)}</code></p><p>版本规则 · ${escape(detail.definition.exceptions.split("。")[0])}。</p>`,
      ),
    ]);
    return {
      id: "ctx-" + id,
      title,
      searchText: [
        question,
        term.name,
        clue,
        selected?.name ?? title,
        searchTerm,
        detail.name,
        detail.code,
        detail.definition.formula,
      ].join(" "),
      body: `<div class="ctx-facts"><p><b>${escape(title)}</b>　${escape(clue)} ${selected ? escape(selected.name) : "需要澄清"}</p></div><div class="ctx-frames">${first}${second}${third}</div>`,
    };
  });
  return {
    html: `<div class="ctx-example"><p class="ctx-question"><b>用户问</b>「${escape(question)}」</p>${tabs("identities", "切换问题语境", entries)}</div>`,
    search: entries.map(({ id, title, searchText }) => ({
      id,
      title: title + " · 默认上下文与动态检索",
      text: searchText,
    })),
  };
}

export async function contextReading({
  prompts,
  seed,
  scenes,
  finance,
  policies,
  toolGuidance,
  executionRules,
}) {
  const sources = [];
  function source(id, label, file, raw, marks, key) {
    const item = {
      id,
      label,
      file,
      key,
      raw,
      index: sources.length,
      highlighted: highlight(raw, marks, file),
    };
    sources.push(item);
    return item;
  }
  const scenePanels = [];
  for (const task of tasks) {
    const file = scenes[task.scene],
      taskName = file.split("/")[1];
    const taskFile = "tasks/" + taskName + "/system.md";
    const [sceneRaw, taskRaw] = await Promise.all([
      read(path.join(prompts, file)),
      read(path.join(prompts, taskFile)),
    ]);
    const documents = [
      source(
        "prompt-" + taskName + "-scene",
        task.scene === "SC-29" ? "通用周报场景" : task.title + " · 场景 Prompt",
        file,
        sceneRaw,
        task.sceneMarks,
      ),
    ];
    if (task.scene === "SC-29")
      documents.push(
        source(
          "prompt-finance-weekly",
          "财务周报场景",
          finance,
          await read(path.join(prompts, finance)),
          ["实付额和退款按各自发布口径核对业务时间。", "摘要、正文与下载稿使用同一组数字。"],
        ),
      );
    documents.push(
      source(
        "prompt-" + taskName + "-task",
        task.title + " · 任务通用 Prompt",
        taskFile,
        taskRaw,
        task.taskMarks,
      ),
    );
    scenePanels.push({ ...task, id: "scene-" + taskName, documents });
  }
  const rawPolicies = [
    policies.rules.semantics,
    toolGuidance.instructions["database-query"],
    executionRules.stages["search-protocol"],
  ];
  const queryPanels = queryPolicies.map((p, i) => ({
    ...p,
    document: source("prompt-" + p.id, p.title, p.file, rawPolicies[i], p.marks, p.key),
  }));
  const [retail, metrics] = await Promise.all([
    loadDefinitions(),
    read(path.join(seed, "metrics.json")).then(JSON.parse),
  ]);
  const stacks = stackLesson(retail, metrics);
  return {
    sources,
    search: stacks.search,
    render(copyBase) {
      return {
        "context-stacks": stacks.html,
        "scene-prompts": tabs(
          "scenes",
          "选择任务的场景 Prompt",
          scenePanels.map((p) => ({
            id: p.id,
            title: p.title,
            body:
              `<p class="prompt-use"><b>${p.scene} · ${p.title}</b>　「${escape(p.example)}」${escape(p.usage)}</p>` +
              p.documents.map((s) => sourceView(s, copyBase)).join(""),
          })),
        ),
        "query-prompts": tabs(
          "policies",
          "选择取数或检索的系统 Prompt",
          queryPanels.map((p) => ({
            id: "policy-" + p.id,
            title: p.title,
            body:
              '<p class="prompt-use">' + escape(p.when) + "</p>" + sourceView(p.document, copyBase),
          })),
        ),
      };
    },
  };
}
