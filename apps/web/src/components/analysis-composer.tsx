"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ChartNoAxesColumn,
  ChevronDown,
  Paperclip,
  RotateCcw,
  Square,
} from "lucide-react";
import {
  AnalysisRunClient,
  completedAnalysisHistory,
  type AnalysisTurnView,
} from "@/client/analysis-run";
import type { PagePayload } from "@/contracts/page";
import type { AvailableModels } from "@/features/models/types";
import { AnalysisAttachments, type ComposerAttachment } from "./analysis-attachments";
import { AnalysisRunTurn } from "./analysis-run-turn";
import { useDocumentSession } from "./shell/session-boundary";

const suggestions = [
  "比较 2025-11-17 至 11-23 和 11-24 至 11-30 两周，哪个渠道的实付金额变化最大？变化主要来自哪些商品？",
  "如何拆解销售增长的影响因素？",
  "怎样评估库存健康度？",
];

export function AnalysisComposer({ payload }: { payload: PagePayload }) {
  const api = useDocumentSession();
  const input = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const history = useRef<HTMLDivElement>(null);
  const followOutput = useRef(true);
  const active = useRef<AnalysisRunClient | null>(null);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [attachmentError, setAttachmentError] = useState("");
  const [question, setQuestion] = useState("");
  const [models, setModels] = useState<AvailableModels | null>(null);
  const [providerId, setProviderId] = useState("");
  const [turns, setTurns] = useState<AnalysisTurnView[]>([]);
  const [historyStart, setHistoryStart] = useState(0);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [retry, setRetry] = useState(0);
  const [formError, setFormError] = useState("");
  const allowed = payload.session.capabilities.includes("models.invoke");
  const automatic = providerId === "auto";
  const selected = models?.items.find((model) => model.id === providerId);
  const started = turns.length > 0;
  const activeTurn = busy ? turns.at(-1) : undefined;

  useLayoutEffect(() => {
    const node = history.current;
    if (node && followOutput.current) node.scrollTop = node.scrollHeight;
  }, [turns]);

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
      active.current?.dispose();
      active.current = null;
      setQuestion("");
      setModels(null);
      setProviderId("");
      setTurns([]);
      setHistoryStart(0);
      setBusy(false);
      setAttachments([]);
      setAttachmentError("");
      setFormError("");
      followOutput.current = true;
      if (fileInput.current) fileInput.current.value = "";
    });
    return () => {
      remove();
      active.current?.dispose();
      active.current = null;
    };
  }, [api]);

  function changeModel(value: string) {
    if (busy) return;
    if (
      completedAnalysisHistory(turns.slice(historyStart)).length > 0 &&
      !window.confirm("切换模型将开启新的对话上下文，已有内容仍保留在当前页面。继续？")
    )
      return;
    setProviderId(value);
    setHistoryStart(turns.length);
    setFormError("");
  }

  function updateTurn(next: AnalysisTurnView) {
    setTurns((items) => {
      const index = items.findIndex((item) => item.id === next.id);
      if (index < 0) return [...items, next];
      const copy = [...items];
      copy[index] = next;
      return copy;
    });
  }

  function send(event: React.FormEvent) {
    event.preventDefault();
    if (!allowed || (!selected && !automatic) || !question.trim() || busy) return;
    if (attachments.length) {
      setFormError("附件暂不支持发送给模型，请移除附件后发送文字。");
      return;
    }
    const { tenantId, workspaceId } = payload.session.scope;
    if (!tenantId || !workspaceId) {
      setFormError("当前工作区范围无效，请重新进入分析页面。");
      return;
    }
    const text = question.trim();
    let client: AnalysisRunClient;
    try {
      client = new AnalysisRunClient({
        api,
        scope: { tenantId, workspaceId },
        question: text,
        history: completedAnalysisHistory(turns.slice(historyStart)),
        selection: automatic
          ? { mode: "auto", provider_id: null }
          : { mode: "fixed", provider_id: selected!.id },
        modelLabel: selected?.alias ?? "系统优化",
        onChange: updateTurn,
        onSettled(settled) {
          if (active.current !== settled) return;
          active.current = null;
          setBusy(false);
          if (api.phase === "active") input.current?.focus();
        },
      });
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "无法启动本轮分析。");
      return;
    }
    active.current = client;
    setBusy(true);
    setQuestion("");
    setFormError("");
    followOutput.current = true;
    api.setDirty(true);
    void client.start();
  }

  function reset() {
    if (
      (turns.length || question || busy || attachments.length) &&
      !window.confirm("清空当前页面的对话内容并新建对话？")
    )
      return;
    active.current?.dispose();
    active.current = null;
    api.releaseDownloads();
    setTurns([]);
    setHistoryStart(0);
    setBusy(false);
    setQuestion("");
    setProviderId("");
    setRetry((value) => value + 1);
    setFormError("");
    setAttachments([]);
    setAttachmentError("");
    followOutput.current = true;
    if (fileInput.current) fileInput.current.value = "";
    api.setDirty(false);
    input.current?.focus();
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
    api.setDirty(Boolean(question || remaining.length || turns.length || busy));
  }

  const stopWaiting =
    activeTurn &&
    ["waiting_for_run", "requesting", "accepted", "already_terminal"].includes(
      activeTurn.stopState,
    );
  const stopLabel =
    activeTurn?.stopState === "unconfirmed"
      ? "重试停止"
      : activeTurn?.stopState === "already_terminal"
        ? "等待结果流"
        : "停止任务";

  return (
    <section
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
            aria-label="当前页面对话记录"
            tabIndex={0}
            onScroll={(event) => {
              const node = event.currentTarget;
              followOutput.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
            }}
          >
            {turns.map((turn) => (
              <AnalysisRunTurn
                key={turn.id}
                turn={turn}
                onDownload={(file) => api.download(file.blob, file.filename)}
              />
            ))}
          </div>
        </>
      )}
      <div className="analysis-composer-dock">
        <form ref={form} className="analysis-composer" data-editor onSubmit={send}>
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
                disabled={!allowed || busy || !models?.items.length}
                onChange={(event) => changeModel(event.target.value)}
              >
                <option value="">选择模型</option>
                <option value="auto">系统优化</option>
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
                  onClick={() => active.current?.requestStop()}
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
                  disabled={!allowed || (!selected && !automatic) || !question.trim()}
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
        {(formError || attachmentError) && (
          <p className="form-error" role="alert">
            {formError || attachmentError}
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
        {!started && (
          <div className="analysis-suggestions">
            {suggestions.map((suggestion) => (
              <button
                className="secondary"
                key={suggestion}
                disabled={busy}
                onClick={() => {
                  setQuestion(suggestion);
                  api.setDirty(true);
                  input.current?.focus();
                }}
              >
                <ChartNoAxesColumn size={17} aria-hidden="true" />
                {suggestion}
              </button>
            ))}
          </div>
        )}
        <p className="analysis-availability">
          <span className="composer-shortcut">Enter 发送，Shift + Enter 换行 · </span>
          <span>对话内容仅保留在本页</span>
        </p>
      </div>
    </section>
  );
}
