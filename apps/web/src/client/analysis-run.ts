import type { components } from "../generated/api";
import {
  autonomyAction,
  autonomyExtensions,
  autonomyResult,
  executionNode,
  parseAnalysisCancelAck,
  ProtocolError,
  readRun,
  runtimeFailure,
  toolPreparation,
  workflowProgress,
  type AnalysisCancelAck,
  type RunAutonomyAction,
  type RunAutonomyResult,
  type RunEvent,
  type RunFinished,
  type RunWorkflowProgress,
} from "../contracts/analysis-run";
import { traceInfo, type TraceInfo } from "../contracts/trace-context";
import { ApiError, type StreamRequestOptions } from "./request-scope";
import type { ExecutionNode } from "../contracts/execution-node";

type MainModelSelection = components["schemas"]["MainModelSelection"];
type ToolStarted = Extract<RunEvent, { type: "tool.started" }>;
type ToolFinished = Extract<RunEvent, { type: "tool.finished" }>;
export type WorkflowAssetRef = components["schemas"]["WorkflowAssetRef"];
export type RunConstraints = Partial<components["schemas"]["RunConstraints"]>;
export type AnalysisRunEntry = {
  workflowAssetRef?: WorkflowAssetRef;
  constraints?: RunConstraints;
};
type RawTerminal = RunFinished["payload"];
type AcknowledgedTerminalStatus = Extract<
  AnalysisCancelAck,
  { status: "already_terminal" }
>["terminalStatus"];

export type AnalysisHistoryItem = { question: string; answer: string };
export type AnalysisToolParams = ToolStarted["payload"]["params"];
export type AnalysisToolStatus =
  "running" | ToolFinished["payload"]["status"] | "canceled" | "incomplete";

export type AnalysisToolView = {
  stepId: string;
  toolCallId: string;
  toolIndex: number;
  toolKind: ToolStarted["payload"]["toolKind"];
  params: AnalysisToolParams;
  status: AnalysisToolStatus;
  rowCount?: number;
  errorCode?: string;
  inputPreview?: string | null;
  resultPreview?: string | null;
};

export type AnalysisStepView = {
  stepId: string;
  stepIndex: number;
  modelCallId: string;
  status: "running" | "completed" | "failed" | "canceled" | "incomplete";
  tools: AnalysisToolView[];
  preparingTools?: number[];
};

export type AnalysisFileView =
  | { status: "none" }
  | { status: "unavailable"; code: "file_limit" | "file_generation_failed" }
  | {
      status: "ready";
      stepId: string;
      toolCallId: string;
      filename: string;
      mediaType: "text/csv; charset=utf-8";
      rowCount: number;
      byteLength: number;
      blob: Blob;
    };

export type AnalysisTurnTerminal =
  | {
      status: "completed";
      finalAnswer: string;
      file: AnalysisFileView;
      artifacts?: components["schemas"]["RunArtifact"][];
    }
  | Extract<RawTerminal, { status: "waiting" | "failed" | "canceled" | "cleanup_unconfirmed" }>;

export type AnalysisStopState =
  "idle" | "waiting_for_run" | "requesting" | "accepted" | "already_terminal" | "unconfirmed";

export type AnalysisTurnView = TraceInfo & {
  id: string;
  requestId: string;
  runId?: string;
  question: string;
  modelLabel: string;
  providerId?: string;
  sceneId?: string;
  toolsEnabled?: boolean;
  steps: AnalysisStepView[];
  phase: "running" | "finished" | "interrupted";
  stopState: AnalysisStopState;
  acknowledgedTerminalStatus?: AcknowledgedTerminalStatus;
  terminal?: AnalysisTurnTerminal;
  errorMessage?: string;
  runtimeFailure?: ReturnType<typeof runtimeFailure>;
  plan?: RunAutonomyAction;
  workflow?: RunWorkflowProgress[];
  workflowDraft?: components["schemas"]["PublicWorkflow"];
  execution?: ExecutionNode[];
  latestExecutionId?: string;
  runStatus?: components["schemas"]["RunPublicSnapshot"]["status"];
  cancellationBlocked?: boolean;
  autonomy?: RunAutonomyResult;
  startedAtMs?: number;
  submittedAtMs?: number;
  durationMs?: number;
};

export type AnalysisRunScope = { tenantId: string; workspaceId: string };

