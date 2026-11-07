"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ChartNoAxesColumn,
  ChevronDown,
  Paperclip,
  Square,
  RotateCcw,
} from "lucide-react";
import { AnalysisAttachments, type ComposerAttachment } from "./analysis-attachments";
import { useDocumentSession } from "./shell/session-boundary";
import type { PagePayload } from "@/contracts/page";
import type { AvailableModels, GenerateInput, RoutedCallResponse } from "@/features/models/types";
import { modelStatus } from "@/features/models/types";
import { CallTrace } from "@/features/models/call-trace";
import type { TraceInfo } from "@/contracts/trace-context";
import { useModelCall } from "@/features/models/use-model-call";
import { modelErrorMessage } from "@/contracts/model-errors";

const suggestions = [
  "如何拆解销售增长的影响因素？",
  "怎样评估库存健康度？",
  "设计一份渠道分析提纲",
];
type Turn = TraceInfo & {
  id: string;
  question: string;
  alias: string;
  text: string;
  status: string;
  errorCode?: string | null;
  unknownOutcome?: boolean;
  errorMessage?: string;
  routed?: RoutedCallResponse;
};
export function AnalysisComposer({ payload }: { payload: PagePayload }) {
  const api = useDocumentSession();
  const call = useModelCall();
  const input = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const history = useRef<HTMLDivElement>(null);
  const followOutput = useRef(true);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [attachmentError, setAttachmentError] = useState("");
  const [question, setQuestion] = useState("");
  const [models, setModels] = useState<AvailableModels | null>(null);
  const [providerId, setProviderId] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [messages, setMessages] = useState<GenerateInput["messages"]>([]);
  const [activeQuestion, setActiveQuestion] = useState("");
  const [activeAlias, setActiveAlias] = useState("");
  const [loadError, setLoadError] = useState("");
  const [retry, setRetry] = useState(0);
  const [formError, setFormError] = useState("");
  const allowed = payload.session.capabilities.includes("models.invoke");
  const automatic = providerId === "auto";
  const selected = models?.items.find((model) => model.id === providerId);
  const started = turns.length > 0 || Boolean(activeQuestion);
  useLayoutEffect(() => {
    const node = history.current;
    if (node && followOutput.current) node.scrollTop = node.scrollHeight;
  }, [turns, activeQuestion, call.output?.text]);
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
    const remove = api.onClear(() => {
      setQuestion("");
      setModels(null);
      setProviderId("");
      setMessages([]);
      setTurns([]);
      setActiveQuestion("");
      setActiveAlias("");
      setAttachments([]);
      setAttachmentError("");
      setFormError("");
      followOutput.current = true;
      if (fileInput.current) fileInput.current.value = "";
    });
    return () => {
      live = false;
      remove();
    };
  }, [api, allowed, payload.session.scope, retry]);
  function changeModel(value: string) {
    if (call.busy) return;
    if (
      messages.length &&
      !window.confirm("切换模型将开启新的对话上下文，已有内容仍保留在当前页面。继续？")
    )
      return;
    call.reset();
    setProviderId(value);
    setMessages([]);
    setActiveQuestion("");
    setFormError("");
  }
  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!allowed || (!selected && !automatic) || !question.trim() || call.busy) return;
    if (attachments.length) {
      setFormError("附件暂不支持发送给模型，请移除附件后发送文字。");
      return;
    }
    if (messages.length > 60) {
      setFormError("当前对话已达到消息上限，请新建对话后继续。");
      return;
    }
    const text = question.trim();
    if (activeQuestion && call.output) {
      const previous = call.output;
      setTurns((items) => [
        ...items,
        {
          id: previous.callId,
          question: activeQuestion,
          alias: activeAlias,
          text: previous.text,
          status: previous.status,
          errorMessage: call.error,
          unknownOutcome: true,
        },
      ]);
    }
    const nextMessages: GenerateInput["messages"] = [
      ...messages,
      { role: "user", content: text, tool_call_id: null, tool_calls: null },
    ];
    const generate: GenerateInput = {
      kind: "generate",
      messages: nextMessages,
      stream: automatic || selected?.capabilities.stream.state === "supported",
      thinking: selected?.capabilities.thinking.state === "supported",
      reasoning_effort: selected?.capabilities.thinking.state === "supported" ? "high" : null,
      tools: [],
      output_format: "text",
      output_schema: null,
      max_tokens: 8192,
      continuation_token: null,
    };
    setActiveQuestion(text);
    setActiveAlias(selected?.alias ?? "系统优化");
    setQuestion("");
    setFormError("");
    followOutput.current = true;
    const result = await call.run(
      "models/calls",
      {
        ...payload.session.scope,
        ...(automatic ? { main_model: { mode: "auto" } } : { provider_id: selected!.id }),
        input: generate,
      },
      {
        onError: (code) => {
          if (code === "model_unavailable" || code === "chat_model_not_supported") {
            setProviderId("");
            setMessages([]);
            setQuestion(text);
            api.setDirty(true);
            setRetry((value) => value + 1);
          }
        },
      },
    );
    if (!api.retainsContent) return;
    if (result) {
      setTurns((items) => [
        ...items,
        {
          id: result.call_id,
          question: text,
          alias: result.routed?.routing?.model ?? selected?.alias ?? "系统优化",
          routed: result.routed,
          traceId: result.traceId,
          traceSampled: result.traceSampled,
          text: result.text,
          status: result.status,
          errorCode: result.error_code,
          unknownOutcome: result.upstream_outcome_unknown,
        },
      ]);
      if (result.status === "completed" && !result.tool_calls?.length)
        setMessages([
          ...nextMessages,
          { role: "assistant", content: result.text, tool_call_id: null, tool_calls: null },
        ]);
      setActiveQuestion("");
      call.reset();
      if (api.phase === "active") input.current?.focus();
    }
  }
  function reset() {
    if (
      (turns.length || question || activeQuestion || attachments.length) &&
      !window.confirm("清空当前页面的对话内容并新建对话？")
    )
      return;
    call.reset();
    setTurns([]);
    setMessages([]);
    setQuestion("");
    setActiveQuestion("");
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
    if (!files?.length || !allowed || call.busy) return;
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
    api.setDirty(Boolean(question || remaining.length || turns.length || activeQuestion));
  }
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
            <button type="button" className="new-chat-button" onClick={reset} disabled={call.busy}>
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
              <article key={turn.id} className="model-chat-turn">
                <p className="model-chat-question">{turn.question}</p>
                <div className="model-chat-answer">
                  <span className="model-chat-label">
                    {turn.alias} · {modelStatus[turn.status]}
                  </span>
                  <pre className="model-prose">{turn.text || "本次调用没有返回正文。"}</pre>
                  {turn.routed && <RoutingDetails value={turn.routed} />}
                  <CallTrace
                    callId={turn.id}
                    traceId={turn.traceId}
                    traceSampled={turn.traceSampled}
                  />
                  {turn.errorMessage && (
                    <p className="form-error" role="alert">
                      {turn.errorMessage}
                    </p>
                  )}
                  {turn.errorCode && (
                    <p className="form-error" role="alert">
                      {modelErrorMessage(turn.errorCode)}
                    </p>
                  )}
                  {turn.status !== "completed" && (
                    <p className="model-help muted">
                      调用 ID：{turn.id}
                      {turn.unknownOutcome ? " · 上游执行结果待确认，可能已经产生用量。" : ""}
                    </p>
                  )}
                </div>
              </article>
            ))}
            {activeQuestion && (
              <article className="model-chat-turn">
                <p className="model-chat-question">{activeQuestion}</p>
                <div className="model-chat-answer">
                  <span className="model-chat-label" role="status">
                    {activeAlias} ·{" "}
                    {call.busy
                      ? (call.output?.phase ?? "准备识别任务")
                      : call.output
                        ? modelStatus[call.output.status]
                        : "准备调用"}
                  </span>
                  <pre className="model-prose">
                    {call.output?.text || (call.busy ? "正在思考…" : "本次调用未返回内容。")}
                  </pre>
                  {call.output?.routed && <RoutingDetails value={call.output.routed} />}
                  {call.output && (
                    <CallTrace
                      callId={call.output.callId}
                      traceId={call.output.traceId}
                      traceSampled={call.output.traceSampled}
                    />
                  )}
                  {call.error && (
                    <p className="form-error" role="alert">
                      {call.error}
                    </p>
                  )}
                </div>
              </article>
            )}
          </div>
        </>
      )}
      <div className="analysis-composer-dock">
        <form
          ref={form}
          className="analysis-composer"
          data-editor
          onSubmit={(event) => void send(event)}
        >
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            aria-label="选择附件"
            onChange={(event) => addAttachments(event.target.files)}
            disabled={!allowed || call.busy}
          />
          <AnalysisAttachments
            items={attachments}
            disabled={call.busy}
            onRemove={removeAttachment}
          />
          <textarea
            ref={input}
            aria-label="分析问题"
            aria-describedby={attachments.length ? "analysis-attachment-notice" : undefined}
            placeholder="给 Compass 发送消息"
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
            disabled={call.busy}
          />
          <div className="composer-toolbar">
            <label className="composer-model-picker">
              <select
                aria-label="选择 Chat 主模型"
                value={providerId}
                disabled={!allowed || call.busy || !models?.items.length}
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
                disabled={!allowed || call.busy}
                onClick={() => fileInput.current?.click()}
              >
                <Paperclip size={21} aria-hidden="true" />
              </button>
              {call.busy ? (
                <button
                  type="button"
                  className="composer-stop"
                  aria-label="停止生成"
                  title="停止生成"
                  onClick={call.stop}
                >
                  <Square size={17} aria-hidden="true" />
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
                disabled={call.busy}
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

function RoutingDetails({ value }: { value: RoutedCallResponse }) {
  return (
    <details className="model-routing-details">
      <summary>意图分类与响应结构</summary>
      <dl>
        <dt>一级分类 / 二级场景</dt>
        <dd>
          {value.intent ? `${value.intent.category_id} / ${value.intent.scene_id}` : "未完成分类"}
        </dd>
        <dt>本次模型</dt>
        <dd>{value.routing?.model ?? "未调用推理模型"}</dd>
      </dl>
      <pre className="model-routing-json" aria-label="意图分类响应结构">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}
