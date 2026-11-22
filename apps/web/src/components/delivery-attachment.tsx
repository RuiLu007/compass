"use client";

import { useEffect, useRef, useState } from "react";
import type { components } from "@/generated/api";
import { parseModelContract } from "@/contracts/model-contract";
import { AnalysisMarkdown } from "./analysis-markdown";
import { UiButton } from "./ui/button";
import { Modal } from "./ui/dialog";

type Dashboard = components["schemas"]["DashboardDocumentV1"];
type Widget = Dashboard["widgets"][number];

function DataWidget({ widget, filter }: { widget: Widget; filter: string }) {
  const rows = widget.rows.filter(
    (row) => !filter || row.some((v) => String(v ?? "").includes(filter)),
  );
  const values = rows.map((row) => (row[1] == null || row[1] === "" ? NaN : Number(row[1])));
  const canChart =
    ["line", "bar"].includes(widget.kind) && values.length > 0 && values.every(Number.isFinite);
  const lo = Math.min(0, ...values),
    hi = Math.max(0, ...values);
  const y = (v: number) => 108 - ((v - lo) / (hi - lo || 1)) * 100;
  const x = (i: number) => 10 + (i + 0.5) * (480 / rows.length);
  return (
    <section aria-label={widget.title}>
      <h3>{widget.title}</h3>
      {!rows.length ? (
        <p>当前筛选没有可显示的数据。</p>
      ) : (
        <>
          {canChart && (
            <svg
              viewBox="0 0 500 118"
              width="100%"
              height="150"
              role="img"
              aria-label={widget.title + "，具体数值见下表"}
            >
              <line x1="5" x2="495" y1={y(0)} y2={y(0)} stroke="var(--muted)" />
              {widget.kind === "line" ? (
                <polyline
                  fill="none"
                  stroke="var(--primary)"
                  strokeWidth="2"
                  points={values.map((v, i) => x(i) + "," + y(v)).join(" ")}
                />
              ) : (
                values.map((v, i) => (
                  <rect
                    key={i}
                    x={x(i) - 140 / rows.length}
                    y={Math.min(y(v), y(0))}
                    width={280 / rows.length}
                    height={Math.max(1, Math.abs(y(v) - y(0)))}
                    fill="var(--primary)"
                  />
                ))
              )}
            </svg>
          )}
          <div
            className="analysis-markdown-table"
            tabIndex={0}
            role="region"
            aria-label={widget.title}
          >
            <table>
              <thead>
                <tr>
                  {widget.columns.map((col, i) => (
                    <th key={i} scope="col">
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((v, j) => (
                      <td key={j}>{v === null ? "—" : String(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

export function DeliveryAttachment({
  href,
  name,
  digest,
  dashboard,
}: {
  href: string;
  name: string;
  digest: string;
  dashboard: boolean;
}) {
  const [open, setOpen] = useState(false),
    [filter, setFilter] = useState("");
  const [preview, setPreview] = useState<Dashboard | string | null>(null),
    [error, setError] = useState("");
  const opener = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    let active: AbortController | null = null;
    async function load() {
      active?.abort();
      const controller = new AbortController();
      active = controller;
      setPreview(null);
      setError("");
      try {
        const res = await fetch(href, {
          cache: "no-store",
          signal: controller.signal,
          credentials: "same-origin",
        });
        if (!res.ok)
          throw new Error(
            res.status === 403
              ? "当前账号无法读取这份产物。请联系工作区管理员申请产物及其来源数据的读取权限。"
              : "读取失败，请重试。",
          );
        const bytes = await res.arrayBuffer();
        const checksum = Array.from(
          new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
          (b) => b.toString(16).padStart(2, "0"),
        ).join("");
        if (checksum !== digest) throw new Error("文件已变更，请刷新会话后重试。");
        const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
        const value = dashboard
          ? parseModelContract<Dashboard>("DashboardDocumentV1", JSON.parse(text))
          : text;
        if (!controller.signal.aborted) setPreview(value);
      } catch (e) {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "读取失败，请重试。");
      }
    }
    function visibility() {
      if (document.visibilityState === "visible") void load();
      else {
        active?.abort();
        setPreview(null);
      }
    }
    void load();
    window.addEventListener("focus", load);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      active?.abort();
      window.removeEventListener("focus", load);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [href, open, digest, dashboard]);
  const label = dashboard ? "看板" : "报告";
  return (
    <div>
      <button
        type="button"
        className="secondary"
        ref={opener}
        onClick={() => {
          setFilter("");
          setPreview(null);
          setError("");
          setOpen(true);
        }}
      >
        打开{label}
      </button>{" "}
      <a href={href} download={name}>
        下载{label}
      </a>
      {open && (
        <Modal
          title={label}
          wide
          open
          onClose={() => setOpen(false)}
          returnFocus={() => opener.current}
        >
          {error ? (
            <p role="alert">
              {error} <UiButton onClick={() => setOpen(false)}>关闭</UiButton>
            </p>
          ) : preview === null ? (
            <p role="status">正在读取…</p>
          ) : typeof preview === "string" ? (
            <AnalysisMarkdown source={preview} embedded />
          ) : (
            <div className="analysis-markdown">
              <h2>{preview.title}</h2>
              <p>
                {preview.scope} · 快照创建于 {preview.asOf}
              </p>
              <label>
                筛选当前快照{" "}
                <input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} />
              </label>
              {preview.widgets.map((w, i) => (
                <DataWidget key={i} widget={w} filter={filter} />
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