type AnalysisRunTransport = {
  readonly retainsContent: boolean;
  streamRequest<T>(
    path: string,
    body: Record<string, unknown>,
    options: StreamRequestOptions<T>,
  ): Promise<T>;
  request<T>(
    path: string,
    method?: string,
    body?: unknown,
    options?: { retainOnNotFound?: boolean; signal?: AbortSignal; timeoutMs?: number },
  ): Promise<T>;
};

type AnalysisRunClientOptions = {
  api: AnalysisRunTransport;
  scope: AnalysisRunScope;
  question: string;
  history: AnalysisHistoryItem[];
  selection: MainModelSelection;
  entry?: AnalysisRunEntry;
  modelLabel: string;
  onChange: (turn: AnalysisTurnView) => void;
  onSettled?: (client: AnalysisRunClient) => void;
  requestId?: string;
};

const encoder = new TextEncoder();
const HISTORY_BYTES = 32 * 1024;
const HISTORY_PAIRS = 12;

function normalizeHistoryItem(item: AnalysisHistoryItem): AnalysisHistoryItem {
  return { question: item.question.trim(), answer: item.answer.trim() };
}

function contentBytes(item: AnalysisHistoryItem) {
  return encoder.encode(item.question).byteLength + encoder.encode(item.answer).byteLength + 2;
}

/** Keep a contiguous suffix of complete turns; incomplete and internal tool messages never enter it. */
export function selectAnalysisHistory(
  items: readonly AnalysisHistoryItem[],
  question: string,
): AnalysisHistoryItem[] {
  let remaining = HISTORY_BYTES - encoder.encode(question.trim()).byteLength;
  if (remaining < 0) throw new ApiError(422, "问题内容过长，请精简后重试。");
  const selected: AnalysisHistoryItem[] = [];
  for (let index = items.length - 1; index >= 0 && selected.length < HISTORY_PAIRS; index -= 1) {
    const item = normalizeHistoryItem(items[index]);
    const size = contentBytes(item);
    if (size > remaining) break;
    selected.push(item);
    remaining -= size;
  }
  return selected.reverse();
}

export function completedAnalysisHistory(turns: readonly AnalysisTurnView[]) {
  return turns.flatMap<AnalysisHistoryItem>((turn) =>
    turn.terminal?.status === "completed"
      ? [{ question: turn.question, answer: turn.terminal.finalAnswer }]
      : [],
  );
}

function findStep(turn: AnalysisTurnView, stepId: string) {
  const index = turn.steps.findIndex((step) => step.stepId === stepId);
  if (index < 0) throw new ProtocolError("ui_unknown_step");
  return index;
}

function decodeFile(file: Extract<RawTerminal, { status: "completed" }>["file"]): AnalysisFileView {
  if (file.status !== "ready") return file;
  let binary: string;
  try {
    binary = atob(file.contentBase64);
  } catch {
    throw new ProtocolError("file_base64");
  }
  if (binary.length !== file.byteLength) throw new ProtocolError("file_length");
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  return {
    status: "ready",
    stepId: file.stepId,
    toolCallId: file.toolCallId,
    filename: file.filename,
    mediaType: file.mediaType,
    rowCount: file.rowCount,
    byteLength: file.byteLength,
    blob: new Blob([bytes], { type: file.mediaType }),
  };
}

function terminalView(payload: RawTerminal): AnalysisTurnTerminal {
  if (payload.status !== "completed") return payload;
  return {
    status: "completed",
    finalAnswer: payload.finalAnswer,
    file: decodeFile(payload.file),
    artifacts: payload.artifacts ?? [],
  };
}

function closeInterruptedSteps(turn: AnalysisTurnView): AnalysisStepView[] {
  return turn.steps.map((step) => ({
    ...step,
    status: step.status === "running" ? "incomplete" : step.status,
    tools: step.tools.map((tool) =>
      tool.status === "running" ? { ...tool, status: "incomplete" } : tool,
    ),
  }));
}

export function confirmCanceledSteps(steps: readonly AnalysisStepView[]): AnalysisStepView[] {
  return steps.map((step) => ({
    ...step,
    status: step.status === "running" || step.status === "incomplete" ? "canceled" : step.status,
    tools: step.tools.map((tool) =>
      tool.status === "running" || tool.status === "incomplete"
        ? { ...tool, status: "canceled" }
        : tool,
    ),
  }));
}

