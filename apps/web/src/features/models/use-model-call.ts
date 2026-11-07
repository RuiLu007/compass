"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import {
  parseCallResponse,
  parseModelEvent,
  parseRoutedResponse,
  parseRoutedEvent,
} from "./protocol";
import { traceInfo, type TraceInfo } from "@/contracts/trace-context";
import { ApiError } from "@/client/request-scope";
import { accumulateToolDelta } from "./tool-deltas";
import { assertModelOutputBudget } from "./output-budget";
import type {
  CallResponse,
  ModelEvent,
  RoutedCallResponse,
  RoutedModelEvent,
  ToolCall,
} from "./types";

export type ModelOutput = TraceInfo & {
  callId: string;
  text: string;
  tools: ToolCall[];
  status: string;
  thinking?: boolean;
  effort?: string | null;
  result?: CallResponse;
  routed?: RoutedCallResponse;
  phase?: string;
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
    options?: { onError?: (code?: string) => void },
  ): Promise<(CallResponse & TraceInfo & { routed?: RoutedCallResponse }) | undefined> {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    const current = ++epoch.current;
    const callId = crypto.randomUUID();
    let view: ModelOutput = { callId, text: "", tools: [], status: "running" };
    let started = false;
    type TerminalEvent = Extract<ModelEvent | RoutedModelEvent, { type: "final" }>;
    let pendingFinal: TerminalEvent | undefined;
    const routed = path === "models/calls";
    let routingResult: RoutedCallResponse | undefined;
    let final: (CallResponse & { routed?: RoutedCallResponse }) | undefined;
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
          parseResult: (value: unknown) =>
            routed ? parseRoutedResponse(value) : parseCallResponse(value),
          parseEvent: (value: unknown) =>
            routed ? parseRoutedEvent(value) : parseModelEvent(value),
          onEvent(event: ModelEvent | RoutedModelEvent) {
            if (event.type === "start") {
              if (started) throw new Error("收到重复的模型开始事件。");
              started = true;
            } else {
              if (!started) throw new Error("模型事件缺少开始标记。");
              if (event.type === "classification_status")
                view = {
                  ...view,
                  phase: event.stage === "jev" ? "正在识别任务" : "正在识别任务（Flash）",
                };
              if (event.type === "route_selected") view = { ...view, phase: "正在生成回答" };
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
        if ("result" in terminal) {
          const { type: _type, ...routedResult } = terminal;
          void _type;
          if (
            (routedResult.status === "completed" && routedResult.result?.status !== "completed") ||
            (routedResult.status !== "completed" && routedResult.result?.status === "completed")
          )
            throw new Error("路由结果与模型终态不一致。");
          routingResult = routedResult;
          final = routedResult.result
            ? { ...routedResult.result, routed: routedResult }
            : undefined;
          view = { ...view, status: terminal.status, result: final, routed: routedResult };
          if (terminal.error_code) {
            setError("本次调用未完成，请查看响应详情。");
            options?.onError?.(terminal.error_code);
          }
        } else {
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
        }
        publish();
      }
      if (result) {
        if (result.call_id !== callId) throw new Error("模型结果与当前调用不一致。");
        if ("intent" in result) {
          if (
            (result.status === "completed" && result.result?.status !== "completed") ||
            (result.status !== "completed" && result.result?.status === "completed")
          )
            throw new Error("路由结果与模型终态不一致。");
          routingResult = result;
          final = result.result ? { ...result.result, routed: result } : undefined;
          view = {
            ...view,
            text: result.result?.text ?? "",
            tools: result.result?.tool_calls ?? [],
            status: result.status,
            result: final,
            routed: result,
          };
          if (result.error_code) {
            setError("本次调用未完成，请查看响应详情。");
            options?.onError?.(result.error_code);
          }
        } else {
          final = result;
          view = {
            ...view,
            text: result.text,
            tools: result.tool_calls ?? [],
            status: result.status,
            result,
          };
        }
        publish();
      }
      if (final && routingResult) final.routed = routingResult;
      return final
        ? { ...final, traceId: view.traceId, traceSampled: view.traceSampled }
        : undefined;
    } catch (cause) {
      if (epoch.current !== current || !api.retainsContent) return;
      const canceled = controller.signal.aborted;
      view = {
        ...view,
        result: undefined,
        routed: undefined,
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
      options?.onError?.(cause instanceof ApiError ? cause.code : undefined);
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
