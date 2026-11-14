"use client";
import { useEffect, useState } from "react";
import { ArrowLeft, Play, Square } from "lucide-react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { useModelCall } from "./use-model-call";
import { ModelOutputPanel } from "./output";
import type { DecisionInput, GenerateInput, Provider } from "./types";
import { capabilityNames } from "./types";

import { decisionExamples, type DecisionQuestionDraft as Question } from "./decision-examples";

const initialQuestion = (): Question => ({
  id: "decision_1",
  type: "choice",
  instructions: "",
  criteria: '{"A":"允许","B":"拒绝"}',
});
const initialTools =
  '[{"type":"function","function":{"name":"get_sales","description":"获取指定月份的销售额","parameters":{"type":"object","properties":{"month":{"type":"string"}},"required":["month"],"additionalProperties":false}}}]';
export function ModelPlayground({
  provider,
  onClose,
}: {
  provider: Provider;
  onClose: () => void;
}) {
  const api = useDocumentSession();
  const call = useModelCall();
  const defaultExample = provider.kind === "decision" ? decisionExamples[0] : null;
  const [exampleId, setExampleId] = useState(defaultExample?.id ?? "");
  const [prompt, setPrompt] = useState(defaultExample?.state ?? "");
  const [stream, setStream] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [effort, setEffort] = useState("");
  const [maxTokens, setMaxTokens] = useState(8192);
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [tools, setTools] = useState(initialTools);
  const [outputFormat, setOutputFormat] = useState<"text" | "json" | "schema">("text");
  const [schema, setSchema] = useState(
    '{"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"],"additionalProperties":false}',
  );
  const [stateFormat, setStateFormat] = useState(defaultExample?.stateFormat ?? "text");
  const [questions, setQuestions] = useState<Question[]>(
    defaultExample?.questions.map((question) => ({ ...question })) ?? [initialQuestion()],
  );
  const [formError, setFormError] = useState("");
  const [toolResults, setToolResults] = useState<Record<string, string>>({});
  const [continuation, setContinuation] = useState<{ input: GenerateInput; token: string } | null>(
    null,
  );
  const [mobilePane, setMobilePane] = useState("input");
  useEffect(
    () =>
      api.onClear(() => {
        setPrompt("");
        setTools("");
        setSchema("");
        setQuestions([]);
        setToolResults({});
        setContinuation(null);
      }),
    [api],
  );
  function loadExample(id: string) {
    const example = decisionExamples.find((item) => item.id === id);
    if (!example) return;
    if (api.dirty && !window.confirm("加载示例会替换当前测试输入和结果，继续？")) return;
    setExampleId(example.id);
    setPrompt(example.state);
    setStateFormat(example.stateFormat);
    setQuestions(example.questions.map((question) => ({ ...question })));
    setFormError("");
    setToolResults({});
    setContinuation(null);
    call.reset();
    api.setDirty(false);
    setMobilePane("input");
  }
  function changeQuestion(index: number, values: Partial<Question>) {
    setQuestions((items) => items.map((item, i) => (i === index ? { ...item, ...values } : item)));
  }
  const supported = (name: keyof typeof capabilityNames) =>
    provider.capabilities[name].state === "supported";
  async function run(continueTools = false) {
    setFormError("");
    try {
      let input: GenerateInput | DecisionInput;
      if (provider.kind === "decision") {
        const entries: DecisionInput["questions"] = {};
        for (const question of questions) {
          if (!question.id.trim() || entries[question.id.trim()])
            throw new Error("决策问题 ID 必须填写且保持唯一。");
          entries[question.id.trim()] = {
            type: question.type,
            instructions: question.instructions,
            criteria:
              question.type === "noul"
                ? null
                : question.type === "score"
                  ? question.criteria
                      .split("\n")
                      .map((item) => item.trim())
                      .filter(Boolean)
                  : JSON.parse(question.criteria),
          };
        }
        input = {
          kind: "decision",
          state: stateFormat === "json" ? JSON.parse(prompt) : prompt,
          questions: entries,
          stream: false,
        };
      } else if (continueTools) {
        if (!continuation || !call.output?.tools.length) throw new Error("请先完成一次工具调用。");
        if (call.output.tools.some((tool) => !toolResults[tool.id]?.trim()))
          throw new Error("请为每个工具调用填写结果。");
        input = {
          ...continuation.input,
          continuation_token: continuation.token,
          messages: [
            ...continuation.input.messages,
            {
              role: "assistant",
              content: call.output.text,
              tool_calls: call.output.tools,
              tool_call_id: null,
            },
            ...call.output.tools.map((tool) => ({
              role: "tool" as const,
              tool_call_id: tool.id,
              tool_calls: null,
              content: toolResults[tool.id],
            })),
          ],
        };
      } else {
        input = {
          kind: "generate",
          continuation_token: null,
          messages: [{ role: "user", content: prompt, tool_call_id: null, tool_calls: null }],
          stream,
          thinking,
          reasoning_effort:
            thinking && effort ? (effort as GenerateInput["reasoning_effort"]) : null,
          max_tokens: maxTokens,
          temperature: null,
          top_p: null,
          tools: toolsEnabled ? JSON.parse(tools) : [],
          output_format: outputFormat,
          output_schema: outputFormat === "schema" ? JSON.parse(schema) : null,
        };
      }
      if (!continueTools && !prompt.trim())
        throw new Error(provider.kind === "decision" ? "请填写决策场景。" : "请填写测试消息。");
      setContinuation(null);
      setToolResults({});
      setMobilePane("output");
      const result = await call.run(`platform/models/${provider.id}/tests`, { input });
      if (result?.continuation_token && input.kind === "generate" && result.tool_calls?.length)
        setContinuation({ input, token: result.continuation_token });
    } catch (cause) {
      setFormError(
        cause instanceof SyntaxError
          ? "JSON 格式有误，请检查工具、Schema 或决策输入。"
          : cause instanceof Error
            ? cause.message
            : "请检查输入内容。",
      );
    }
  }
  const close = () => {
    if (
      api.dirty &&
      !window.confirm(
        call.busy ? "停止当前调用并返回模型列表？" : "放弃本页测试输入和结果并返回模型列表？",
      )
    )
      return;
    call.reset();
    api.setDirty(false);
    onClose();
  };
  return (
    <section className="model-playground">
      <div className="model-page-head">
        <div>
          <button className="model-back secondary compact" onClick={close}>
            <ArrowLeft size={15} />
            返回模型列表
          </button>
          <h2>
            {provider.alias}
            <span className="status status-muted">
              {provider.kind === "decision" ? "决策模型" : "LLM"}
            </span>
          </h2>
          <p className="muted">
            {provider.api_model} · 配置版本 {provider.version} · 每次运行均产生一次真实模型调用。
          </p>
        </div>
      </div>
      <div className="model-mobile-tabs">
        <button
          className={mobilePane === "input" ? "" : "secondary"}
          onClick={() => setMobilePane("input")}
        >
          测试输入
        </button>
        <button
          className={mobilePane === "output" ? "" : "secondary"}
          onClick={() => setMobilePane("output")}
        >
          输出结果
        </button>
      </div>
      <div className={`model-playground-grid model-mobile-${mobilePane}`}>
        <form
          className="model-input panel model-form"
          data-editor
          onSubmit={(event) => {
            event.preventDefault();
            void run();
          }}
        >
          <h3>能力测试</h3>
          <fieldset
            disabled={call.busy}
            onChange={() => {
              api.setDirty(true);
              setContinuation(null);
            }}
          >
            {provider.kind === "llm" ? (
              <>
                <label>
                  测试消息
                  <textarea
                    aria-label="测试消息"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    rows={5}
                    maxLength={32000}
                    placeholder="输入一条测试消息"
                    required
                  />
                </label>
                <div className="model-form-grid">
                  <label>
                    返回方式
                    <select
                      value={stream ? "stream" : "sync"}
                      onChange={(e) => setStream(e.target.value === "stream")}
                    >
                      <option value="sync" disabled={!supported("sync")}>
                        同步
                      </option>
                      <option value="stream" disabled={!supported("stream")}>
                        流式
                      </option>
                    </select>
                  </label>
                  <label>
                    最大输出 tokens
                    <input
                      type="number"
                      min={1}
                      max={32768}
                      value={maxTokens}
                      onChange={(e) => setMaxTokens(Number(e.target.value))}
                      required
                    />
                  </label>
                </div>
                <label className="model-check" title={provider.capabilities.thinking.reason}>
                  <input
                    type="checkbox"
                    checked={thinking}
                    disabled={!supported("thinking")}
                    onChange={(e) => {
                      setThinking(e.target.checked);
                      setEffort("");
                    }}
                  />
                  开启 Thinking
                </label>
                {thinking && (
                  <label>
                    推理强度
                    <select value={effort} onChange={(e) => setEffort(e.target.value)}>
                      <option value="">模型默认</option>
                      {provider.capabilities.reasoning_efforts.map((item) => (
                        <option key={item} value={item}>
                          {item}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="model-check" title={provider.capabilities.tools.reason}>
                  <input
                    type="checkbox"
                    checked={toolsEnabled}
                    disabled={!supported("tools")}
                    onChange={(e) => setToolsEnabled(e.target.checked)}
                  />
                  请求工具调用
                </label>
                {toolsEnabled && (
                  <label>
                    工具定义（JSON 数组）
                    <textarea
                      className="model-code"
                      value={tools}
                      onChange={(e) => setTools(e.target.value)}
                      rows={7}
                      spellCheck={false}
                      maxLength={200000}
                    />
                    <span className="model-help muted">
                      填写 function 的 name、description 和
                      parameters。工具仅展示请求，结果由你手动回填。
                    </span>
                  </label>
                )}
                <label>
                  输出格式
                  <select
                    value={outputFormat}
                    onChange={(e) => setOutputFormat(e.target.value as typeof outputFormat)}
                  >
                    <option value="text">文本</option>
                    <option value="json" disabled={!supported("json_output")}>
                      JSON
                    </option>
                    <option value="schema" disabled={!supported("schema_output")}>
                      JSON Schema
                    </option>
                  </select>
                </label>
                {outputFormat === "schema" && (
                  <label>
                    输出 JSON Schema
                    <textarea
                      className="model-code"
                      value={schema}
                      onChange={(e) => setSchema(e.target.value)}
                      rows={6}
                      spellCheck={false}
                      maxLength={200000}
                    />
                  </label>
                )}
                <details className="model-capability-details">
                  <summary>模型能力说明</summary>
                  <ul>
                    {Object.entries(capabilityNames).map(([key, name]) => {
                      const item = provider.capabilities[key as keyof typeof capabilityNames];
                      return (
                        <li key={key}>
                          {name}：
                          {item.state === "supported"
                            ? "支持"
                            : item.state === "unsupported"
                              ? "不支持"
                              : "待验证"}{" "}
                          · {item.reason}
                        </li>
                      );
                    })}
                  </ul>
                </details>
              </>
            ) : (
              <>
                <p className="model-notice">决策模型使用结构化问题，在管理端同步测试。</p>
                <label>
                  测试示例
                  <select
                    aria-label="测试示例"
                    value={exampleId}
                    onChange={(event) => {
                      event.stopPropagation();
                      setExampleId(event.target.value);
                    }}
                  >
                    {decisionExamples.map((example) => (
                      <option key={example.id} value={example.id}>
                        {example.title}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="secondary compact"
                  onClick={() => loadExample(exampleId)}
                >
                  加载示例
                </button>
                <p className="muted model-help">
                  {decisionExamples.find((example) => example.id === exampleId)?.description}{" "}
                  加载示例后可继续编辑，点击“运行测试”才会调用模型。
                </p>
                <label>
                  场景格式
                  <select
                    value={stateFormat}
                    onChange={(e) => setStateFormat(e.target.value as "text" | "json")}
                  >
                    <option value="text">文本</option>
                    <option value="json">JSON</option>
                  </select>
                </label>
                <label>
                  决策场景 state
                  <textarea
                    aria-label="决策场景 state"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    rows={5}
                    maxLength={64000}
                    placeholder="填写决策所需的事实和上下文"
                    required
                  />
                </label>
                {questions.map((question, index) => (
                  <fieldset className="model-question" key={index}>
                    <legend>问题 {index + 1}</legend>
                    <div className="model-form-grid">
                      <label>
                        问题 ID
                        <input
                          value={question.id}
                          onChange={(e) => changeQuestion(index, { id: e.target.value })}
                          maxLength={64}
                          required
                        />
                      </label>
                      <label>
                        问题类型
                        <select
                          aria-label="问题类型"
                          value={question.type}
                          onChange={(e) =>
                            changeQuestion(index, {
                              type: e.target.value as Question["type"],
                              criteria:
                                e.target.value === "score"
                                  ? "低\n高"
                                  : e.target.value === "choice"
                                    ? '{"A":"允许","B":"拒绝"}'
                                    : "",
                            })
                          }
                        >
                          <option value="choice">choice · 选项决策</option>
                          <option value="noul">noul · 是非判断</option>
                          <option value="score">score · 等级评分</option>
                        </select>
                      </label>
                    </div>
                    <label>
                      问题说明
                      <textarea
                        aria-label="问题说明"
                        value={question.instructions}
                        onChange={(e) => changeQuestion(index, { instructions: e.target.value })}
                        maxLength={8000}
                        rows={2}
                      />
                    </label>
                    {question.type !== "noul" && (
                      <label>
                        {question.type === "choice"
                          ? "选项字典 criteria（JSON）"
                          : "评分等级 criteria（每行一项，至少 2 项）"}
                        <textarea
                          className="model-code"
                          value={question.criteria}
                          onChange={(e) => changeQuestion(index, { criteria: e.target.value })}
                          rows={3}
                          maxLength={32000}
                          required
                        />
                      </label>
                    )}
                    {questions.length > 1 && (
                      <button
                        type="button"
                        className="secondary compact"
                        onClick={() => setQuestions((items) => items.filter((_, i) => i !== index))}
                      >
                        移除问题 {index + 1}
                      </button>
                    )}
                  </fieldset>
                ))}
                <button
                  type="button"
                  className="secondary"
                  disabled={questions.length >= 16}
                  onClick={() =>
                    setQuestions((items) => [
                      ...items,
                      { ...initialQuestion(), id: `decision_${crypto.randomUUID().slice(0, 8)}` },
                    ])
                  }
                >
                  添加决策问题
                </button>
              </>
            )}
          </fieldset>
          {formError && (
            <p role="alert" className="form-error">
              {formError}
            </p>
          )}
          <div className="actions model-run-actions">
            {call.busy ? (
              <button type="button" className="secondary" onClick={call.stop}>
                <Square size={15} />
                停止生成
              </button>
            ) : (
              <button type="submit">
                <Play size={15} />
                运行测试
              </button>
            )}
            <span className="muted">手动触发 · 不自动重试</span>
          </div>
        </form>
        <div className="model-output-column panel">
          <ModelOutputPanel output={call.output} error={call.error} busy={call.busy} />
          {continuation && (
            <form
              className="model-tool-feedback model-form"
              data-editor
              onSubmit={(event) => {
                event.preventDefault();
                void run(true);
              }}
            >
              <h3>回填工具结果</h3>
              <p className="muted">按调用 ID 对应填写结果，再执行一次模型续接。</p>
              {call.output?.tools.map((tool) => (
                <label key={tool.id}>
                  {tool.function.name} · {tool.id}
                  <textarea
                    value={toolResults[tool.id] ?? ""}
                    onChange={(e) =>
                      setToolResults((items) => ({ ...items, [tool.id]: e.target.value }))
                    }
                    rows={3}
                    maxLength={100000}
                    required
                  />
                </label>
              ))}
              <button disabled={call.busy} type="submit">
                提交工具结果并续接
              </button>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}