export function interruptAnalysisTurn(turn: AnalysisTurnView, message: string): AnalysisTurnView {
  return {
    ...turn,
    phase: "interrupted",
    terminal: undefined,
    steps: closeInterruptedSteps(turn),
    durationMs: elapsed(turn),
    errorMessage: message,
  };
}

function elapsed(turn: AnalysisTurnView): number | undefined {
  return turn.startedAtMs === undefined
    ? undefined
    : Math.max(0, performance.now() - turn.startedAtMs);
}

/** Apply already protocol-validated logical events to the browser-facing turn. */
export function applyAnalysisRunEvent(
  turn: AnalysisTurnView,
  runId: string,
  event: RunEvent,
): AnalysisTurnView {
  if (turn.terminal) throw new ProtocolError("ui_event_after_terminal");
  if (turn.runId && turn.runId !== runId) throw new ProtocolError("ui_run_identity");
  switch (event.type) {
    case "run.started":
      return { ...turn, runId };
    case "run.routed":
      return {
        ...turn,
        runId,
        providerId: event.payload.providerId,
        modelLabel: event.payload.modelLabel,
        sceneId: event.payload.sceneId,
        toolsEnabled: event.payload.toolsEnabled,
      };
    case "step.started":
      return {
        ...turn,
        runId,
        latestExecutionId: event.payload.stepId,
        steps: [
          ...turn.steps,
          {
            ...event.payload,
            status: "running",
            tools: [],
          },
        ],
      };
    case "tool.started": {
      const stepIndex = findStep(turn, event.payload.stepId);
      const step = turn.steps[stepIndex];
      const steps = [...turn.steps];
      steps[stepIndex] = {
        ...step,
        tools: [
          ...step.tools,
          {
            ...event.payload,
            status: "running",
          },
        ],
      };
      return {
        ...turn,
        runId,
        steps,
        latestExecutionId: `${step.stepId}:${event.payload.toolIndex}`,
      };
    }
    case "tool.finished": {
      const stepIndex = findStep(turn, event.payload.stepId);
      const step = turn.steps[stepIndex];
      const toolIndex = step.tools.findIndex(
        (tool) => tool.toolCallId === event.payload.toolCallId,
      );
      if (toolIndex < 0) throw new ProtocolError("ui_unknown_tool");
      const tools = [...step.tools];
      tools[toolIndex] = { ...tools[toolIndex], ...event.payload };
      const steps = [...turn.steps];
      steps[stepIndex] = { ...step, tools };
      return {
        ...turn,
        runId,
        steps,
        latestExecutionId: `${step.stepId}:${event.payload.toolIndex}`,
      };
    }
    case "step.finished": {
      const stepIndex = findStep(turn, event.payload.stepId);
      const step = turn.steps[stepIndex];
      const steps = [...turn.steps];
      steps[stepIndex] = {
        ...step,
        status: event.payload.status === "canceled" ? "incomplete" : event.payload.status,
        tools: step.tools.map((tool) =>
          tool.status === "running" ? { ...tool, status: "incomplete" } : tool,
        ),
      };
      return { ...turn, runId, steps, latestExecutionId: step.stepId };
    }
    case "stream.extension": {
      const failure = runtimeFailure(event);
      if (failure) return { ...turn, runtimeFailure: failure };
      const node = executionNode(event);
      if (node) {
        const nodes = turn.execution ?? [];
        const exists = nodes.some((item) => item.id === node.id);
        return {
          ...turn,
          runId,
          latestExecutionId: node.id,
          execution: exists
            ? nodes.map((item) => (item.id === node.id ? node : item))
            : [...nodes, node],
        };
      }
      const preparation = toolPreparation(event);
      if (preparation) {
        const stepIndex = findStep(turn, preparation.stepId);
        const steps = [...turn.steps];
        const step = steps[stepIndex];
        const indices = new Set(step.preparingTools);
        if (preparation.status === "preparing") indices.add(preparation.toolIndex);
        else indices.delete(preparation.toolIndex);
        steps[stepIndex] = { ...step, preparingTools: [...indices] };
        return {
          ...turn,
          runId,
          steps,
          latestExecutionId:
            preparation.status === "preparing"
              ? `${step.stepId}:${preparation.toolIndex}`
              : step.stepId,
        };
      }
      const progress = workflowProgress(event);
      if (progress) {
        const nodes = turn.workflow ?? [];
        const index = nodes.findIndex(
          (node) => node.source === progress.source && node.key === progress.key,
        );
        return {
          ...turn,
          latestExecutionId: `${progress.source}:${progress.key}`,
          workflow:
            index < 0
              ? [...nodes, progress]
              : nodes.map((node, position) => (position === index ? progress : node)),
        };
      }
      const plan = autonomyAction(event);
      if (plan) return { ...turn, plan };
      const autonomy = autonomyResult(event);
      return autonomy ? { ...turn, autonomy } : turn;
    }
    case "stream.batch":
      return event.payload.reduce(
        (current, item) => applyAnalysisRunEvent(current, runId, item),
        turn,
      );
    case "run.finished":
      return {
        ...turn,
        runId,
        steps: event.payload.status === "canceled" ? confirmCanceledSteps(turn.steps) : turn.steps,
        phase: "finished",
        durationMs: event.durationMs ?? elapsed(turn),
        terminal: terminalView(event.payload),
        errorMessage: undefined,
      };
  }
}

