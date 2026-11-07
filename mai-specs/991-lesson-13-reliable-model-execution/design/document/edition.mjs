import fs from "node:fs/promises";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const file = (name) => fileURLToPath(new URL(name, import.meta.url));

function requiredReplace(source, pattern, value) {
  if (typeof pattern === "string" ? !source.includes(pattern) : !pattern.test(source))
    throw new Error(`Document template changed: ${pattern}`);
  return source.replace(pattern, () => value);
}

const types = `\x60\x60\x60python
# 设计类型：LogicalPolicy 与有界执行摘要。
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict
from uuid import UUID

type TerminalStatus = Literal["completed", "failed", "canceled", "incomplete"]
type Recovery = Literal["not_sent_transient", "rejected_transient", "never"]
type StopReason = Literal[
    "completed", "non_retryable", "attempts_exhausted", "deadline_exceeded",
    "canceled", "overloaded", "cleanup_unconfirmed",
]


@dataclass(frozen=True, slots=True)
class LogicalPolicy:
    max_attempts: Literal[1, 2] = 1
    attempt_timeout_s: float = 180.0


class UsageSummary(TypedDict):
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None


class AttemptSummary(TypedDict):
    attempt_id: UUID
    number: int
    provider_id: UUID
    config_version: int
    binding_version: int
    started_at: datetime
    duration_ms: int
    status: TerminalStatus
    error_code: str | None
    upstream_request_id: str | None
    upstream_outcome_unknown: bool
    sent: bool
    cleanup_confirmed: bool
    usage: UsageSummary


class ExecutionSummary(TypedDict):
    version: Literal[1]
    max_attempts: Literal[1, 2]
    queue_wait_ms: int
    stop_reason: StopReason
    attempts: list[AttemptSummary]  # 0–2 项，按 number 排序。
\x60\x60\x60`;

