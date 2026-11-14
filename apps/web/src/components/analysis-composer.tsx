"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ChartNoAxesColumn,
  ChevronDown,
  Paperclip,
  RotateCcw,
  Square,
} from "lucide-react";
import {
  type AnalysisFileView,
  type AnalysisRunEntry,
  type RunConstraints,
  type WorkflowAssetRef,
} from "@/client/analysis-run";
import { notifySessionsChanged } from "@/client/analysis-sessions";
import { requestDurableControl, useDurableConversation } from "@/client/durable-analysis";
import type { components } from "@/generated/api";
import type { PagePayload } from "@/contracts/page";
import { RUN_STREAM_LIMITS } from "@/generated/run-stream-limits";
import type { AvailableModels } from "@/features/models/types";
import { AnalysisAttachments, type ComposerAttachment } from "./analysis-attachments";
import { AnalysisRunTurn } from "./analysis-run-turn";
import { AnalysisTodoPanel, planResolved } from "./analysis-todo-panel";
import { useDocumentSession } from "./shell/session-boundary";

type PromptSuggestion = {
  label: string;
  question: string;
  constraints?: RunConstraints;
};

type PromptCard = {
  title: string;
  suggestions: readonly PromptSuggestion[];
};

const workflowSuggestions = [
  {
    label: "对比渠道实付并保存",
    question:
      "请比较 Retail 两周的渠道实付额，找出差额绝对值最大的渠道。直接用 database_query 执行下面这一条只读 SQL，返回渠道 ID、两期金额和差额（金额单位为分）。仅使用渠道商品日报，无需查表目录、维表或拆商品。\n\nWITH latest AS ( SELECT DISTINCT ON (tenant_id, report_date, channel_id, sku_id, region_id) report_date, channel_id, paid_amount_cent FROM core.channel_product_daily WHERE report_date BETWEEN DATE '2025-11-17' AND DATE '2025-11-23' OR report_date BETWEEN DATE '2025-11-24' AND DATE '2025-11-30' ORDER BY tenant_id, report_date, channel_id, sku_id, region_id, report_version DESC ) SELECT channel_id, COALESCE(SUM(paid_amount_cent) FILTER (WHERE report_date BETWEEN DATE '2025-11-17' AND DATE '2025-11-23'), 0) AS baseline_cent, COALESCE(SUM(paid_amount_cent) FILTER (WHERE report_date BETWEEN DATE '2025-11-24' AND DATE '2025-11-30'), 0) AS current_cent FROM latest GROUP BY channel_id ORDER BY ABS(COALESCE(SUM(paid_amount_cent) FILTER (WHERE report_date BETWEEN DATE '2025-11-24' AND DATE '2025-11-30'), 0) - COALESCE(SUM(paid_amount_cent) FILTER (WHERE report_date BETWEEN DATE '2025-11-17' AND DATE '2025-11-23'), 0)) DESC, channel_id LIMIT 1\n\n查到结果后，请立即调用 create_workflow 创建并试跑「两周渠道实付对比」。将两期各自的起止日期设为四个 YYYY-MM-DD 字符串入参，脚本用本次入参填入同一条 SQL，只调度一个 sub_agent，prompt 明确要求直接执行这条 SQL 并返回查询结果。试跑成功后简要汇报结果并提示我确认保存。",
  },
  {
    label: "换两周对比渠道实付",
    question:
      "用 Retail 渠道商品日报比较 2025-11-03 至 2025-11-09 和 2025-11-10 至 2025-11-16。每个租户、日期、渠道、SKU 和区域取最新报表版本。找出实付额差额绝对值最大的渠道，只报告渠道 ID、两期金额和差额（单位为分）。",
  },
] as const;

const diagnoseRetail =
  "比较 2025-11-17 至 11-23 和 11-24 至 11-30 两周的渠道实付金额，找出变化最大的渠道，再分析主要商品的贡献。";

