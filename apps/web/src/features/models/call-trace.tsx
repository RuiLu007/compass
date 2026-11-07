"use client";
import { useState } from "react";
import type { TraceInfo } from "@/contracts/trace-context";

export function CallTrace({ callId, traceId, traceSampled }: TraceInfo & { callId: string }) {
  const [copied, setCopied] = useState("");
  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
    } catch {
      setCopied("复制失败，请选择编号复制。");
    }
  }
  return (
    <details className="model-call-trace">
      <summary>调用信息</summary>
      <dl className="model-call-details">
        <div>
          <dt>调用编号</dt>
          <dd>
            <code>{callId}</code>{" "}
            <button
              type="button"
              className="secondary"
              aria-label="复制调用编号"
              onClick={() => void copy(callId, "已复制")}
            >
              复制
            </button>
          </dd>
        </div>
        <div>
          <dt>观测编号</dt>
          <dd>
            {traceId ? (
              <>
                <code>{traceId}</code>{" "}
                <button
                  type="button"
                  className="secondary"
                  aria-label="复制观测编号"
                  onClick={() => void copy(traceId, "已复制")}
                >
                  复制
                </button>
              </>
            ) : (
              "调用观测未启用。"
            )}
          </dd>
        </div>
      </dl>
      {traceId && (
        <p className="muted">
          {traceSampled
            ? "本轮已采样；观测数据可能稍后可见。"
            : "本轮未采样，可使用调用编号联系管理员排查。"}
        </p>
      )}
      <span role="status">{copied}</span>
    </details>
  );
}
