"use client";

import { useEffect, useRef, useState } from "react";
import { Play, Square, FlaskConical, CheckCircle2, CircleAlert } from "lucide-react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { parseModelContract } from "@/contracts/model-contract";
import { modelErrorMessage } from "@/contracts/model-errors";
import type { components } from "@/generated/api";
import { modelStatus } from "./types";

type Catalog = components["schemas"]["ShowcaseCatalog"];
type Report = components["schemas"]["ShowcaseReport"];
type Event = components["schemas"]["ShowcaseEvent"];
type Progress = Extract<Event, { type: "progress" }>;

export function ReliabilityPanel() {
  const api = useDocumentSession();
  const [catalog, setCatalog] = useState<Catalog>();
  const [scenario, setScenario] =
    useState<components["schemas"]["ReliabilityScenario"]>("rate_limit");
  const [report, setReport] = useState<Report>();
  const [events, setEvents] = useState<Progress[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  useEffect(() => {
    let live = true;
    const clear = () => {
      epoch.current++;
      active.current?.abort();
      active.current = null;
      setCatalog(undefined);
      setEvents([]);
      setReport(undefined);
      setBusy(false);
    };
    const remove = api.onClear(clear);
    void api
      .request<unknown>("platform/models/reliability/scenarios")
      .then((value) => {
        if (live) setCatalog(parseModelContract<Catalog>("ShowcaseCatalog", value));
      })
      .catch(() => {
        if (live) setError("演示场景暂时无法加载，请刷新页面。");
      });
    return () => {
      live = false;
      remove();
      clear();
    };
  }, [api]);

  async function run() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    const current = ++epoch.current;
    setReport(undefined);
    setEvents([]);
    setError("");
    setBusy(true);
    let pending: Report | undefined;
    try {
      await api.modelRequest<Report, Event>(
        "platform/models/reliability/runs",
        { call_id: crypto.randomUUID(), scenario },
        {
          signal: controller.signal,
          parseResult: (value) => parseModelContract<Report>("ShowcaseReport", value),
          parseEvent: (value) => parseModelContract<Event>("ShowcaseEvent", value),
          onEvent(event) {
            if (epoch.current !== current || !api.retainsContent) return;
            if (event.type === "progress") setEvents((items) => [...items, event].slice(-240));
            if (event.type === "final") pending = event;
          },
        },
      );
      if (epoch.current === current && api.retainsContent && pending) setReport(pending);
    } catch (cause) {
      if (epoch.current === current && api.retainsContent)
        setError(
          controller.signal.aborted
            ? "演示已停止，已收到的片段仅供预览。"
            : cause instanceof Error
              ? cause.message
              : "演示暂时失败，请重试。",
        );
    } finally {
      if (epoch.current === current) {
        active.current = null;
        setBusy(false);
      }
    }
  }
  const selected = catalog?.items.find((item) => item.id === scenario);
  const capacity = report?.capacity ?? events.at(-1)?.capacity;
  const preview = events
    .filter((event) => event.text)
    .map((event) => event.text)
    .join("");
  return (
    <section className="panel reliability-panel" aria-label="可靠性模拟测试" aria-busy={busy}>
      <div className="reliability-heading">
        <div>
          <p className="eyebrow">
            <FlaskConical size={15} aria-hidden="true" /> RELIABILITY LAB
          </p>
          <h3>可靠性模拟测试</h3>
          <p className="muted">选择故障场景，观察一次逻辑调用如何恢复、停止和释放资源。</p>
        </div>
        <span className="status status-muted">模拟供应商 · 无模型费用</span>
      </div>
      <div className="reliability-controls">
        <label>
          演示场景
          <select
            aria-label="可靠性演示场景"
            value={scenario}
            disabled={busy || !catalog}
            onChange={(event) => setScenario(event.target.value as typeof scenario)}
          >
            {catalog?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
        <button disabled={busy || !catalog} onClick={() => void run()}>
          <Play size={16} aria-hidden="true" />
          运行场景
        </button>
        {busy && (
          <button className="secondary" onClick={() => active.current?.abort()}>
            <Square size={15} aria-hidden="true" />
            停止演示
          </button>
        )}
      </div>
      {selected && (
        <div className="reliability-expectation">
          <strong>{selected.description}</strong>
          <span>预期：{selected.expected}</span>
        </div>
      )}
      <div className="reliability-metrics">
        {[
          [
            "实际尝试",
            report
              ? report.calls.reduce(
                  (sum, call) => sum + (call.result.execution?.attempts?.length ?? 0),
                  0,
                )
              : "—",
          ],
          ["可提交结果", report ? report.calls.filter((call) => call.accepted).length : "—"],
          ["活跃峰值 / 上限", capacity ? `${capacity.peak_active} / ${capacity.capacity}` : "—"],
          [
            "逻辑峰值 / 上限",
            capacity ? `${capacity.peak_inflight} / ${capacity.max_inflight}` : "—",
          ],
          ["可用 / 隔离槽", capacity ? `${capacity.available} / ${capacity.quarantined}` : "—"],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <p role="status" className="reliability-result">
        {busy ? (
          "正在执行场景…"
        ) : report ? (
          <>
            {report.passed ? (
              <CheckCircle2 size={18} aria-hidden="true" />
            ) : (
              <CircleAlert size={18} aria-hidden="true" />
            )}
            {report.passed ? "场景符合预期" : "场景与预期不符"} · {report.duration_ms} ms
          </>
        ) : (
          "等待运行"
        )}
      </p>
      {(events.length > 0 || report) && (
        <div className="reliability-live">
          <div>
            <h4>实时事件</h4>
            <ol aria-label="演示事件记录">
              {events.map((event, index) => (
                <li key={index}>
                  <span>调用 {event.number}</span>
                  {event.message}
                </li>
              ))}
            </ol>
          </div>
          <div>
            <h4>输出预览</h4>
            <pre className="model-prose" aria-label="演示输出预览">
              {preview || "尚无正文分片"}
            </pre>
            <p className="muted">片段实时展示；完整成功后才成为可提交结果。</p>
          </div>
        </div>
      )}
      {report && (
        <div className="reliability-calls">
          {report.calls.map((call) => (
            <details key={call.number} open={report.calls.length === 1}>
              <summary>
                调用 {call.number} · {modelStatus[call.result.status]} · {call.upstream_requests}{" "}
                次供应商请求
                {call.result.error_code ? ` · ${modelErrorMessage(call.result.error_code)}` : ""}
              </summary>
              <p>
                结束原因：{call.result.execution?.stop_reason} · 排队{" "}
                {call.result.execution?.queue_wait_ms} ms · 输入 / 输出用量：
                {call.result.usage?.input_tokens ?? "未知"} /{" "}
                {call.result.usage?.output_tokens ?? "未知"} ·{" "}
                {call.accepted ? "结果可提交" : "结果未提交"}
              </p>
              {call.result.execution?.attempts?.map((attempt) => (
                <div className="reliability-attempt" key={attempt.attempt_id}>
                  <strong>Attempt {attempt.number}</strong>
                  <span>
                    {modelStatus[attempt.status]} · {attempt.duration_ms} ms
                  </span>
                  <span>{attempt.error_code ?? "无错误"}</span>
                  <span>{attempt.cleanup_confirmed ? "资源清理已确认" : "资源关闭未确认"}</span>
                </div>
              ))}
              {call.result.upstream_outcome_unknown && <p>上游结果未知，可能已产生用量。</p>}
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
