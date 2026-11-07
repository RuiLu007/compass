import fs from "node:fs/promises";

function replaceRequired(text, pattern, replacement) {
  if (!text.match(pattern)) throw new Error("Learner edition source changed: " + pattern);
  return text.replace(pattern, () => replacement);
}

export async function createLearnerDocument(source) {
  const intro = await fs.readFile(new URL("learner-intro.md", import.meta.url), "utf8");
  const dataDesign = await fs.readFile(new URL("learner-data.md", import.meta.url), "utf8");
  const architecture = source.match(/### 1\.2 整体架构\n\n([\s\S]*?)\n### 1\.3 /)?.[1];
  let details = source.match(/(## 2\. 详细设计方案[\s\S]*?)\n## 3\. /)?.[1];
  if (!architecture || !details) throw new Error("Missing complete design sections");
  details = replaceRequired(
    details,
    /#### 2\.1\.1 数据结构设计[\s\S]*?(?=#### 2\.1\.2 LLM Runtime 完整 UML)/,
    dataDesign.trim() + "\n\n",
  );

  const paragraphs = [
    [
      /^V2 不新增部署环境变量[^\n]+/m,
      "运行依赖为 Python 3.13、LiteLLM Core 1.106.6、Pydantic、HTTPX、cryptography 与 jsonschema。LiteLLM Core 提供模型协议转换，HTTPX 承担异步 HTTP，Pydantic 校验类型，cryptography 保护凭据与续接密文，jsonschema 校验结构化结果。依赖版本在工程锁文件中固定。",
    ],
    [
      /^三款接入沿用现有绑定[^\n]+/m,
      "生成模型复用 LiteLLM Core 供应商协议。DeepSeek 示例使用 deepseek-v4-pro、deepseek-flash。决策模型共用 SystemOneDecisionAdapter，阿里云使用工作区 /compatible-mode/v1，JEV 官方使用 https://api.typesafe.ai/v1，调用时追加 /systemone。目录提供预设并支持手工输入任意有效型号；决策配置支持 typesafe/model、aliyun/model 和 systemone/model，在管理端测试。Chat 由平台启用和可见性配置控制。",
    ],
    [
      /^全部路径相对 Compass 根[^\n]+/m,
      "以下为实现目录及职责，路径相对 Compass 工程根。models/ 承担模型接入的应用服务，llm/ 承担单次模型调用内核；两者通过类型化请求和结果交接。目录随实际实现创建。",
    ],
    [
      /^本节完整列出本期 Runtime[^\n]+/m,
      "本节集中列出公开契约、内部类型及关键执行逻辑，阅读时无需打开工程文件。Protocol 使用签名表达边界，具体业务分支在 §2.4.4 展开；类型与算法片段用于说明设计，应用完整装配按 §2.3 的模块职责实现。",
    ],
    [
      /^下面包含当前 Feature 993[^\n]+/m,
      "以下完整列出可信上下文、能力、模型管理、生成/决策、结果、流事件与错误 envelope。公开类型使用 Pydantic 定义字段、默认值和校验语义，HTTP 层与生成契约共同使用这些定义。",
    ],
  ];
  for (const [pattern, replacement] of paragraphs)
    details = replaceRequired(details, pattern, replacement);

  details = replaceRequired(
    details,
    /```text\napps\/agent\/[\s\S]*?\n```/,
    source
      .match(/```text\napps\/agent\/[\s\S]*?\n```/)[0]
      .replace(/\[(保留|修改|新增|生成)\]\s*/g, "")
      .replace(/mai-specs\/993-lesson-11-llm-runtime\/design\/[\s\S]*?\n```/, "```")
      .replace("说明新执行职责", "应用启动与运行说明")
      .replace("公开 Runtime 与必要类型", "无副作用的包入口，类型按模块导入")
      .replace("0001/0002 不改写", "按应用管理 schema 变更")
      .replace(
        "│   │   └── secrets.py",
        "│   │   ├── validation.py                         端点允许列表和 URL 校验\n│   │   └── secrets.py",
      )
      .replace(
        "    ├── test_runtime.py",
        "    ├── test_model_lifecycle.py                    应用任务、断连和记录收尾\n    ├── test_runtime.py",
      ),
  );

  details = details
    .replace("### 2.2 配置变更", "### 2.2 运行依赖与配置")
    .replace("### 2.4 供评审的代码设计（for review check）", "### 2.4 完整类型与核心代码设计")
    .replace("当前用途与来源", "用途")
    .replace("| 沿用当前配置", "| 本地 http://127.0.0.1:8000")
    .replaceAll("保留已有 transport 兼容处理", "传输层处理规则")
    .replaceAll(
      "所有路由继续使用现有前缀和 HTTP Schema。",
      "接口通过 HTTP 提供模型管理与执行能力。",
    )
    .replaceAll(
      "内部 Runtime 的 Python 接口调整不对浏览器暴露。",
      "内部 Python 对象通过 DTO 映射为公开响应。",
    )
    .replaceAll("公开错误继续使用", "公开错误使用")
    .replaceAll("使用既有 503 映射", "返回 503")
    .replaceAll(
      "复用 DocumentSession 的 controller、generation、viewRevision 和显示期限",
      "前端以 DocumentSession 表示页面请求作用域，保存 controller、generation、viewRevision 和显示期限",
    )
    .replaceAll("不再承担模型执行状态判断", "模型执行状态由 Runtime 判断")
    .replaceAll(
      "本节将现有取消保护的行为收拢到明确 owner。实施时需验证 asyncio 的重复取消与线程池事务完成竞争，不能只检查最终页面文字。",
      "任务、模型资源和记录分别具有明确的收尾责任方。取消验证同时核对 asyncio 任务退出、线程池事务提交结果、数据库状态与最终页面，覆盖重复取消和提交竞争。",
    )
    .replaceAll("分别保留原有错误行为", "分别返回对应的安全错误类别")
    .replaceAll("当前接口不假装支持多次尝试", "当前接口仅支持单次尝试")
    .replaceAll("models/**init**.py", "models/\\_\\_init\\_\\_.py");

  details = replaceRequired(
    details,
    "7. HTTP 和 JSON Schema 从 schemas.py 生成；SQL 从迁移源生成；文档不成为生成文件的编辑入口。新目录职责 README 随实施交付。",
    "7. HTTP 和 JSON Schema 从 schemas.py 生成；SQL 从迁移源生成；生成文件随源更新。\n8. 当前 llm/ 的 11 个实现模块保持单层组织；适配器出现独立辅助模块时再提取 adapters/。路由、治理以及上层 Agent 工作流、调度、恢复目录均随实际实现创建。\n9. llm/runtime.py 管单次模型调用；上层 Agent 的多步运行编排按独立职责组织。models/ 只通过 Runtime 的类型化入口调用内核。",
  );

  const document = `${intro.trim()}\n\n${architecture.trim()}\n\n${details.trim()}\n`;
  if (
    /\]\((?!#)[^)]+\.md(?:#|\))|需求依据与验收|关键决策与 Review|原技术方案|原方案|IMPLEMENTATION|AC-993|\bV2\b|\b000[123](?:\b|_)/.test(
      document,
    )
  )
    throw new Error("Learner edition retains workflow references");
  return document;
}

export function createLearnerTemplate(template) {
  template = replaceRequired(
    template,
    /          <section class="status-panel"[\s\S]*?<\/section>\n/,
    "",
  );
  template = replaceRequired(
    template,
    /          <h2 id="acceptance">[\s\S]*?\{\{implementation\}\}\n/,
    "",
  );
  template = replaceRequired(
    template,
    /<a href="TECH_DESIGN\.v2\.md">[\s\S]*?<a href="#top">/,
    '<a href="#top">',
  );
  template = template
    .replaceAll("V2", "V3")
    .replace("类型契约与实施验收", "类型契约与核心执行逻辑")
    .replace(
      '<a class="desktop-link" href="#acceptance">实施验收</a>',
      '<a class="desktop-link" href="{{requirements}}">需求简述</a>',
    )
    .replace("V3 · 已实施", "V3 · 学员阅读版")
    .replace("Feature 993", "独立离线文档")
    .replace(
      '<a class="primary-link" href="{{architecture}}">阅读架构设计',
      '<a class="primary-link" href="{{requirements}}">从需求开始阅读',
    );
  return replaceRequired(
    template,
    '    <div class="toast"',
    `    <dialog id="screenshot-dialog" aria-labelledby="screenshot-title">
      <div class="dialog-header">
        <h2 id="screenshot-title">产品界面</h2>
        <button class="close-button" data-close aria-label="关闭截图">{{icon-close}}</button>
      </div>
      <div class="screenshot-viewport"><img id="screenshot-full" alt="" /></div>
    </dialog>
    <div class="toast"`,
  );
}
