"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, Database, Download, FileSpreadsheet, Sparkles } from "lucide-react";
import type {
  AnalysisFileView,
  AnalysisStepView,
  AnalysisToolParams,
  AnalysisToolView,
  AnalysisTurnView,
} from "@/client/analysis-run";
import { CallTrace } from "@/features/models/call-trace";

type ReadyFile = Extract<AnalysisFileView, { status: "ready" }>;

const runErrors: Record<string, string> = {
  model_failed: "模型调用失败，本轮分析未完成。",
  model_incomplete: "模型返回结果不完整，本轮分析未完成。",
  model_unavailable: "所选模型当前不可用，请重新选择后再试。",
  run_timeout: "本轮分析已超时，请缩小问题范围后重试。",
  context_limit: "当前对话上下文过长，请新建对话后重试。",
  step_limit: "本轮已达到最大分析轮数，未能形成有效结论。",
  internal_error: "Agent 执行遇到异常，请稍后重试。",
};

const toolErrors: Record<string, string> = {
  unknown_tool: "模型请求了未开放的工具。",
  invalid_parameters: "查询参数不符合当前数据范围。",
  retail_unavailable: "当前工作区未开放 Retail 查询。",
  retail_query_failed: "Retail 查询失败。",
  retail_query_timeout: "Retail 查询超时。",
  result_limit: "查询结果超过本轮限制。",
};

function runState(turn: AnalysisTurnView) {
  if (turn.terminal?.status === "completed") return { label: "已完成", tone: "success" as const };
  if (turn.terminal?.status === "failed") return { label: "执行失败", tone: "error" as const };
  if (turn.terminal?.status === "canceled") return { label: "已终止", tone: "neutral" as const };
  if (turn.terminal?.status === "cleanup_unconfirmed")
    return { label: "终止清理失败", tone: "error" as const };
  if (turn.phase === "interrupted" && turn.acknowledgedTerminalStatus)
    return { label: "终态结果流不完整", tone: "error" as const };
  if (turn.phase === "interrupted")
    return turn.stopState === "idle"
      ? { label: "连接中断", tone: "error" as const }
      : { label: "终止结果待确认", tone: "waiting" as const };
  if (turn.stopState === "unconfirmed")
    return { label: "终止请求未确认", tone: "waiting" as const };
  if (turn.stopState === "already_terminal")
    return { label: "已结束，等待结果", tone: "waiting" as const };
  if (turn.stopState !== "idle") return { label: "正在终止", tone: "waiting" as const };
  return { label: "执行中", tone: "active" as const };
}

function toolState(tool: AnalysisToolView) {
  switch (tool.status) {
    case "running":
      return { label: "执行中", tone: "active" as const };
    case "ok":
      return { label: "成功", tone: "success" as const };
    case "empty":
      return { label: "无数据", tone: "neutral" as const };
    case "error":
      return { label: "失败", tone: "error" as const };
    case "canceled":
      return { label: "已终止", tone: "neutral" as const };
    case "incomplete":
      return { label: "结果待确认", tone: "waiting" as const };
  }
}

function dateRange(period: { start: string; end: string }) {
  return `${period.start} 至 ${period.end}`;
}

function toolTitle(tool: AnalysisToolView) {
  if (!tool.params) return "校验工具调用";
  return tool.params.dimension === "channel"
    ? "比较渠道实付金额"
    : `查询 ${tool.params.channelCode} 渠道商品明细`;
}

function paramsRows(params: AnalysisToolParams) {
  if (!params) return [["参数", "未通过校验"]] as const;
  return [
    ["时段", dateRange(params.period)],
    ...(params.compareTo ? [["对比时段", dateRange(params.compareTo)]] : []),
    ["维度", params.dimension === "channel" ? "渠道" : "商品"],
    ...(params.dimension === "product" ? [["渠道", params.channelCode]] : []),
  ];
}

function resultText(tool: AnalysisToolView) {
  if (tool.status === "running") return "查询正在执行。";
  if (tool.status === "ok") return `返回 ${tool.rowCount ?? 0} 条记录。`;
  if (tool.status === "empty") return "查询成功，没有匹配记录。";
  if (tool.status === "error") return toolErrors[tool.errorCode ?? ""] ?? "工具调用失败。";
  if (tool.status === "canceled") return "本次调用已停止。";
  return "尚未确认本次调用结果。";
}