async function createDocument(source) {
  for (const term of ["LogicalPolicy", "model_call.execution", "max_attempts=1", "K=8", "N=32"])
    if (!source.includes(term))
      throw new Error(`Design changed; reconcile learner edition: ${term}`);
  const [guide, diagrams, codeSource] = await Promise.all([
    fs.readFile(file("guide.md"), "utf8"),
    fs.readFile(file("diagrams.md"), "utf8"),
    fs.readFile(file("../CODE_DESIGN.md"), "utf8"),
  ]);
  const blocks = [...codeSource.matchAll(/^```(?:python|typescript)\n[\s\S]*?^```/gm)].map(
    (match) => match[0],
  );
  if (blocks.length !== 6) throw new Error("Expected the six canonical code design blocks");
  const expected = [
    "class ExecutionGate",
    "async def execute",
    "def classify_recovery",
    "def aggregate_usage",
    "pendingFinal",
    "async def run_stage",
  ];
  blocks.forEach((block, index) => {
    if (!block.includes(expected[index])) throw new Error(`Code block changed: ${expected[index]}`);
  });
  const document = guide
    .replace(/\{\{diagram:([a-z]+)\}\}/g, (_, name) => {
      const match = diagrams.match(
        new RegExp(`<!-- diagram:${name} -->\\s*(\x60\x60\x60mermaid[\\s\\S]*?\x60\x60\x60)`),
      );
      if (!match) throw new Error(`Diagram source missing: ${name}`);
      return match[1];
    })
    .replace(/\{\{code:(\d+)\}\}/g, (_, index) => {
      if (!blocks[Number(index)]) throw new Error(`Code block missing: ${index}`);
      return blocks[Number(index)];
    })
    .replace("{{types}}", types);
  if (/\{\{|需求依据|验收标准|Review|评审索引|部署|兼容性/.test(document))
    throw new Error("Learner edition contains unresolved blocks or workflow sections");
  return document;
}

function createTemplate(template) {
  template = template
    .replaceAll(
      "Compass LLM Runtime V3 完整技术设计：架构、数据库 ER、UML、推理时序、类型契约与核心执行逻辑。",
      "Compass 第13课技术方案：逻辑调用、串行尝试、共享并发闸门、输出契约与流式提交。",
    )
    .replaceAll("LLM Runtime V3 · Compass 技术设计", "可靠模型执行 · Compass 第13课技术方案")
    .replace("<span>011</span>", "<span>013</span>")
    .replace(
      "LLM Runtime<span>从模型接入，<br />到可控的每次推理。</span>",
      "可靠模型执行<span>一次逻辑调用，<br />组织每次模型执行。</span>",
    )
    .replace("V3 · 学员阅读版", "学员阅读版 · 设计示意")
    .replaceAll("2026-11-04", "2026-11-05")
    .replaceAll("2026.11.04", "2026.11.05")
    .replace("数据库 ER</a>", "数据模型</a>")
    .replace("完整 UML</a>", "组件关系</a>")
    .replace("推理请求时序</a>", "执行时序</a>")
    .replace("完整类型定义</a>", "类型与代码</a>")
    .replace("目录组织规范</a>", "变更文件</a>")
    .replace("COMPASS · LLM RUNTIME V3", "COMPASS · RELIABLE EXECUTION / LESSON 13")
    .replace("试试 RuntimeResult、取消、数据库…", "试试 ExecutionGate、Attempt、Deadline…")
    .replace("ONE REQUEST. CLEAR OWNERSHIP.", "ONE CALL. ORDERED ATTEMPTS.")
    .replace("IN-PROCESS", "EXECUTOR");

  template = requiredReplace(
    template,
    /<p>\s*在 Agent[\s\S]*?Chat。\s*<\/p>/,
    '<div class="hero-topics"><span>Logical Call</span><span>Attempt</span><span>Final</span></div>',
  );
  template = requiredReplace(
    template,
    '<a class="desktop-link" href="{{requirements}}">需求简述</a>',
    '<a class="desktop-link" href="{{directory}}">变更文件</a>',
  );
  template = requiredReplace(
    template,
    '<a class="primary-link" href="{{requirements}}">从需求开始阅读',
    '<a class="primary-link" href="{{architecture}}">查看整体架构',
  );
  template = requiredReplace(
    template,
    /<svg\s+viewBox="0 0 480 330"[\s\S]*?<\/svg>/,
    readFileSync(file("hero.svg"), "utf8").trim(),
  );
  template = requiredReplace(
    template,
    "应用管理配置与记录，Runtime 管理执行生命周期。",
    "同一快照 · 同一期限 · 有序尝试 · 一个结果",
  );
  template = requiredReplace(
    template,
    /<div class="metrics"[\s\S]*?<\/div>\n      <\/section>/,
    `<div class="metrics" aria-label="设计默认值">
      <div class="metric"><strong>2</strong><small>次生成尝试</small><p>Logical Call / 同一 Provider</p></div>
      <div class="metric"><strong>8</strong><small>个共享执行槽</small><p>ExecutionGate / K</p></div>
      <div class="metric"><strong>32</strong><small>个逻辑调用名额</small><p>ExecutionGate / N</p></div>
      <div class="metric"><strong>180</strong><small>秒总执行预算</small><p>一个绝对 Deadline</p></div>
    </div>\n      </section>`,
  );
  // 本阅读版使用设计图、表和代码；保留顶部直达导航与检索。
  template = requiredReplace(template, /<aside class="sidebar"[\s\S]*?<\/aside>/, "");
  template = requiredReplace(template, /<details class="mobile-nav">[\s\S]*?<\/details>/, "");
  template = requiredReplace(
    template,
    "{{styles}}",
    "{{styles}}\n" + readFileSync(file("reading.css"), "utf8"),
  );
  if (/LLM Runtime V3|需求简述|设计已落地|已验收|011<|Feature 993/.test(template))
    throw new Error("Template retains prior lesson content");
  return template;
}

export default {
  sourceFile: file("../TECH_DESIGN.md"),
  output: file("../TECH_DESIGN.html"),
  overviewHeading: "1.1",
  directoryHeading: "2.3.4",
  createDocument,
  createTemplate,
  graphTitles: [
    "从应用调用到单次执行",
    "Logical Call 与 Attempt 的数据关系",
    "组件职责与依赖方向",
    "两次尝试，一个逻辑调用",
    "调用状态",
    "一次失败后的决策",
    "共享容量与执行槽",
    "从流式预览到最终提交",
  ],
  graphCaptions: [
    "深绿色：本课新增的执行协调与并发控制。",
    "execution：一列 JSON，最多两条有序尝试摘要。",
    "应用服务 → 执行器 → 单次 Runtime → 协议适配器。",
    "示例路径：无正文的临时拒绝 → 退避 → 同模型重新执行。",
    "排队、尝试与退避属于 running 的内部阶段。",
    "资格、内容状态、次数与期限共同决定下一步。",
    "退避释放执行槽，逻辑调用名额继续占用。",
    "delta 进入预览；成功 final 与完整读取形成提交。",
  ],
};
