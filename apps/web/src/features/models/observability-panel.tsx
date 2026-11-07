"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import type { components } from "@/generated/api";
import type { Provider } from "./types";

type Status = components["schemas"]["ObservabilityStatus"] & { project_url?: string | null };
type Comparison = components["schemas"]["ModelComparison"];
const display = (value: number | null | undefined) =>
  value == null ? "未提供" : value.toLocaleString("zh-CN", { maximumFractionDigits: 6 });

export function ObservabilityPanel({ providers }: { providers: Provider[] }) {
  const api = useDocumentSession();
  const [status, setStatus] = useState<Status>();
  const [rows, setRows] = useState<Comparison>();
  const [models, setModels] = useState<[string, string]>(["", ""]);
  const [window, setWindow] = useState("24h");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const lifecycle = useRef(0);
  const invalidate = useCallback(() => {
    lifecycle.current++;
  }, []);
  async function refresh() {
    const epoch = lifecycle.current;
    setBusy(true);
    setError("");
    try {
      const result = await api.request<Status>("platform/models/observability");
      if (epoch === lifecycle.current) setStatus(result);
    } catch {
      if (epoch === lifecycle.current) setError("暂时无法读取观测状态，请重新加载。");
    } finally {
      if (epoch === lifecycle.current) setBusy(false);
    }
  }
  useEffect(() => {
    let live = true;
    void api
      .request<Status>("platform/models/observability")
      .then((value) => {
        if (live) setStatus(value);
      })
      .catch(() => {
        if (live) setError("暂时无法读取观测状态，请重新加载。");
      });
    const clear = api.onClear(() => {
      invalidate();
      setStatus(undefined);
      setRows(undefined);
    });
    return () => {
      invalidate();
      live = false;
      clear();
    };
  }, [api, invalidate]);
  async function compare() {
    const epoch = lifecycle.current;
    setBusy(true);
    setError("");
    setStale(true);
    try {
      const result = await api.request<Comparison>(
        `platform/models/observability/summary?${new URLSearchParams({ provider_ids: models.join(","), window })}`,
      );
      if (epoch === lifecycle.current) {
        setRows(result);
        setStale(result.items.some((row) => row.data_state === "unavailable"));
      }
    } catch {
      if (epoch === lifecycle.current) setError("暂时无法读取模型对比，请重试。");
    } finally {
      if (epoch === lifecycle.current) setBusy(false);
    }
  }
  return (
    <section className="panel model-observability" aria-label="调用观测">
      <div className="section-heading">
        <h3>调用观测</h3>
        <div className="model-actions">
          {status?.project_url ? (
            <a
              className="button secondary"
              href={status.project_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              打开 Langfuse
            </a>
          ) : (
            <button type="button" className="secondary" disabled>
              打开 Langfuse
            </button>
          )}
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => void refresh()}
          >
            刷新状态
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {status && (
        <>
          <p className="muted">
            当前实例：<code>{status.instance_id}</code> · 环境 {status.environment} · 采样率{" "}
            {display(status.sample_ratio)}
          </p>
          {!status.enabled ? (
            <p>调用观测未启用。</p>
          ) : !status.configured ? (
            <p>尚未配置 Langfuse 入口，请联系平台管理员。</p>
          ) : (
            <p>
              本实例待导出 {status.queue_size}/{status.queue_capacity} · 丢弃 {status.dropped} ·
              导出失败 {status.export_failures}
            </p>
          )}
          {status.last_export === "failure" && (
            <p role="status">观测导出异常，模型调用继续运行；部分观测数据可能缺失。</p>
          )}
        </>
      )}
      <form
        className="model-filters"
        onSubmit={(event) => {
          event.preventDefault();
          void compare();
        }}
      >
        {([0, 1] as const).map((index) => (
          <label key={index}>
            模型 {index === 0 ? "A" : "B"}
            <select
              aria-label={`对比模型${index === 0 ? "A" : "B"}`}
              value={models[index]}
              onChange={(event) =>
                setModels((old) =>
                  index === 0 ? [event.target.value, old[1]] : [old[0], event.target.value],
                )
              }
            >
              <option value="">选择模型</option>
              {providers.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.alias} · {model.api_model}
                </option>
              ))}
            </select>
          </label>
        ))}
        <label>
          时间范围
          <select
            aria-label="观测时间范围"
            value={window}
            onChange={(event) => setWindow(event.target.value)}
          >
            <option value="1h">最近1小时</option>
            <option value="24h">最近24小时</option>
            <option value="7d">最近7天</option>
          </select>
        </label>
        <button
          type="submit"
          disabled={
            busy || !status?.configured || !models[0] || !models[1] || models[0] === models[1]
          }
        >
          查询
        </button>
      </form>
      {busy && <p role="status">正在读取观测数据…</p>}
      {rows && (
        <>
          <p className="muted">
            {new Date(rows.from_timestamp).toLocaleString()} 至{" "}
            {new Date(rows.to_timestamp).toLocaleString()} · 环境 {rows.environment} ·
            跨实例聚合，观测数据可能缺失
          </p>
          {stale && <p role="status">查询未完成，当前结果可能非最新。</p>}
          <div className="model-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>模型</th>
                  <th>逻辑调用 / 尝试</th>
                  <th>成功率</th>
                  <th>平均耗时</th>
                  <th>Token 已知部分</th>
                  <th>估算费用（USD）</th>
                </tr>
              </thead>
              <tbody>
                {rows.items.map((row) => (
                  <tr key={row.provider_id}>
                    <td>{row.model}</td>
                    <td>
                      {display(row.logical_count)} / {display(row.attempt_count)}
                    </td>
                    <td>
                      {row.success_rate == null
                        ? "未提供"
                        : `${(row.success_rate * 100).toFixed(1)}%`}
                    </td>
                    <td>{display(row.mean_duration_ms)} ms</td>
                    <td>
                      {display(row.known_input_tokens)} / {display(row.known_output_tokens)}
                      <br />
                      未知尝试：{display(row.unknown_usage_attempts)}
                    </td>
                    <td>
                      {row.data_state === "unavailable" ? (
                        "暂时无法读取模型对比，请重试。"
                      ) : (
                        <>
                          {display(row.estimated_usd ?? row.known_estimated_usd)}
                          {row.unknown_cost_attempts
                            ? `（已知部分，${row.unknown_cost_attempts} 次未知）`
                            : ""}
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.items.every((row) => row.data_state === "empty") && <p>所选范围暂无调用数据。</p>}
        </>
      )}
    </section>
  );
}