const promptCards: readonly PromptCard[] = [
  {
    title: "第 15 课 · Agent 执行内核",
    suggestions: [
      {
        label: "两周渠道与商品对比",
        question:
          "比较 2025-11-17 至 11-23 和 11-24 至 11-30 两周，哪个渠道的实付金额变化最大？变化主要来自哪些商品？",
      },
    ],
  },
  {
    title: "第 16 课 · 受控自主执行",
    suggestions: [
      {
        label: "先列计划 · 渠道与商品诊断",
        question: diagnoseRetail,
        constraints: { plan: "required" },
      },
      {
        label: "最多两轮 · 渠道与商品诊断",
        question: diagnoseRetail,
        constraints: { plan: "required", maxSteps: 2 },
      },
      {
        label: "5 秒超时 · 渠道与商品诊断",
        question: diagnoseRetail,
        constraints: { timeoutSeconds: 5 },
      },
    ],
  },
  {
    title: "第 17 课 · 混合工作流",
    suggestions: workflowSuggestions,
  },
  {
    title: "第 18 课 · 工具执行与 Sandbox",
    suggestions: [
      {
        label: "Python 分析绘图",
        question:
          "查询 Retail 中 POS 渠道在 2025-11-17 至 2025-11-23 和 2025-11-24 至 2025-11-30 的商品实付金额。使用 Python 计算每个商品两周的金额差额，生成可下载的柱状图，并结合计算结果解释主要变化；缺少数据时说明缺口。",
      },
      {
        label: "同轮多工具查询",
        question:
          "同一轮发起两项互不依赖的 Retail 查询：分别读取 POS 渠道在 2025-11-17 至 2025-11-23 和 2025-11-24 至 2025-11-30 的商品实付金额。拿到两份结果后再汇总差额，列出主要商品的金额变化，并标明所用时段。",
      },
      {
        label: "使用我的 MCP 工具",
        question:
          "请使用本次任务已加载的 MCP 只读工具，查询 2025-11-24 至 2025-11-30 的销售汇总，说明实付金额、订单量和工具来源。缺少合适的工具或必需参数时先告诉我；需要审批的调用先生成待审批提案。",
      },
    ],
  },
  {
    title: "通用分析",
    suggestions: ["如何拆解销售增长的影响因素？", "怎样评估库存健康度？"].map((question) => ({
      label: question,
      question,
    })),
  },
];

function AnalysisErrorToast({ message }: { message: string }) {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(false), 5000);
    return () => window.clearTimeout(timer);
  }, []);
  return visible ? (
    <p role="alert" className="toast analysis-error-toast">
      {message}
    </p>
  ) : null;
}

