import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";

const file = (name) => fileURLToPath(new URL(name, import.meta.url));
function section(source, start, end) {
  const from = source.indexOf(start);
  const to = end ? source.indexOf(end, from + start.length) : source.length;
  if (from < 0 || to < 0) throw new Error(`Source section missing: ${start}`);
  return source.slice(from, to).trim();
}
function replace(source, before, after) {
  if (!source.includes(before)) throw new Error(`Source changed: ${before}`);
  return source.replace(before, after);
}

async function createDocument(source) {
  const code = await fs.readFile(file("../CODE_DESIGN.md"), "utf8");
  const intro = await fs.readFile(file("intro.md"), "utf8");
  const diagrams = await fs.readFile(file("diagrams.md"), "utf8");
  const graph = (name) => {
    const match = diagrams.match(
      new RegExp(`<!-- diagram:${name} -->\\s*(\x60\x60\x60mermaid[\\s\\S]*?\x60\x60\x60)`),
    );
    if (!match) throw new Error(`Diagram missing: ${name}`);
    return match[1];
  };
  let contracts = section(source, "## 2. 分类、字典与模型契约", "## 3. 应用流程与失败处理");
  contracts = contracts
    .replace(/^## 2\.[^\n]+\n\n/, "")
    .replace(/^### 2\.(\d) /gm, "#### 2.1.4.$1 ");
  contracts = replace(
    contracts,
    "| 用户fixed",
    "| SC-39 闲聊 / 业务范围外 | 优先使用专用 Flash 绑定与独立范围引导提示词，reason=off_topic_flash |\n| 用户fixed",
  );
  contracts = replace(contracts, "| 用户fixed                  |", "| 业务场景且用户fixed |");

  let http = section(source, "## 4. HTTP与响应结构", "## 5. 配置、授权与数据")
    .replace(/^## 4\.[^\n]+\n\n/, "")
    .replace(/^### 4\.(\d) /gm, "#### 2.3.$1 ");
  http = replace(
    http,
    "fixed请求在选模后校验实际主模型符合用户指定接入。",
    "业务 fixed 请求在选模后校验实际主模型符合用户指定接入；SC-39 校验专用 Flash 绑定及 off_topic_flash 原因。",
  );

  let configuration = section(source, "## 5. 配置、授权与数据", "### 5.3 数据与记录")
    .replace(/^## 5\.[^\n]+\n\n/, "")
    .replace(/^### 5\.(\d) /gm, "#### 2.2.$1 ");
  configuration +=
    "\n\n代码配置另登记范围外响应的 Flash 角色与专用提示词版本；分类 Flash、业务生成 Flash 和范围外 Flash 可以复用 Provider，保留各自语义。";

  let core = section(code, "## 1. 类型与职责", "## 6. 评审重点");
  core = replace(core, "### 1.1 代码配置与统一入口", "#### 2.4.2 代码配置与统一入口");
  const headings = [
    ["## 1. 类型与职责", "#### 2.4.1 类型与职责"],
    ["## 2. 两题JEV与Flash统一校验", "#### 2.4.3 两题 JEV 与 Flash 统一校验"],
    ["## 3. 分类主路与一次兜底", "#### 2.4.4 分类主路与一次兜底"],
    ["## 4. 字典特征与模型选择", "#### 2.4.5 字典特征与模型选择"],
    ["## 5. 最小调用链", "#### 2.4.6 最小调用链"],
  ];
  for (const [before, after] of headings) core = replace(core, before, after);
  core = replace(
    core,
    '    if not calibration.matches(outcome.model_version, dictionary.version, outcome.template_version):\n        return None, "calibration_unavailable"\n',
    "",
  );
  core = core.replaceAll("calibration", "acceptance_defaults");
  core = replace(
    core,
    '    if selection.mode == "fixed":',
    `    if intent.scene_id == "SC-39":
        # 范围外响应优先于 fixed/auto，绑定独立范围引导提示词。
        provider, reason = bindings.require_off_topic_flash(), "off_topic_flash"
    elif selection.mode == "fixed":`,
  );
  core = replace(
    core,
    "fixed始终只覆盖本次推理模型，不影响此前分类。",
    "业务场景的 fixed 绑定本次推理模型，分类配置保持独立；SC-39 优先使用范围外 Flash。",
  );
  core = replace(
    core,
    "auto的Provider在route_selected后校验",
    "实际 Provider 在 route_selected 后按业务 fixed/auto 或 SC-39 规则校验",
  );
  core = replace(
    core,
    "原provider_id请求也进入相同前置分类链",
    "原 provider_id 请求归一化为 fixed 并进入相同前置分类链，SC-39 仍遵循范围外 Flash 规则",
  );

  const records = section(source, "### 5.3 数据与记录", "## 6. 验证与实验").replace(
    /^### 5\.3[^\n]+\n\n/,
    "",
  );
  const process = section(source, "### 3.1 正常与分类兜底", "## 4. HTTP与响应结构")
    .replace("### 3.1 正常与分类兜底", "#### 正常与分类兜底")
    .replace("### 3.2 时限、取消与未知结果", "#### 时限、取消与未知结果")
    .replace(
      "4. 字典提取特征，路由契约选择模型",
      "4. 字典提取特征；SC-39 优先绑定范围外 Flash，业务场景按 fixed/auto 路由契约选择模型",
    );
  const directory = `### 2.3.7 目录组织与职责

以下为拟新增或拟调整位置，目录随实现创建。

\x60\x60\x60text
apps/agent/
├── src/compass_agent/
│   ├── model_api.py                统一 models/calls 入口，保留 Provider 诊断
│   ├── schemas.py                  公开 DTO 与事件唯一源
│   ├── routing/                    本课拟新增的薄应用服务
│   │   ├── README.md               分类、字典与选模职责
│   │   ├── contracts.py            Intent / Features / Route / Grant
│   │   ├── config.py               角色、默认门槛、预算与模板版本
│   │   ├── intent.py               两级分类与一次 Flash 兜底
│   │   ├── dictionary.py           只读字典、归属检查与特征查表
│   │   ├── router.py               范围外 Flash / fixed / auto
│   │   ├── service.py              分类到单次推理的协调
│   │   └── data/scenes.json        从工作簿派生，无问题金标
│   ├── models/                     复用配置、授权、凭据和记录
│   └── llm/                        复用单次 Runtime 与适配器
└── scripts/export_routing_dictionary.py   拟新增字典导出入口
apps/web/
└── src/                            更新 BFF、Chat、事件和响应解析
contracts/                          由 Agent DTO 与路由派生
\x60\x60\x60

HTTP → routing → models → llm 的依赖保持单向。Web 与 Agent 通过 HTTP 协作；生成文件随 schemas.py 与路由源更新。
`;

  let document = `${intro.trim()}\n\n${graph("architecture")}

## 2. 详细设计方案

### 2.1 核心结构与请求流程

#### 2.1.1 数据结构与记录归属

本课复用已有 Agent 数据库的 Provider 配置和 ModelCall 元记录。每次物理分类与推理各有调用标识，整轮阶段关联、分类和路由解释在响应与页面内存中组织。

${graph("data")}

${records}

#### 2.1.2 组件 UML 与类型关系

下图表达拟定组件职责和依赖方向；方法用于说明边界。公开响应中的 intent、routing 和 result 在失败阶段可为空，具体可空规则见接口设计。

${graph("components")}

${graph("contracts")}

#### 2.1.3 完整推理请求时序

状态图描述整轮应用流程，持久化继续记录各次物理调用。

${graph("states")}

${graph("sequence")}

${process}

#### 2.1.4 两级分类、字典与路由契约

${contracts}

### 2.2 运行依赖、配置与授权

复用 Python 3.13、FastAPI、Pydantic、现有 LiteLLM 与 SystemOne 适配器、PostgreSQL 调用记录。规则与阈值维护在服务端代码中，Provider 连接和凭据通过已有模型管理装配。

${configuration}

### 2.3 HTTP、响应与模块组织

${http}

${directory}

### 2.4 类型与核心代码设计

以下保留维护方案中的类型和核心算法。它们是待实现的设计骨架，复用已有 StrictModel、DecisionInput、GenerateInput、Runtime 和 ModelService；DTO 完整字段、辅助函数、序列化和框架装配按工程实现补齐。JEV 使用代码默认接受门槛；效果校准安排在后续课程。SC-39 的 Flash 分支优先于用户主模型选择，输出契约通过独立提示词表达温和范围引导。

${core}
`;
  // 离线版本保留必要文字，仓库导航继续由维护说明承担。
  document = document.replace(/\[([^\]]+)\]\((?!#)[^)]+\)/g, "$1");
  if (/不是[\s\S]{0,30}而是|calibration_unavailable|需求依据与范围|## 2026-/.test(document))
    throw new Error("Learner document contains superseded narrative");
  return document;
}

function createTemplate(template) {
  template = template
    .replaceAll(
      "Compass LLM Runtime V3 完整技术设计：架构、数据库 ER、UML、推理时序、类型契约与核心执行逻辑。",
      "Compass 第12课技术方案：两级意图分类、字典特征、契约选模、统一接口与核心代码。",
    )
    .replaceAll("LLM Runtime V3 · Compass 技术设计", "多模型路由 · Compass 第12课技术方案")
    .replace("<span>011</span>", "<span>012</span>")
    .replace(
      "LLM Runtime<span>从模型接入，<br />到可控的每次推理。</span>",
      "多模型路由<span>识别当前任务，<br />选择本次模型。</span>",
    )
    .replace(
      /在 Agent\s*进程内建立统一的模型执行内核。[\s\S]*?Chat。/,
      "将两级意图识别、字典特征与确定性模型路由接入统一 Chat。复用已有 Runtime 完成本次推理，逐层理解分类、选模与响应契约。",
    )
    .replace("V3 · 学员阅读版", "学员阅读版 · 设计待实现")
    .replaceAll("2026-11-04", "2026-11-05")
    .replaceAll("2026.11.04", "2026.11.05")
    .replace("数据库 ER</a>", "数据与记录</a>")
    .replace("完整 UML</a>", "组件与类型</a>")
    .replace("完整类型定义</a>", "类型与核心代码</a>")
    .replace("类型契约与核心执行逻辑", "分类、选模与核心执行逻辑")
    .replace("COMPASS · LLM RUNTIME V3", "COMPASS · MODEL ROUTING / LESSON 12")
    .replace("试试 RuntimeResult、取消、数据库…", "试试 Intent、SC-39、固定模型…");
  template = template.replace(
    /<div class="metrics"[\s\S]*?<\/div>\n      <\/section>/,
    `<div class="metrics" aria-label="设计约束">
    <div class="metric"><strong>2</strong><small>个分类字段</small><p>category_id / scene_id</p></div>
    <div class="metric"><strong>1</strong><small>次前端请求</small><p>统一 models/calls 入口</p></div>
    <div class="metric"><strong>≤3</strong><small>次模型出站</small><p>JEV + 分类 Flash + 主推理</p></div>
    <div class="metric"><strong>0</strong><small>项新增数据库结构</small><p>复用 Provider 与 ModelCall</p></div>
    </div>\n      </section>`,
  );
  template = template.replace(
    /<svg\s+viewBox="0 0 480 330"[\s\S]*?<\/svg>/,
    `<svg viewBox="0 0 480 330" role="img" aria-labelledby="hero-diagram-title">
    <title id="hero-diagram-title">两级分类到一次主模型推理</title>
    <defs><marker id="hero-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0 0 6 3 0 6" fill="none" stroke="#6b8767" /></marker></defs>
    <g font-family="-apple-system,BlinkMacSystemFont,PingFang SC,sans-serif">
    <rect x="25" y="18" width="430" height="52" rx="8" fill="#fff" stroke="#cbd9c3" />
    <text x="44" y="41" font-size="15" fill="#23462f">统一 models/calls</text><text x="44" y="59" font-size="11" fill="#526b4c">问题 / 成功上下文 / 主模型选择</text>
    <path d="M240 70 V92" stroke="#6b8767" marker-end="url(#hero-arrow)" />
    <rect x="25" y="96" width="430" height="59" rx="8" fill="#234b3d" />
    <text x="44" y="121" font-size="15" fill="#f0f6e7">JEV → Flash 兜底</text><text x="44" y="142" font-size="12" fill="#c5d9bc">输出 category_id + scene_id</text>
    <path d="M240 155 V179" stroke="#6b8767" marker-end="url(#hero-arrow)" />
    <rect x="25" y="183" width="200" height="55" rx="8" fill="#fff" stroke="#cbd9c3" /><text x="44" y="207" font-size="14" fill="#23462f">字典特征</text><text x="44" y="226" font-size="11" fill="#526b4c">任务 / 难度 / 响应约束</text>
    <path d="M225 210 H250" stroke="#6b8767" marker-end="url(#hero-arrow)" />
    <rect x="255" y="183" width="200" height="55" rx="8" fill="#fff" stroke="#cbd9c3" /><text x="273" y="207" font-size="14" fill="#23462f">契约选模</text><text x="273" y="226" font-size="11" fill="#526b4c">范围外 Flash / fixed / auto</text>
    <path d="M355 238 V261 H240 V276" stroke="#6b8767" fill="none" marker-end="url(#hero-arrow)" />
    <rect x="80" y="280" width="320" height="36" rx="18" fill="#d7edb4" stroke="#bacf99" /><text x="240" y="303" text-anchor="middle" font-size="13" fill="#34502a">现有 Runtime · 一次主模型推理</text></g></svg>`,
  );
  template = template
    .replace("ONE REQUEST. CLEAR OWNERSHIP.", "CLASSIFY. LOOK UP. SELECT. INVOKE.")
    .replace("IN-PROCESS", "ROUTING")
    .replace(
      "应用管理配置与记录，Runtime 管理执行生命周期。",
      "模型识别标签，字典提取特征，契约决定本次模型。",
    );
  if (/LLM Runtime V3|张持久化表|设计已落地|已验收|011<|Feature 993/.test(template))
    throw new Error("Template retains lesson 11 content");
  return template;
}

export default {
  sourceFile: file("../TECH_DESIGN.md"),
  output: file("../TECH_DESIGN.html"),
  createDocument,
  createTemplate,
  graphTitles: [
    "统一推理链路与依赖",
    "数据来源与记录归属",
    "UML · 组件职责",
    "UML · 类型关系",
    "整轮状态与终态",
    "完整推理请求时序",
  ],
  graphCaptions: [
    "SC-39 优先走 Flash 专用响应；业务场景遵循 fixed/auto。",
    "字典由工作簿派生；复用已有 Provider 和 ModelCall，整轮信息保留于响应与页面内存。",
    "routing 协调分类、查表和选模，models 与 Runtime 承担现有调用职责。",
    "Intent 严格两字段；特征、路由及内部授权由服务端生成。",
    "整轮状态由分类、选模和推理共同决定；应用流程与单次调用记录各有职责。",
    "浏览器只发送一次请求；分类子调用事件经应用层整合，整轮最多一个 start 和一个 final。",
  ],
};