const terminalStatusLabels: Record<AcknowledgedTerminalStatus, string> = {
  completed: "完成",
  waiting: "等待人工决策",
  failed: "失败",
  canceled: "终止",
  cleanup_unconfirmed: "清理待确认",
};

function failureMessage(error: unknown, turn: AnalysisTurnView) {
  if (turn.acknowledgedTerminalStatus)
    return `服务端已确认任务以“${terminalStatusLabels[turn.acknowledgedTerminalStatus]}”结束，但原结果流不完整，无法安全提交最终回答或文件。`;
  if (turn.stopState !== "idle") return "连接在终止期间中断，尚未收到服务端的清理确认。";
  if (error instanceof ApiError) return error.message;
  return "运行连接已中断，本轮结果不完整。已收到的分析过程仍可查看。";
}

export class AnalysisRunClient {
  private readonly api: AnalysisRunTransport;
  private readonly scope: AnalysisRunScope;
  private readonly history: AnalysisHistoryItem[];
  private readonly selection: MainModelSelection;
  private readonly entry?: AnalysisRunEntry;
  private readonly onChange: (turn: AnalysisTurnView) => void;
  private readonly onSettled?: (client: AnalysisRunClient) => void;
  private readonly streamController = new AbortController();
  private cancelController: AbortController | null = null;
  private cancelPromise: Promise<void> | null = null;
  private started = false;
  private disposed = false;
  private publishFrame: number | undefined;
  private turn: AnalysisTurnView;

  constructor(options: AnalysisRunClientOptions) {
    const requestId = options.requestId ?? crypto.randomUUID();
    this.api = options.api;
    this.scope = options.scope;
    this.history = selectAnalysisHistory(options.history, options.question);
    this.selection = options.selection;
    this.entry = options.entry;
    this.onChange = options.onChange;
    this.onSettled = options.onSettled;
    this.turn = {
      id: requestId,
      requestId,
      question: options.question,
      modelLabel: options.modelLabel,
      steps: [],
      phase: "running",
      stopState: "idle",
    };
  }

  get snapshot() {
    return this.turn;
  }

  async start() {
    if (this.started) throw new Error("Analysis run already started");
    this.started = true;
    this.turn = { ...this.turn, startedAtMs: performance.now() };
    this.publish();
    try {
      await this.api.streamRequest(
        "analysis/runs",
        {
          ...this.scope,
          requestId: this.turn.requestId,
          question: this.turn.question,
          history: this.history,
          selection: this.selection,
          ...this.entry,
        },
        {
          signal: this.streamController.signal,
          timeoutMs: 200000,
          inputLimitBytes: 64 * 1024,
          unavailableMessage: "Agent 运行服务暂时不可用，请稍后重试。",
          onHeaders: (headers) => {
            this.turn = { ...this.turn, ...traceInfo(headers) };
          },
          consume: async (body, signal) => {
            for await (const { runId, event } of readRun(body, {
              requestId: this.turn.requestId,
              signal,
              extensions: autonomyExtensions,
            })) {
              if (this.disposed) throw new DOMException("页面已清理。", "AbortError");
              if (
                event.type === "run.finished" &&
                this.turn.stopState === "accepted" &&
                ["completed", "waiting"].includes(event.payload.status)
              )
                throw new ProtocolError("cancel_accepted_completed");
              if (
                event.type === "run.finished" &&
                this.turn.acknowledgedTerminalStatus &&
                this.turn.acknowledgedTerminalStatus !== event.payload.status
              )
                throw new ProtocolError("cancel_terminal_status_mismatch");
              this.turn = applyAnalysisRunEvent(this.turn, runId, event);
              this.publish(event.type === "run.finished");
              if (event.type === "run.finished")
                this.cancelController?.abort(new DOMException("运行已返回终态。", "AbortError"));
              if (event.type === "run.started" && this.turn.stopState === "waiting_for_run")
                void this.submitCancel();
            }
          },
        },
      );
    } catch (error) {
      if (!this.disposed && this.api.retainsContent && !this.turn.terminal) {
        this.cancelController?.abort(new DOMException("运行连接已中断。", "AbortError"));
        this.turn = interruptAnalysisTurn(this.turn, failureMessage(error, this.turn));
        this.publish();
      }
    } finally {
      this.onSettled?.(this);
    }
    return this.turn;
  }

