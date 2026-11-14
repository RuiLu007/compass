"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ArrowUpRight, CheckCircle2, CircleAlert } from "lucide-react";
import Markdown from "react-markdown";
import type { AnalysisRunScope } from "@/client/analysis-run";
import { ApiError } from "@/client/request-scope";
import type { components } from "@/generated/api";
import { AppLink } from "./shell/app-link";
import { NavigationIcon } from "./shell/navigation-icons";
import { useDocumentSession } from "./shell/session-boundary";
import { UiButton } from "./ui/button";
import { UiCodeBlock } from "./ui/code-block";
import { UiDisclosure } from "./ui/disclosure";

type Draft = components["schemas"]["WorkflowDraftView"];
type Asset = components["schemas"]["WorkflowAssetView"];

function legacyPeriods(args: Draft["args"]) {
  const { baseline, current } = args;
  if (
    baseline &&
    typeof baseline === "object" &&
    current &&
    typeof current === "object" &&
    "start" in baseline &&
    typeof baseline.start === "string" &&
    "end" in baseline &&
    typeof baseline.end === "string" &&
    "start" in current &&
    typeof current.start === "string" &&
    "end" in current &&
    typeof current.end === "string"
  )
    return {
      baseline: { start: baseline.start, end: baseline.end },
      current: { start: current.start, end: current.end },
    };
}

export function WorkflowSaveCard({
  draftId,
  persisted,
  scope,
  pluginHref,
}: {
  draftId: string;
  persisted?: components["schemas"]["PublicWorkflow"];
  scope: AnalysisRunScope;
  pluginHref?: string;
}) {
  const api = useDocumentSession();
  const [draft, setDraft] = useState<Draft>();
  const [asset, setAsset] = useState<Asset>();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(0);
  useEffect(() => {
    const tick = () => setClock(Date.now());
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);
  const nameId = useId();
  const nameInput = useRef<HTMLInputElement>(null);
  const query = new URLSearchParams(scope);
  const path = `analysis/workflows/drafts/${encodeURIComponent(draftId)}`;
  const options = { retainOnNotFound: true };

  useEffect(() => {
    if (draft) nameInput.current?.focus({ preventScroll: true });
  }, [draft]);

  async function open() {
    setBusy(true);
    setError("");
    try {
      const detail = await api.request<Draft>(`${path}?${query}`, "GET", undefined, options);
      setDraft(detail);
      setName(detail.name);
    } catch (failure) {
      setError(
        failure instanceof ApiError && [404, 410].includes(failure.status)
          ? "保存邀请已失效，请重新运行分析。"
          : "暂时无法打开工作流详情，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!draft || !name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const saved = await api.request<Asset>(
        `${path}/confirm?${query}`,
        "POST",
        { name: name.trim() },
        options,
      );
      setAsset(saved);
    } catch (failure) {
      setError(
        failure instanceof ApiError && [404, 410].includes(failure.status)
          ? "保存邀请已失效，请重新运行分析。"
          : "工作流保存失败，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }

  if (dismissed) return null;
  const dynamic = draft?.entryContract === "agent_workflow@1";
  const periods = draft && !dynamic ? legacyPeriods(draft.args) : undefined;
  const savedName = asset?.name ?? (persisted?.status === "saved" ? persisted.draft.name : null);
  const expired =
    !savedName &&
    persisted &&
    (persisted.status === "expired" || Date.parse(persisted.expiresAt) <= clock);
  return (
    <section className="ui-panel workflow-save-card" aria-label="保存工作流">
      {savedName ? (
        <div className="workflow-save-success">
          <p className="ui-notice" data-tone="success" role="status">
            <CheckCircle2 size={17} aria-hidden="true" />
            <span>已保存「{savedName}」，模型可以在后续分析中按任务需要调用。</span>
          </p>
          {pluginHref && (
            <AppLink
              className="ui-link-button"
              href={pluginHref}
              target="_blank"
              rel="noopener noreferrer"
            >
              在新标签页打开插件 → Workflow
              <ArrowUpRight size={15} aria-hidden="true" />
            </AppLink>
          )}
        </div>
      ) : expired ? (
        <p className="ui-notice" role="status">
          保存邀请已失效，分析结果与编排脚本已保留。
        </p>
      ) : (
        <>
          <header className="workflow-save-header">
            <span className="workflow-save-icon">
              <NavigationIcon name="workflow" size={20} />
            </span>
            <div>
              <h3>要把本次编排保存为可复用的工作流吗？</h3>
              <p className="ui-panel-description">保存后，模型可以在后续相关分析中选择该工作流。</p>
            </div>
          </header>
          {draft ? (
            <div className="workflow-save-detail">
              <div className="ui-field workflow-save-name">
                <label htmlFor={nameId}>工作流名称</label>
                <input
                  ref={nameInput}
                  id={nameId}
                  className="ui-input"
                  value={name}
                  maxLength={80}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              {dynamic && (
                <>
                  <p className="ui-panel-description">{draft.description}</p>
                  <UiDisclosure label="查看工具参数">
                    <UiCodeBlock
                      title="函数参数契约"
                      language="JSON"
                      code={JSON.stringify(draft.parameters, null, 2)}
                    />
                    <UiCodeBlock
                      title="试跑入参"
                      language="JSON"
                      code={JSON.stringify(draft.args, null, 2)}
                    />
                  </UiDisclosure>
                </>
              )}
              {periods && (
                <div className="workflow-save-scope">
                  <dl>
                    <div>
                      <dt>基准期</dt>
                      <dd>
                        <time dateTime={periods.baseline.start}>{periods.baseline.start}</time>
                        {" 至 "}
                        <time dateTime={periods.baseline.end}>{periods.baseline.end}</time>
                      </dd>
                    </div>
                    <div>
                      <dt>本期</dt>
                      <dd>
                        <time dateTime={periods.current.start}>{periods.current.start}</time>
                        {" 至 "}
                        <time dateTime={periods.current.end}>{periods.current.end}</time>
                      </dd>
                    </div>
                  </dl>
                  <p>结束日期不计入查询范围。</p>
                </div>
              )}
              <UiDisclosure label="查看查询结论">
                <div className="analysis-markdown workflow-save-summary">
                  <Markdown skipHtml>{draft.summary}</Markdown>
                </div>
              </UiDisclosure>
              <UiDisclosure label="查看编排脚本" defaultOpen>
                <UiCodeBlock
                  title={draft.entryContract}
                  language="JavaScript"
                  code={draft.scriptSource}
                />
              </UiDisclosure>
            </div>
          ) : null}
          {error && (
            <p className="ui-notice" data-tone="error" role="alert">
              <CircleAlert size={17} aria-hidden="true" />
              <span>{error}</span>
            </p>
          )}
          <footer className="ui-actions workflow-save-actions">
            <UiButton variant="quiet" disabled={busy} onClick={() => setDismissed(true)}>
              暂不保存
            </UiButton>
            {draft ? (
              <UiButton busy={busy} disabled={!name.trim()} onClick={() => void confirm()}>
                确认保存为工作流
              </UiButton>
            ) : (
              <UiButton busy={busy} onClick={() => void open()}>
                查看工作流详情
              </UiButton>
            )}
          </footer>
        </>
      )}
    </section>
  );
}