export function AnalysisComposer({
  payload,
  workflow,
}: {
  payload: PagePayload;
  workflow?: { name: string; ref: WorkflowAssetRef };
}) {
  const api = useDocumentSession();
  const input = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const stopButton = useRef<HTMLButtonElement>(null);
  const focusInput = useCallback(() => {
    if (input.current?.disabled) stopButton.current?.focus();
    else input.current?.focus();
  }, []);
  const fileInput = useRef<HTMLInputElement>(null);
  const history = useRef<HTMLDivElement>(null);
  const chat = useRef<HTMLElement>(null);
  const composerDock = useRef<HTMLDivElement>(null);
  const followOutput = useRef(true);
  const pendingSubmit = useRef<Record<string, unknown> | null>(null);
  const [accepting, setAccepting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [attachmentError, setAttachmentError] = useState("");
  const [question, setQuestion] = useState(
    workflow ? `使用已保存的工作流「${workflow.name}」分析所选时段。` : "",
  );
  const [entry, setEntry] = useState<AnalysisRunEntry>(
    workflow ? { workflowAssetRef: workflow.ref } : {},
  );
  const constraints = entry.constraints;
  const [models, setModels] = useState<AvailableModels | null>(null);
  const [providerId, setProviderId] = useState("auto");

  const [loadError, setLoadError] = useState("");
  const [retry, setRetry] = useState(0);
  const [formError, setFormError] = useState("");
  const allowed = payload.session.capabilities.includes("models.invoke");
  const { tenantId, workspaceId } = payload.session.scope;
  const workflowScope = tenantId && workspaceId ? { tenantId, workspaceId } : undefined;
  const sessionId = new URL(payload.viewKey, "https://compass.local").searchParams.get("session");
  const conversation = useDurableConversation(api, sessionId, workflowScope);
  const notification =
    formError ||
    attachmentError ||
    (conversation.missing ? "原会话已找不到，发送消息将开启新会话。" : conversation.error);
  const { turns } = conversation;
  const busy = accepting || navigating || conversation.loading || Boolean(conversation.active);
  const workflowPluginHref = payload.session.navigation.find(
    (item) => item.id === "plugins-workflows",
  )?.href;
  const automatic = providerId === "auto";
  const selected = models?.items.find((model) => model.id === providerId);
  const canSelectModel = allowed && !busy && Boolean(models?.items.length);
  const canSend =
    canSelectModel &&
    (automatic || Boolean(selected)) &&
    Boolean(question.trim()) &&
    !entry.workflowAssetRef;
  const started = turns.length > 0;
  const latestTurn = turns.at(-1);
  const activeTurn = busy ? latestTurn : undefined;
  const dockTurn =
    latestTurn?.phase === "running" ||
    (latestTurn?.terminal?.status === "completed" &&
      latestTurn.plan &&
      planResolved(latestTurn.plan))
      ? latestTurn
      : undefined;

  const revealOutput = useCallback(() => {
    const node = history.current;
    if (node && followOutput.current) node.scrollTop = node.scrollHeight;
  }, []);
  const downloadFile = useCallback(
    (file: Extract<AnalysisFileView, { status: "ready" }>) =>
      api.download(file.blob, file.filename),
    [api],
  );

  useLayoutEffect(() => revealOutput(), [turns, revealOutput]);

  useLayoutEffect(() => {
    const dock = composerDock.current;
    const section = chat.current;
    if (!started || !dock || !section) return;
    const measure = () => {
      section.style.setProperty("--composer-overlap", `${dock.offsetHeight}px`);
      revealOutput();
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(dock);
    return () => {
      observer.disconnect();
      section.style.removeProperty("--composer-overlap");
    };
  }, [revealOutput, started]);

  useEffect(() => {
    if (workflow) {
      api.setDirty(true);
      input.current?.focus({ preventScroll: true });
    }
  }, [api, workflow]);

  useEffect(() => {
    let live = true;
    if (allowed)
      void api
        .request<AvailableModels>(
          `models/available?${new URLSearchParams({ ...payload.session.scope, view: "chat" })}`,
        )
        .then((result) => {
          if (live) {
            setModels(result);
            setLoadError("");
          }
        })
        .catch(() => {
          if (live) setLoadError("可用模型加载失败，请重新加载。");
        });
    return () => {
      live = false;
    };
  }, [api, allowed, payload.session.scope, retry]);

  useEffect(() => {
    const remove = api.onClear(() => {
      setQuestion("");
      setEntry({});
      setModels(null);
      setProviderId("auto");
      setAttachments([]);
      setAttachmentError("");
      setFormError("");
      followOutput.current = true;
      if (fileInput.current) fileInput.current.value = "";
    });
    return () => {
      remove();
    };
  }, [api]);

  async function changeModel(value: string) {
    if (busy) return;
    if (sessionId && turns.length) {
      if (!window.confirm("切换模型将开启新的对话上下文，已有内容保留在会话中。继续？")) return;
      setAccepting(true);
      try {
        let cursor: string | null = null;
        let found: components["schemas"]["SessionListItem"] | undefined;
        do {
          const query = new URLSearchParams({
            ...workflowScope,
            limit: "50",
            ...(cursor ? { cursor } : {}),
          });
          const list: components["schemas"]["SessionList"] = await api.request(
            `analysis/sessions?${query}`,
          );
          found = list.items.find((item) => item.sessionId === sessionId);
          cursor = list.nextCursor;
        } while (!found && cursor);
        if (!found) throw new Error("会话信息已变化，请重新加载。");
        await api.request(`analysis/sessions/${sessionId}/context-reset`, "POST", {
          ...workflowScope,
          requestId: crypto.randomUUID(),
          expectedVersion: found.version,
        });
      } catch (error) {
        setFormError(error instanceof Error ? error.message : "上下文重置失败。");
        return;
      } finally {
        setAccepting(false);
      }
    }
    setProviderId(value);
    setFormError("");
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!canSend || !workflowScope) return;
    if (!form.current?.reportValidity()) return;
    if (attachments.length) {
      setFormError("附件暂不支持发送给模型，请移除附件后发送文字。");
      return;
    }
    const text = question.trim();
    const input = {
      question: text,
      sessionId: conversation.missing ? null : sessionId,
      selection: automatic
        ? { mode: "auto", provider_id: null }
        : { mode: "fixed", provider_id: selected!.id },
      ...entry,
    };
    if (
      !pendingSubmit.current ||
      JSON.stringify(pendingSubmit.current.input) !== JSON.stringify(input)
    )
      pendingSubmit.current = { input, requestId: crypto.randomUUID() };
    setAccepting(true);
    setFormError("");
    try {
      if (!automatic) {
        const current = await api.request<AvailableModels>(
          `models/available?${new URLSearchParams({ ...payload.session.scope, view: "chat" })}`,
        );
        setModels(current);
        if (!current.items.some((model) => model.id === providerId)) {
          setProviderId("auto");
          setFormError("所选模型已不可用，请重新选择后发送。");
          return;
        }
      }
      const result = await api.request<components["schemas"]["SessionAccepted"]>(
        "analysis/sessions/submit",
        "POST",
        { ...workflowScope, ...input, requestId: pendingSubmit.current.requestId },
      );
      pendingSubmit.current = null;
      setQuestion("");
      setEntry({});
      api.setDirty(false);
      followOutput.current = true;
      notifySessionsChanged(
        result.session?.titleStatus === "pending"
          ? {
              ...result.session,
              title: Array.from(text.trim().replace(/\s+/gu, " ")).slice(0, 40).join(""),
            }
          : result.session,
      );
      if (result.sessionId === sessionId) conversation.reload();
      else if (api.navigate(`${payload.viewKey.split("?")[0]}?session=${result.sessionId}`))
        setNavigating(true);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "提交未确认，请重试。");
    } finally {
      setAccepting(false);
    }
  }

  function changeConstraints(update: Partial<RunConstraints>) {
    setEntry({ constraints: { ...constraints, ...update } });
    api.setDirty(true);
  }

  function reset() {
    if (!sessionId) {
      setQuestion("");
      setEntry({});
      setAttachments([]);
      api.setDirty(false);
      return;
    }
    api.setDirty(Boolean(question || attachments.length));
    if (api.navigate(payload.viewKey.split("?")[0])) setNavigating(true);
  }

  function addAttachments(files: FileList | null) {
    if (!files?.length || !allowed || busy) return;
    const incoming = Array.from(files);
    const available = 10 - attachments.length;
    setAttachmentError(incoming.length > available ? "每条消息最多添加 10 个附件。" : "");
    setAttachments((items) => [
      ...items,
      ...incoming.slice(0, Math.max(0, 10 - items.length)).map((file) => ({
        id: crypto.randomUUID(),
        file,
      })),
    ]);
    setFormError("");
    api.setDirty(true);
    if (fileInput.current) fileInput.current.value = "";
  }

  function removeAttachment(id: string) {
    const remaining = attachments.filter((item) => item.id !== id);
    setAttachments(remaining);
    setAttachmentError("");
    setFormError("");
    api.setDirty(Boolean(question || remaining.length));
  }

  const stopWaiting =
    accepting ||
    stopping ||
    conversation.active?.run?.status === "cancelling" ||
    conversation.loading ||
    (activeTurn &&
      ["waiting_for_run", "requesting", "accepted", "already_terminal"].includes(
        activeTurn.stopState,
      ));
  const stopLabel =
    activeTurn?.stopState === "unconfirmed"
      ? "重试停止"
      : activeTurn?.stopState === "already_terminal"
        ? "等待结果流"
        : "停止任务";

  return (
    <section
      ref={chat}
      className={`analysis-welcome ${started ? "model-chat-started" : ""}`}
      data-chat-state={started ? "started" : "empty"}
      aria-label="新建分析对话"
    >
      {!started && (
        <div className="analysis-intro">
          <h2>
            每个好问题，<em>都有迹可循。</em>
          </h2>
          <p>从一个问题开始，发现经营改善的方向。</p>
        </div>
      )}
      {started && (
        <>
          <div className="model-chat-heading">
            <span>当前对话</span>
            <button type="button" className="new-chat-button" onClick={reset}>
              <RotateCcw size={15} aria-hidden="true" />
              新建对话
            </button>
          </div>
          <div
            ref={history}
            className="model-chat-history"
            aria-label="当前会话记录"
            tabIndex={0}
            onScroll={(event) => {
              const node = event.currentTarget;
              followOutput.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
            }}
          >
            {conversation.state?.nextCursor && (
              <button className="secondary" onClick={conversation.older}>
                加载更早记录
              </button>
            )}
            {turns.map((turn) => {
              const run = conversation.runs.find((view) => view.runId === turn.id);
              return (
                <AnalysisRunTurn
                  key={turn.id}
                  turn={turn}
                  onDownload={downloadFile}
                  onReveal={revealOutput}
                  scope={workflowScope}
                  pluginHref={workflowPluginHref}
                  saved
                  durable={
                    run && sessionId ? { run, sessionId, reload: conversation.reload } : undefined
                  }
                  partialText={
                    Object.values(run?.messages ?? {}).find((m) => m.role === "assistant")?.content
                      .text
                  }
                />
              );
            })}
          </div>
        </>
      )}
      {conversation.loading && <p role="status">正在读取会话…</p>}
      {notification && <AnalysisErrorToast key={notification} message={notification} />}
      {dockTurn?.plan && (
        <AnalysisTodoPanel key={dockTurn.id} plan={dockTurn.plan} onFocusReturn={focusInput} />
      )}
      <div className="analysis-composer-dock" ref={composerDock}>
        <form ref={form} className="analysis-composer" data-editor onSubmit={send}>
          {constraints && (
            <fieldset className="analysis-run-constraints" disabled={busy}>
              <legend>本次运行约束</legend>
              <label>
                <input
                  type="checkbox"
                  checked={constraints.plan === "required"}
                  onChange={(event) =>
                    changeConstraints({ plan: event.target.checked ? "required" : undefined })
                  }
                />
                先列计划
              </label>
              <div className="ui-field">
                <label htmlFor="analysis-max-steps">最多轮次</label>
                <input
                  id="analysis-max-steps"
                  className="ui-input"
                  type="number"
                  min={1}
                  max={RUN_STREAM_LIMITS.steps}
                  placeholder={String(RUN_STREAM_LIMITS.steps)}
                  value={constraints.maxSteps ?? ""}
                  onChange={(event) =>
                    changeConstraints({
                      maxSteps: event.target.value ? event.target.valueAsNumber : undefined,
                    })
                  }
                />
              </div>
              <div className="ui-field">
                <label htmlFor="analysis-timeout">时限（秒）</label>
                <input
                  id="analysis-timeout"
                  className="ui-input"
                  type="number"
                  min={1}
                  max={RUN_STREAM_LIMITS.timeoutSeconds}
                  placeholder={String(RUN_STREAM_LIMITS.timeoutSeconds)}
                  value={constraints.timeoutSeconds ?? ""}
                  onChange={(event) =>
                    changeConstraints({
                      timeoutSeconds: event.target.value ? event.target.valueAsNumber : undefined,
                    })
                  }
                />
              </div>
              <button type="button" className="secondary compact" onClick={() => setEntry({})}>
                清除约束
              </button>
            </fieldset>
          )}
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            aria-label="选择附件"
            onChange={(event) => addAttachments(event.target.files)}
            disabled={!allowed || busy}
          />
          <AnalysisAttachments items={attachments} disabled={busy} onRemove={removeAttachment} />
          <textarea
            ref={input}
            aria-label="分析问题"
            aria-keyshortcuts="Enter"
            aria-describedby={attachments.length ? "analysis-attachment-notice" : undefined}
            placeholder={busy ? "Compass 正在分析…" : "给 Compass 发送消息"}
            value={question}
            onChange={(event) => {
              setQuestion(event.target.value);
              setFormError("");
            }}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing &&
                event.nativeEvent.keyCode !== 229
              ) {
                event.preventDefault();
                form.current?.requestSubmit();
              }
            }}
            maxLength={8000}
            disabled={busy}
          />
          <div className="composer-toolbar">
            <label className="composer-model-picker">
              <select
                aria-label="选择 Chat 主模型"
                value={providerId}
                disabled={!canSelectModel}
                onChange={(event) => changeModel(event.target.value)}
              >
                <option value="">选择模型</option>
                <option value="auto">Auto</option>
                {models?.items.map((model) => (
                  <option value={model.id} key={model.id}>
                    {model.alias}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} aria-hidden="true" />
            </label>
            <div className="composer-actions">
              <button
                type="button"
                className="composer-attach"
                aria-label="添加附件"
                title="添加附件，最多 10 个"
                disabled={!allowed || busy}
                onClick={() => fileInput.current?.click()}
              >
                <Paperclip size={21} aria-hidden="true" />
              </button>
              {busy ? (
                <button
                  ref={stopButton}
                  type="button"
                  className="composer-stop-task"
                  disabled={Boolean(stopWaiting)}
                  aria-label={
                    activeTurn?.stopState === "already_terminal"
                      ? "任务已结束，正在等待原结果流"
                      : stopWaiting
                        ? "正在终止任务"
                        : stopLabel
                  }
                  onClick={async () => {
                    const run = conversation.active;
                    if (!run?.run) return;
                    setStopping(true);
                    setFormError("");
                    try {
                      await requestDurableControl(api, sessionId!, run.runId, "cancel", {
                        ...workflowScope,
                        requestId: crypto.randomUUID(),
                        expectedVersion: run.run.version,
                      });
                      conversation.reload();
                    } catch (error) {
                      setFormError(error instanceof Error ? error.message : "终止未确认。");
                    } finally {
                      setStopping(false);
                    }
                  }}
                >
                  <Square size={15} aria-hidden="true" />
                  {activeTurn?.stopState === "already_terminal"
                    ? "等待结果流"
                    : stopWaiting
                      ? "正在终止"
                      : stopLabel}
                </button>
              ) : (
                <button
                  className="composer-send"
                  type="submit"
                  disabled={!canSend}
                  aria-label="发送消息"
                  title="发送消息"
                >
                  <ArrowUp size={23} aria-hidden="true" />
                </button>
              )}
            </div>
          </div>
        </form>
        {attachments.length > 0 && (
          <p className="composer-attachment-notice" id="analysis-attachment-notice">
            附件仅供预览，暂不支持发送给模型。
          </p>
        )}
        {!allowed ? (
          <p className="model-notice">当前账户尚未获得模型调用权限，请联系工作区管理员。</p>
        ) : loadError ? (
          <p role="alert">
            {loadError}
            <button className="secondary compact" onClick={() => setRetry((value) => value + 1)}>
              重新加载模型
            </button>
          </p>
        ) : models && models.items.length === 0 ? (
          <p className="model-notice">暂无可用主模型，请联系平台管理员开放模型。</p>
        ) : null}
        {entry.workflowAssetRef && (
          <button type="button" className="secondary compact" onClick={() => setEntry({})}>
            旧版工作流执行已停止 · 清除选择后继续分析
          </button>
        )}
        {!started && (
          <div className="analysis-suggestions" role="group" aria-label="候选提示词">
            {promptCards.map((card) => (
              <section className="analysis-prompt-card" key={card.title} aria-label={card.title}>
                <h3>{card.title}</h3>
                <ul>
                  {card.suggestions.map(({ label, question, constraints }) => (
                    <li key={label}>
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy}
                        onClick={() => {
                          setQuestion(question);
                          setEntry(constraints ? { constraints } : {});
                          api.setDirty(true);
                          input.current?.focus();
                        }}
                      >
                        <ChartNoAxesColumn size={16} aria-hidden="true" />
                        <span>{label}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