  requestStop() {
    if (
      this.disposed ||
      this.turn.terminal ||
      this.turn.phase !== "running" ||
      ["waiting_for_run", "requesting", "accepted", "already_terminal"].includes(
        this.turn.stopState,
      )
    )
      return;
    this.turn = {
      ...this.turn,
      stopState: this.turn.runId ? "requesting" : "waiting_for_run",
      errorMessage: undefined,
    };
    this.publish();
    if (this.turn.runId) void this.submitCancel();
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    if (this.publishFrame !== undefined) cancelAnimationFrame(this.publishFrame);
    this.streamController.abort(new DOMException("页面已清理。", "AbortError"));
    this.cancelController?.abort(new DOMException("页面已清理。", "AbortError"));
  }

  private publish(immediate = true) {
    if (this.disposed || !this.api.retainsContent) return;
    if (!immediate && typeof requestAnimationFrame === "function") {
      this.publishFrame ??= requestAnimationFrame(() => {
        this.publishFrame = undefined;
        this.publish();
      });
      return;
    }
    if (this.publishFrame !== undefined) {
      cancelAnimationFrame(this.publishFrame);
      this.publishFrame = undefined;
    }
    if (!this.disposed && this.api.retainsContent) this.onChange(this.turn);
  }

  private async submitCancel() {
    const runId = this.turn.runId;
    if (
      !runId ||
      this.disposed ||
      this.turn.terminal ||
      ["accepted", "already_terminal"].includes(this.turn.stopState) ||
      this.cancelPromise
    )
      return;
    this.turn = { ...this.turn, stopState: "requesting", errorMessage: undefined };
    this.publish();
    const controller = new AbortController();
    this.cancelController = controller;
    const request = (async () => {
      try {
        const raw = await this.api.request<unknown>(
          `analysis/runs/${encodeURIComponent(runId)}/cancel`,
          "POST",
          this.scope,
          { retainOnNotFound: true, signal: controller.signal, timeoutMs: 6000 },
        );
        const ack = parseAnalysisCancelAck(raw);
        if (ack.runId !== runId) throw new ProtocolError("cancel_ack_identity");
        if (
          this.disposed ||
          this.turn.terminal ||
          this.turn.phase !== "running" ||
          !this.api.retainsContent
        )
          return;
        this.turn =
          ack.status === "already_terminal"
            ? {
                ...this.turn,
                stopState: "already_terminal",
                acknowledgedTerminalStatus: ack.terminalStatus,
                errorMessage: undefined,
              }
            : { ...this.turn, stopState: "accepted", errorMessage: undefined };
        this.publish();
      } catch (error) {
        if (
          this.disposed ||
          this.turn.terminal ||
          this.turn.phase !== "running" ||
          !this.api.retainsContent
        )
          return;
        this.turn = {
          ...this.turn,
          stopState: "unconfirmed",
          errorMessage:
            error instanceof ApiError
              ? `终止请求未确认：${error.message}`
              : "终止请求未确认，请重试停止或等待本轮结束。",
        };
        this.publish();
      }
    })();
    this.cancelPromise = request;
    try {
      await request;
    } finally {
      if (this.cancelController === controller) this.cancelController = null;
      if (this.cancelPromise === request) this.cancelPromise = null;
    }
  }
}
