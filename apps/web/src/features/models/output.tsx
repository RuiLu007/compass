"use client";
import { CallTrace } from "./call-trace";
import { useState } from "react";
import type { ModelOutput } from "./use-model-call";
import { modelStatus } from "./types";
import { modelErrorMessage } from "@/contracts/model-errors";

function describe(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function ModelOutputPanel({
  output,
  error,
  busy,
}: {
  output: ModelOutput | null;
  error: string;
  busy: boolean;
}) {
  const [tab, setTab] = useState("text");
  const result = output?.result;
  return (
    <section className="model-output" aria-label="模型测试结果">
      <div className="section-heading">
        <h3>输出结果</h3>
        <span
          className={`status ${output?.status === "completed" ? "" : "status-muted"}`}
          role="status"
        >
          {output ? (modelStatus[output.status] ?? output.status) : "等待测试"}
        </span>
      </div>
      {output && (
        <CallTrace
          callId={output.callId}
          traceId={output.traceId}
          traceSampled={output.traceSampled}
        />
      )}
      <div className="model-tabs" role="tablist" aria-label="结果类型">
        {[
          ["text", "正文"],
          ["tools", "工具调用"],
          ["structured", "结构化结果"],
          ["details", "调用信息"],
          ["raw", "原始响应"],
        ].map(([value, label], index, tabs) => (
          <button
            type="button"
            id={`model-tab-${value}`}
            key={value}
            role="tab"
            aria-selected={tab === value}
            aria-controls={`model-panel-${value}`}
            tabIndex={tab === value ? 0 : -1}
            className={tab === value ? "active" : ""}
            onClick={() => setTab(value)}
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const next =
                event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? tabs.length - 1
                    : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
              setTab(tabs[next][0]);
              document.getElementById(`model-tab-${tabs[next][0]}`)?.focus();
            }}
          >
            {label}
            {value === "tools" && output?.tools.length ? ` (${output.tools.length})` : ""}
          </button>
        ))}
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {result?.error_code && (
        <p className="form-error" role="alert">
          {modelErrorMessage(result.error_code)}
        </p>
      )}
      <div
        className="model-result-body"
        id={`model-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`model-tab-${tab}`}
        tabIndex={0}
      >
        {tab === "text" && (
          <>
            {output?.text ? (
              <pre className="model-prose">{output.text}</pre>
            ) : (
              <div className="model-empty">
                {busy
                  ? "等待模型返回内容…"
                  : result?.kind === "decision"
                    ? "决策结果请查看“结构化结果”。"
                    : "运行一次测试，查看模型返回的内容。"}
              </div>
            )}
            {output?.thinking && (
              <p className="muted model-help">
                Thinking 已启用{output.effort ? ` · 强度 ${output.effort}` : ""}
                。仅显示推理配置与用量。
              </p>
            )}
          </>
        )}
        {tab === "tools" &&
          (output?.tools.length ? (
            output.tools.map((tool, index) => (
              <article key={tool.id || index} className="model-tool">
                <strong>{tool.function.name || "接收工具名称中…"}</strong>
                <p className="muted">{tool.id}</p>
                <pre>{tool.function.arguments || "等待参数…"}</pre>
              </article>
            ))
          ) : (
            <p className="muted">本次调用暂无工具请求。</p>
          ))}
        {tab === "structured" && (
          <>
            {result?.validation && (
              <p>
                校验状态：
                {
                  { valid: "通过", invalid: "未通过", not_requested: "未请求" }[
                    result.validation.status
                  ]
                }{" "}
                · {result.validation.mode}
              </p>
            )}
            {result?.validation?.paths?.length ? (
              <ul>
                {result.validation.paths.map((path) => (
                  <li key={path}>{path}</li>
                ))}
              </ul>
            ) : null}
            {result?.answers ? (
              Object.entries(result.answers).map(([id, answer]) => (
                <article className="model-tool" key={id}>
                  <strong>
                    {id} · {answer.type}
                  </strong>
                  {answer.choice != null && (
                    <p>
                      选项：{describe(answer.legend?.[answer.choice] ?? answer.choice)} (
                      {answer.choice})
                    </p>
                  )}
                  {answer.score != null && <p>评分：{answer.score}</p>}
                  {answer.noul != null && <p>判断值：{answer.noul}</p>}
                  {answer.confidence != null && <p>置信度：{answer.confidence}</p>}
                  {answer.probabilities && (
                    <dl>
                      {Object.entries(answer.probabilities).map(([key, value]) => (
                        <div key={key}>
                          <dt>{describe(answer.legend?.[key] ?? key)}</dt>
                          <dd>{value}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </article>
              ))
            ) : (
              <pre>{output?.text || "尚无结构化输出。"}</pre>
            )}
          </>
        )}
        {tab === "raw" &&
          (result ? (
            <>
              <p className="muted model-help">
                {result.raw_response
                  ? "供应商返回的原始 JSON 响应。"
                  : "模型测试接口返回的 JSON；流式调用展示汇总后的结果。"}
              </p>
              <pre className="model-code" aria-label="原始响应 JSON">
                {JSON.stringify(result.raw_response ?? result, null, 2)}
              </pre>
            </>
          ) : (
            <p className="muted">{busy ? "等待本次调用返回响应…" : "运行测试后显示原始响应。"}</p>
          ))}
        {tab === "details" &&
          (result ? (
            <dl className="model-call-details">
              {[
                ["调用 ID", result.call_id],
                ["配置版本", result.config_version],
                ["请求模型", result.requested_model],
                ["响应模型", result.response_model ?? "未提供"],
                ["耗时", `${result.duration_ms} ms`],
                [
                  "输入 / 输出 tokens",
                  `${result.usage?.input_tokens ?? "—"} / ${result.usage?.output_tokens ?? "—"}`,
                ],
                ["推理 tokens", result.usage?.reasoning_tokens ?? "未提供"],
                ["结束原因", result.finish_reason ?? "未提供"],
                ["上游请求 ID", result.upstream_request_id ?? "未提供"],
              ].map(([name, value]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
              {result.execution && (
                <div className="model-execution-summary">
                  <dt>执行摘要</dt>
                  <dd>
                    {result.execution.attempts?.length ?? 0} 次尝试 · 排队{" "}
                    {result.execution.queue_wait_ms} ms · {result.execution.stop_reason}
                  </dd>
                  {result.execution.attempts?.map((attempt) => (
                    <dd key={attempt.attempt_id}>
                      Attempt {attempt.number}：{modelStatus[attempt.status]} ·{" "}
                      {attempt.duration_ms} ms · {attempt.error_code ?? "无错误"} ·{" "}
                      {attempt.cleanup_confirmed ? "清理完成" : "关闭未确认"}
                    </dd>
                  ))}
                </div>
              )}
              {result.upstream_outcome_unknown && <p>上游执行结果暂未确认，可能已经产生用量。</p>}
            </dl>
          ) : (
            <p className="muted">
              {output
                ? `调用 ID：${output.callId}。调用尚未返回完整摘要。`
                : "完成调用后显示元数据。"}
            </p>
          ))}
      </div>
    </section>
  );
}
