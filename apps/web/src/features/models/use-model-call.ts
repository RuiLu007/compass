"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { parseCallResponse, parseModelEvent } from "./protocol";
import { traceInfo, type TraceInfo } from "@/contracts/trace-context";
import { accumulateToolDelta } from "./tool-deltas";
import { assertModelOutputBudget } from "./output-budget";
import type { CallResponse, ModelEvent, ToolCall } from "./types";

export type ModelOutput = TraceInfo & {
  callId: string;
  text: string;
  tools: ToolCall[];
  status: string;
  thinking?: boolean;
  effort?: string | null;
  result?: CallResponse;
};
export function useModelCall() {
  const api = useDocumentSession();
  const [output, setOutput] = useState<ModelOutput | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const reset = useCallback(() => {
    epoch.current++;
    active.current?.abort();
    active.current = null;
    setOutput(null);
    setError("");
    setBusy(false);
  }, []);
  useEffect(() => {
    const remove = api.onClear(reset);
    return () => {
      remove();
      reset();
    };
  }, [api, reset]);
  const stop = () => active.current?.abort();
  async function run(
    path: string,
    body: Record<string, unknown>,
  ): Promise<(CallResponse & TraceInfo) | undefined> {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    const current = ++epoch.current;
    const callId = crypto.randomUUID();
    let view: ModelOutput = { callId, text: "", tools: [], status: "running" };
    let started = false;
    type TerminalEvent = Extract<ModelEvent, { type: "final" }>;
    let pendingFinal: TerminalEvent | undefined;
    let final: CallResponse | undefined;
    const toolParts = new Map<number, ToolCall>();
    const publish = () => {
      assertModelOutputBudget(view.text, view.tools);
      if (epoch.current === current && api.retainsContent) setOutput({ ...view });
    };
    setBusy(true);
    setError("");
    setOutput(view);
    api.setDirty(true);
    try {
      const result = await api.modelRequest(
        path,
        { ...body, call_id: callId },
        {
          signal: controller.signal,
          onHeaders(headers) {
            view = { ...view, ...traceInfo(headers) };
            publish();
          },
          parseResult: parseCallResponse,
          parseEvent: parseModelEvent,
          onEvent(event) {
            if (event.type === "start") {
              if (started) throw new Error("收到重复的模型开始事件。");
              started = true;
            } else {
              if (!started) throw new Error("模型事件缺少开始标记。");
              if (event.type === "text_delta") view = { ...view, text: view.text + event.text };
              if (event.type === "tool_delta") {
                view = { ...view, tools: accumulateToolDelta(toolParts, event) };
              }
              if (event.type === "reasoning_meta")
                view = { ...view, thinking: event.thinking, effort: event.reasoning_effort };
              if (event.type === "final") {
                if (pendingFinal) throw new Error("收到重复的模型结束事件。");
                pendingFinal = event;
              }
            }
            publish();
          },
        },
      );
      const terminal = pendingFinal as TerminalEvent | undefined;
      if (terminal) {
        const { type: _type, ...outcome } = terminal;
        void _type;
        final = {
          ...outcome,
          kind: "generate",
          text: view.text,
          tool_calls: view.tools,
          answers: null,
          raw_response: null,
        };
        view = { ...view, status: terminal.status, result: final };
        publish();
      }
      if (result) {
        if (result.call_id !== callId) throw new Error("模型结果与当前调用不一致。");
        final = result;
        view = {
          ...view,
          text: result.text,
          tools: result.tool_calls ?? [],
          status: result.status,
          result,
        };
        publish();
      }
      return final
        ? { ...final, traceId: view.traceId, traceSampled: view.traceSampled }
        : undefined;
    } catch (cause) {
      if (epoch.current !== current || !api.retainsContent) return;
      const canceled = controller.signal.aborted;
      view = {
        ...view,
        result: undefined,
        status: canceled ? "canceled" : view.text || view.tools.length ? "incomplete" : "failed",
      };
      setOutput(view);
      setError(
        canceled
          ? "已停止本次调用。上游执行可能已经产生用量。"
          : cause instanceof Error
            ? cause.message
            : "调用暂时失败，请手动重试。",
      );
      return undefined;
    } finally {
      if (epoch.current === current) {
        active.current = null;
        setBusy(false);
      }
    }
  }
  return { output, error, busy, run, stop, reset };
}
