"use client";
import Image from "next/image";
import { useEffect, useRef, useState, type ComponentProps, type ReactNode } from "react";
import { ApiError } from "@/client/request-scope";
import type { RunView } from "@/contracts/agent-response";
import { requestDurableControl } from "@/client/durable-analysis";
import { useDocumentSession } from "./shell/session-boundary";
import { parseModelContract } from "@/contracts/model-contract";
import type { components } from "@/generated/api";
import { AnalysisMarkdown } from "./analysis-markdown";
import { UiButton } from "./ui/button";
import { UiExecutionTimeline } from "./ui/execution-timeline";

const reasons: Record<string, string> = {
  lease_expired: "执行连接中断，正在检查恢复位置。",
  agent_restarted: "服务已恢复，正在检查执行记录。",
  worker_released: "正在释放执行资源，随后继续任务。",
  process_interrupted: "执行进程已重启，可从已保存的位置继续。",
  stream_data_lost: "增量记录不可用，请保留当前会话并联系管理员。",
  execution_unknown: "上次调用结果待核对，暂停后续执行。",
  approval_expired: "确认已过期，已保留只读分析。",
  approval_rejected: "已拒绝执行，已保留只读分析。",
  cleanup_unconfirmed: "执行资源正在核对，确认完成前无法继续。",
};
export function DurableRunActions({
  run,
  sessionId,
  scope,
  reload,
  timeline,
  children,
}: {
  run: RunView;
  sessionId: string;
  scope: { tenantId: string; workspaceId: string };
  reload: () => void;
  timeline: ComponentProps<typeof UiExecutionTimeline>;
  children: ReactNode;
}) {
  const api = useDocumentSession(),
    pending = useRef<{ kind: string; body: Record<string, unknown> } | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [resolvedWait, setResolvedWait] = useState<string>();
  const approval = Object.values(run.objects)
    .filter((object) => object.kind === "approval")
    .sort(
      (a, b) =>
        Number(a.value.status === "pending") - Number(b.value.status === "pending") ||
        (a.value.expiresAt ?? "").localeCompare(b.value.expiresAt ?? ""),
    )
    .at(-1)?.value;
  const scheduleDetail = approval?.summary.match(
    /^((?:创建|修改)「[^\r\n]+?」[。][\s\S]*?)分析内容[：:]([\s\S]+)$/,
  );
  const [wait, setWait] = useState<components["schemas"]["WaitView"] | null>(null);
  const waitId = approval?.waitId;
  const scopeQuery = new URLSearchParams(scope).toString();
  useEffect(() => {
    if (!waitId) return;
    const controller = new AbortController();
    void api
      .request(`analysis/runs/${run.runId}/waits/${waitId}?${scopeQuery}`, "GET", undefined, {
        signal: controller.signal,
        retainOnNotFound: true,
      })
      .then((value) => {
        if (!controller.signal.aborted) setWait(parseModelContract("WaitView", value));
      })
      .catch(() => {});
    return () => controller.abort();
  }, [api, run.runId, run.run?.version, scopeQuery, waitId]);
  const [clock, setClock] = useState(0);
  const pendingExpiration = approval?.status === "pending" ? approval.expiresAt : undefined;
  useEffect(() => {
    if (!pendingExpiration) return;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [pendingExpiration]);
  const value = run.run;
  const expired = !!approval?.expiresAt && Date.parse(approval.expiresAt) <= clock;
  const reason = value?.reasonCode && reasons[value.reasonCode];
  async function command(kind: string, extra: Record<string, unknown> = {}) {
    if (busy || !value) return;
    if (
      !pending.current ||
      pending.current.kind !== kind ||
      (extra.decision && pending.current.body.decision !== extra.decision)
    )
      pending.current = {
        kind,
        body: {
          ...scope,
          requestId: crypto.randomUUID(),
          expectedVersion: value.version,
          ...extra,
        },
      };
    setBusy(true);
    setError("");
    try {
      await requestDurableControl(api, sessionId, run.runId, kind, pending.current.body);
      if (kind === "decisions" && waitId) setResolvedWait(waitId);
      pending.current = null;
      reload();
    } catch (failure) {
      if (failure instanceof ApiError && [409, 410].includes(failure.status)) {
        pending.current = null;
        reload();
      }
      setError(failure instanceof Error ? failure.message : "操作未确认，请重试。");
    } finally {
      setBusy(false);
    }
  }
  const canPause = value?.status === "running";
  const canResume = value?.status === "paused" && value.reasonCode !== "queued";
  const action =
    canPause || canResume ? (
      <UiButton
        variant="quiet"
        className="analysis-process-action"
        busy={busy}
        disabled={
          canResume && (value.recoveryStatus === "blocked" || value.cleanupStatus === "unconfirmed")
        }
        aria-label={canPause ? "暂停任务" : "继续任务"}
        onClick={() => command(canPause ? "pause" : "resume")}
      >
        {canPause ? "暂停" : "继续"}
      </UiButton>
    ) : undefined;

  return (
    <>
      <UiExecutionTimeline {...timeline} action={action} />
      {reason && <p className="analysis-run-control-note">{reason}</p>}
      {value?.status === "paused" && value.reasonCode && value.reasonCode !== "queued" && (
        <details className="analysis-run-control-note">
          <summary>暂停详情</summary>
          <p>
            错误码 <code>{value.reasonCode}</code>
          </p>
          <p>
            运行 ID <code>{run.runId}</code>
          </p>
        </details>
      )}
      {children}
      {value &&
        approval?.status === "pending" &&
        approval.waitId !== resolvedWait &&
        !expired &&
        !["completed", "failed", "canceled"].includes(value.status) && (
          <section className="durable-approval" aria-label="执行确认">
            <strong>等待你的决定</strong>
            {scheduleDetail ? (
              <>
                <p className="durable-approval-summary">{scheduleDetail[1].trim()}</p>
                <details className="durable-approval-detail">
                  <summary>查看分析内容</summary>
                  <AnalysisMarkdown source={scheduleDetail[2].trim()} embedded />
                </details>
              </>
            ) : (
              <AnalysisMarkdown source={approval.summary} embedded />
            )}
            {approval.expiresAt && (
              <p className="durable-approval-expiry">
                有效期至 {new Date(approval.expiresAt).toLocaleString("zh-CN")}
              </p>
            )}
            {busy && !action && <p role="status">正在提交…</p>}
            {wait && wait.waitId === waitId && (
              <div className="actions">
                {wait.allowedActions.includes("approve") && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      command("decisions", {
                        waitId: approval.waitId,
                        proposalId: approval.proposalId,
                        proposalRevision: approval.proposalRevision,
                        proposalDigest: approval.proposalDigest,
                        decisionId: crypto.randomUUID(),
                        decision: "approve",
                      })
                    }
                  >
                    批准执行
                  </button>
                )}
                {wait.allowedActions.includes("reject") && (
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      command("decisions", {
                        waitId: approval.waitId,
                        proposalId: approval.proposalId,
                        proposalRevision: approval.proposalRevision,
                        proposalDigest: approval.proposalDigest,
                        decisionId: crypto.randomUUID(),
                        decision: "reject",
                      })
                    }
                  >
                    拒绝执行
                  </button>
                )}
                {wait.allowedActions.includes("reconcile") && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      command("reconcile", {
                        waitId: approval.waitId,
                        actionId: approval.actionId,
                      })
                    }
                  >
                    核对执行结果
                  </button>
                )}
              </div>
            )}
          </section>
        )}
      {Object.values(run.objects)
        .filter((o) => o.kind === "artifact")
        .map(({ value: artifact }) => {
          const href = `/api/web/v1/analysis/runs/${run.runId}/artifacts/${artifact.id}?${new URLSearchParams(scope)}`;
          if (artifact.mediaType === "image/png")
            return (
              <figure className="analysis-chart" key={artifact.id}>
                <Image src={href} unoptimized width={768} height={512} alt="Python 分析图表" />
                <figcaption>
                  <span>分析图表 · PNG · {Math.ceil(artifact.byteSize / 1024)} KiB</span>
                  <a href={href} download={artifact.name}>
                    下载图表
                  </a>
                </figcaption>
              </figure>
            );
          return (
            <p key={artifact.id}>
              <a href={href} download={artifact.name}>
                下载数据 · {artifact.name}
              </a>
            </p>
          );
        })}
      {error && (
        <p role="alert">
          {error}
          <button
            className="secondary"
            disabled={busy}
            onClick={() => pending.current && command(pending.current.kind)}
          >
            重试操作
          </button>
          <button className="secondary" onClick={reload}>
            刷新状态
          </button>
        </p>
      )}
    </>
  );
}