function ToolCard({ tool, step }: { tool: AnalysisToolView; step: AnalysisStepView }) {
  const state = toolState(tool);
  return (
    <details className="analysis-tool-card" open={tool.status === "running" || undefined}>
      <summary>
        <span className="analysis-tool-icon">
          <Database size={18} aria-hidden="true" />
        </span>
        <span className="analysis-tool-title">
          <strong>{toolTitle(tool)}</strong>
          <small>第 {step.stepIndex} 轮 · Retail 查询</small>
        </span>
        <span className="analysis-status-chip" data-tone={state.tone}>
          {state.label}
        </span>
        <ChevronDown className="analysis-tool-caret" size={16} aria-hidden="true" />
      </summary>
      <dl className="analysis-tool-data">
        {paramsRows(tool.params).map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
        <div>
          <dt>结果</dt>
          <dd>{resultText(tool)}</dd>
        </div>
        <div>
          <dt>调用编号</dt>
          <dd>
            <code>{tool.toolCallId}</code>
          </dd>
        </div>
      </dl>
    </details>
  );
}

function TurnNotice({ turn }: { turn: AnalysisTurnView }) {
  let title = "";
  let detail = "";
  let tone: "neutral" | "waiting" | "error" = "neutral";
  if (turn.terminal?.status === "failed") {
    title = "本轮分析未完成";
    detail = runErrors[turn.terminal.errorCode] ?? "本轮分析执行失败。";
    tone = "error";
  } else if (turn.terminal?.status === "canceled") {
    title = "任务已终止";
    detail = "服务端已确认在途工作退出和资源清理。已完成的工具过程仍可查看。";
  } else if (turn.terminal?.status === "cleanup_unconfirmed") {
    title = "终止清理失败";
    detail = "任务已停止派发后续步骤，但服务端未能确认全部执行资源已完成清理。";
    tone = "error";
  } else if (turn.errorMessage) {
    title = turn.acknowledgedTerminalStatus
      ? "服务端终态已确认，但结果流不完整"
      : turn.stopState === "idle"
        ? "运行连接已中断"
        : "终止结果待确认";
    detail = turn.errorMessage;
    tone = turn.stopState === "idle" || turn.acknowledgedTerminalStatus ? "error" : "waiting";
  }
  if (!title) return null;
  return (
    <section className="analysis-run-notice" data-tone={tone} role="status">
      <strong>{title}</strong>
      <p>{detail}</p>
    </section>
  );
}

function AnalysisFileCard({ file, onDownload }: { file: ReadyFile; onDownload: () => void }) {
  return (
    <div className="analysis-file-card">
      <span className="analysis-file-icon">
        <FileSpreadsheet size={19} aria-hidden="true" />
      </span>
      <span className="analysis-file-name">
        <strong>{file.filename}</strong>
        <small>
          CSV · {file.rowCount} 条记录 · {Math.max(1, Math.ceil(file.byteLength / 1024))} KiB
        </small>
      </span>
      <button type="button" className="analysis-file-download" onClick={onDownload}>
        <Download size={17} aria-hidden="true" />
        下载
      </button>
    </div>
  );
}

export function AnalysisRunTurn({
  turn,
  onDownload,
}: {
  turn: AnalysisTurnView;
  onDownload: (file: ReadyFile) => void;
}) {
  const processId = useId();
  const toggle = useRef<HTMLButtonElement>(null);
  const process = useRef<HTMLDivElement>(null);
  const collapsedRun = useRef<string | undefined>(undefined);
  const [expanded, setExpanded] = useState(true);
  const processSteps = turn.steps.filter((step) => step.tools.length > 0);
  const modelOnlySteps = turn.steps.filter((step) => step.tools.length === 0);
  const hasProcess = processSteps.length > 0;
  const state = runState(turn);

  useEffect(() => {
    const runId = turn.runId;
    if (
      !hasProcess ||
      !runId ||
      turn.terminal?.status !== "completed" ||
      collapsedRun.current === runId
    )
      return;
    collapsedRun.current = runId;
    if (process.current?.contains(document.activeElement)) toggle.current?.focus();
    setExpanded(false);
  }, [hasProcess, turn.runId, turn.terminal?.status]);

  const completed = turn.terminal?.status === "completed" ? turn.terminal : undefined;
  const readyFile = completed?.file.status === "ready" ? completed.file : undefined;

  return (
    <article className="model-chat-turn" data-run-id={turn.runId}>
      <p className="model-chat-question">{turn.question}</p>
      <div className="model-chat-answer analysis-run-answer">
        <header className="analysis-run-header">
          <span className="analysis-run-icon">
            <Sparkles size={18} aria-hidden="true" />
          </span>
          <strong>Compass</strong>
          <span className="analysis-run-model">{turn.modelLabel}</span>
          <span className="analysis-status-chip" data-tone={state.tone} role="status">
            {state.label}
          </span>
        </header>

        {hasProcess && (
          <section className="analysis-process">
            <button
              ref={toggle}
              type="button"
              className="analysis-process-toggle"
              aria-expanded={expanded}
              aria-controls={processId}
              onClick={() => setExpanded((value) => !value)}
            >
              <ChevronDown size={17} aria-hidden="true" />
              <span>分析过程</span>
              <small>{processSteps.length} 轮工具调用</small>
            </button>
            <div ref={process} id={processId} className="analysis-process-body" hidden={!expanded}>
              {processSteps.map((step) => (
                <section className="analysis-step" key={step.stepId}>
                  <header>
                    <strong>第 {step.stepIndex} 轮</strong>
                    <span>模型调用 {step.modelCallId}</span>
                  </header>
                  <CallTrace callId={step.modelCallId} />
                  {step.tools.map((tool) => (
                    <ToolCard key={tool.toolCallId} tool={tool} step={step} />
                  ))}
                </section>
              ))}
            </div>
          </section>
        )}

        {completed && (
          <section className="analysis-final-answer" aria-label="最终回答">
            <h3>最终回答</h3>
            <pre className="model-prose">{completed.finalAnswer}</pre>
            {readyFile && (
              <AnalysisFileCard file={readyFile} onDownload={() => onDownload(readyFile)} />
            )}
            {completed.file.status === "unavailable" && (
              <p className="analysis-file-warning" role="status">
                {completed.file.code === "file_limit"
                  ? "CSV 超过本轮交付限制，分析结论已保留。"
                  : "CSV 生成失败，分析结论已保留。"}
              </p>
            )}
          </section>
        )}

        {modelOnlySteps.length > 0 && (
          <section className="analysis-model-steps" aria-label="模型调用信息">
            {modelOnlySteps.map((step) => (
              <div className="analysis-model-step" key={step.stepId}>
                <span>第 {step.stepIndex} 轮模型调用</span>
                <CallTrace callId={step.modelCallId} />
              </div>
            ))}
          </section>
        )}

        <TurnNotice turn={turn} />
      </div>
    </article>
  );
}
