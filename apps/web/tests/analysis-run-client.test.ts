import { describe, expect, it, vi } from "vitest";
import {
  AnalysisRunClient,
  completedAnalysisHistory,
  selectAnalysisHistory,
  type AnalysisHistoryItem,
  type AnalysisTurnView,
} from "../src/client/analysis-run";
import { ApiError, type StreamRequestOptions } from "../src/client/request-scope";
import {
  encodeRunPacket,
  RUN_STREAM_PROTOCOL,
  RUN_STREAM_VERSION,
  type RunEvent,
} from "../src/contracts/analysis-run";

const requestId = "98900000-0000-4000-8000-000000000101";
const runId = "98900000-0000-4000-8000-000000000102";
const providerId = "98900000-0000-4000-8000-000000000103";
const channelStepId = "98900000-0000-4000-8000-000000000104";
const productStepId = "98900000-0000-4000-8000-000000000105";
const channelModelCallId = "98900000-0000-4000-8000-000000000106";
const productModelCallId = "98900000-0000-4000-8000-000000000107";
const channelToolCallId = "channel_query";
const productToolCallId = "product_query";

function frame(event: RunEvent, sequence: number) {
  return encodeRunPacket({
    protocol: RUN_STREAM_PROTOCOL,
    version: RUN_STREAM_VERSION,
    requestId,
    runId,
    seq: sequence,
    event,
  });
}

function body(events: RunEvent[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      events.forEach((event, index) => controller.enqueue(frame(event, index + 1)));
      controller.close();
    },
  });
}

function successfulEvents(): RunEvent[] {
  const csv = "product,amount\nA,120\n";
  return [
    { type: "run.started", payload: {} },
    {
      type: "run.routed",
      payload: {
        providerId,
        modelLabel: "DeepSeek V4 Pro",
        sceneId: "SC-02",
        toolsEnabled: true,
      },
    },
    {
      type: "step.started",
      payload: { stepId: channelStepId, stepIndex: 1, modelCallId: channelModelCallId },
    },
    {
      type: "tool.started",
      payload: {
        stepId: channelStepId,
        toolCallId: channelToolCallId,
        toolIndex: 1,
        toolKind: "retail_sales",
        params: {
          dimension: "channel",
          period: { start: "2025-11-24", end: "2025-11-30" },
          compareTo: { start: "2025-11-17", end: "2025-11-23" },
        },
      },
    },
    {
      type: "tool.finished",
      payload: {
        stepId: channelStepId,
        toolCallId: channelToolCallId,
        toolIndex: 1,
        status: "ok",
        rowCount: 2,
      },
    },
    { type: "step.finished", payload: { stepId: channelStepId, status: "completed" } },
    {
      type: "step.started",
      payload: { stepId: productStepId, stepIndex: 2, modelCallId: productModelCallId },
    },
    {
      type: "tool.started",
      payload: {
        stepId: productStepId,
        toolCallId: productToolCallId,
        toolIndex: 1,
        toolKind: "retail_sales",
        params: {
          dimension: "product",
          channelCode: "POS",
          period: { start: "2025-11-24", end: "2025-11-30" },
          compareTo: { start: "2025-11-17", end: "2025-11-23" },
        },
      },
    },
    {
      type: "tool.finished",
      payload: {
        stepId: productStepId,
        toolCallId: productToolCallId,
        toolIndex: 1,
        status: "ok",
        rowCount: 1,
      },
    },
    { type: "step.finished", payload: { stepId: productStepId, status: "completed" } },
    {
      type: "run.finished",
      payload: {
        status: "completed",
        finalAnswer: "POS 渠道增长最多，主要由商品 A 拉动。",
        file: {
          status: "ready",
          stepId: productStepId,
          toolCallId: productToolCallId,
          filename: `retail-products-${runId}.csv`,
          mediaType: "text/csv; charset=utf-8",
          rowCount: 1,
          byteLength: csv.length,
          contentBase64: btoa(csv),
        },
      },
    },
  ];
}

function unfinishedToolEvents(
  terminal: Extract<RunEvent, { type: "run.finished" }>["payload"] | undefined = undefined,
): RunEvent[] {
  const events: RunEvent[] = [
    { type: "run.started", payload: {} },
    {
      type: "step.started",
      payload: { stepId: channelStepId, stepIndex: 1, modelCallId: channelModelCallId },
    },
    {
      type: "tool.started",
      payload: {
        stepId: channelStepId,
        toolCallId: channelToolCallId,
        toolIndex: 1,
        toolKind: "retail_sales",
        params: {
          dimension: "channel",
          period: { start: "2025-11-24", end: "2025-11-30" },
          compareTo: null,
        },
      },
    },
    { type: "step.finished", payload: { stepId: channelStepId, status: "canceled" } },
  ];
  if (terminal) events.push({ type: "run.finished", payload: terminal });
  return events;
}

type RequestCall = {
  path: string;
  method: string | undefined;
  body: unknown;
};
type RequestOptions = { retainOnNotFound?: boolean; signal?: AbortSignal; timeoutMs?: number };

function transport(
  stream: ReadableStream<Uint8Array>,
  cancel: (options?: RequestOptions) => Promise<unknown> = async () => ({
    runId,
    status: "accepted",
  }),
) {
  const streamBodies: Record<string, unknown>[] = [];
  const requestCalls: RequestCall[] = [];
  return {
    retainsContent: true,
    streamBodies,
    requestCalls,
    async streamRequest<T>(
      _path: string,
      requestBody: Record<string, unknown>,
      options: StreamRequestOptions<T>,
    ) {
      streamBodies.push(requestBody);
      return options.consume(stream, options.signal);
    },
    async request<T>(
      path: string,
      method?: string,
      requestBody?: unknown,
      options?: RequestOptions,
    ) {
      requestCalls.push({ path, method, body: requestBody });
      return (await cancel(options)) as T;
    },
  };
}

function clientFor(api: ReturnType<typeof transport>, onChange: (turn: AnalysisTurnView) => void) {
  return new AnalysisRunClient({
    api,
    scope: { tenantId: "tenant-1", workspaceId: "workspace-1" },
    question: "比较渠道并解释商品贡献",
    history: [{ question: "上一问", answer: "上一答" }],
    selection: { mode: "fixed", provider_id: providerId },
    modelLabel: "DeepSeek V4 Pro",
    requestId,
    onChange,
  });
}

describe("analysis run browser client", () => {
  it("keeps the runtime failure diagnostic on the failed turn", async () => {
    const diagnostic = {
      stage: "tool_setup",
      exceptionType: "UndefinedColumn",
      reasonCode: "database_schema_mismatch",
    };
    const result = await clientFor(
      transport(
        body([
          { type: "run.started", payload: {} },
          {
            type: "stream.extension",
            payload: {
              name: "compass.runtime.failure",
              version: 1,
              required: false,
              data: diagnostic,
            },
          },
          { type: "run.finished", payload: { status: "failed", errorCode: "internal_error" } },
        ]),
      ),
      () => undefined,
    ).start();
    expect(result.runtimeFailure).toEqual(diagnostic);
    expect(result.terminal?.status).toBe("failed");
  });

  it("projects one immutable card per tool and exposes the answer and CSV only after clean EOF", async () => {
    const changes: AnalysisTurnView[] = [];
    const api = transport(body(successfulEvents()));
    const result = await clientFor(api, (turn) => changes.push(turn)).start();

    expect(result.phase).toBe("finished");
    expect(result.steps.map((step) => step.tools.length)).toEqual([1, 1]);
    expect(result.steps[0].tools[0]).toMatchObject({
      toolCallId: channelToolCallId,
      status: "ok",
      rowCount: 2,
    });
    expect(result.terminal).toMatchObject({
      status: "completed",
      finalAnswer: "POS 渠道增长最多，主要由商品 A 拉动。",
      file: { status: "ready", rowCount: 1 },
    });
    if (result.terminal?.status !== "completed" || result.terminal.file.status !== "ready")
      throw new Error("expected a ready CSV");
    expect(await result.terminal.file.blob.text()).toBe("product,amount\nA,120\n");
    expect(changes.filter((turn) => turn.terminal).map((turn) => turn.terminal?.status)).toEqual([
      "completed",
    ]);
    expect(api.streamBodies[0]).toMatchObject({
      requestId,
      question: "比较渠道并解释商品贡献",
      history: [{ question: "上一问", answer: "上一答" }],
      selection: { mode: "fixed", provider_id: providerId },
    });
    expect(completedAnalysisHistory([result])).toEqual([
      {
        question: "比较渠道并解释商品贡献",
        answer: "POS 渠道增长最多，主要由商品 A 拉动。",
      },
    ]);
  });

  it("commits a zero-tool answer without inventing process cards", async () => {
    const events: RunEvent[] = [
      { type: "run.started", payload: {} },
      {
        type: "run.routed",
        payload: {
          providerId,
          modelLabel: "DeepSeek V4 Pro",
          sceneId: "SC-02",
          toolsEnabled: false,
        },
      },
      {
        type: "step.started",
        payload: { stepId: channelStepId, stepIndex: 1, modelCallId: channelModelCallId },
      },
      { type: "step.finished", payload: { stepId: channelStepId, status: "completed" } },
      {
        type: "run.finished",
        payload: {
          status: "completed",
          finalAnswer: "这是无需查询工具即可回答的问题。",
          file: { status: "none" },
        },
      },
    ];

    const result = await clientFor(transport(body(events)), () => undefined).start();

    expect(result.steps).toEqual([
      expect.objectContaining({ stepId: channelStepId, status: "completed", tools: [] }),
    ]);
    expect(result.terminal).toEqual({
      status: "completed",
      finalAnswer: "这是无需查询工具即可回答的问题。",
      artifacts: [],
      file: { status: "none" },
    });
  });

  it("remembers an early stop and sends exactly one separate cancellation after run.started", async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    const api = transport(stream);
    const changes: AnalysisTurnView[] = [];
    const client = clientFor(api, (turn) => changes.push(turn));
    const pending = client.start();

    client.requestStop();
    expect(client.snapshot.stopState).toBe("waiting_for_run");
    streamController.enqueue(frame({ type: "run.started", payload: {} }, 1));
    await vi.waitFor(() => expect(api.requestCalls).toHaveLength(1));
    expect(api.requestCalls[0]).toEqual({
      path: `analysis/runs/${runId}/cancel`,
      method: "POST",
      body: { tenantId: "tenant-1", workspaceId: "workspace-1" },
    });
    await vi.waitFor(() => expect(client.snapshot.stopState).toBe("accepted"));
    client.requestStop();
    expect(api.requestCalls).toHaveLength(1);
    streamController.enqueue(
      frame(
        {
          type: "run.finished",
          payload: { status: "canceled", reason: "user", cleanup: "confirmed" },
        },
        2,
      ),
    );
    streamController.close();

    await expect(pending).resolves.toMatchObject({
      phase: "finished",
      stopState: "accepted",
      terminal: { status: "canceled", cleanup: "confirmed" },
    });
    expect(changes.some((turn) => turn.stopState === "waiting_for_run")).toBe(true);
  });

  it("keeps the original stream alive when cancellation is unconfirmed and allows a retry", async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    let attempt = 0;
    const api = transport(stream, async () => {
      attempt += 1;
      if (attempt === 1) throw new ApiError(503, "终止服务暂时不可用");
      return { runId, status: "accepted" };
    });
    const client = clientFor(api, () => undefined);
    const pending = client.start();
    streamController.enqueue(frame({ type: "run.started", payload: {} }, 1));
    await vi.waitFor(() => expect(client.snapshot.runId).toBe(runId));

    client.requestStop();
    await vi.waitFor(() => expect(client.snapshot.stopState).toBe("unconfirmed"));
    expect(client.snapshot.phase).toBe("running");
    client.requestStop();
    await vi.waitFor(() => expect(client.snapshot.stopState).toBe("accepted"));
    expect(api.requestCalls).toHaveLength(2);
    streamController.enqueue(
      frame(
        {
          type: "run.finished",
          payload: { status: "canceled", reason: "user", cleanup: "confirmed" },
        },
        2,
      ),
    );
    streamController.close();
    await pending;
  });

  it("rejects a completed terminal after cancellation was accepted", async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    const api = transport(stream);
    const changes: AnalysisTurnView[] = [];
    const client = clientFor(api, (turn) => changes.push(turn));
    const pending = client.start();
    const events = successfulEvents();
    const completed = events.pop()!;
    events.forEach((event, index) => streamController.enqueue(frame(event, index + 1)));
    await vi.waitFor(() => expect(client.snapshot.steps).toHaveLength(2));

    client.requestStop();
    await vi.waitFor(() => expect(client.snapshot.stopState).toBe("accepted"));
    streamController.enqueue(frame(completed, events.length + 1));
    streamController.close();

    const result = await pending;
    expect(result).toMatchObject({ phase: "interrupted", stopState: "accepted" });
    expect(result.terminal).toBeUndefined();
    expect(result.errorMessage).toContain("终止期间中断");
    expect(changes.some((turn) => turn.terminal?.status === "completed")).toBe(false);
    expect(completedAnalysisHistory([result])).toEqual([]);
  });

  it.each([
    {
      label: "cleanup confirmation failure",
      terminal: {
        status: "cleanup_unconfirmed",
        reason: "execution_error",
        cleanup: "unconfirmed",
      } as const,
    },
    {
      label: "deadline boundary",
      terminal: { status: "failed", errorCode: "run_timeout" } as const,
    },
  ])("preserves $label after cancellation was accepted", async ({ terminal }) => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    const client = clientFor(transport(stream), () => undefined);
    const pending = client.start();
    streamController.enqueue(frame({ type: "run.started", payload: {} }, 1));
    await vi.waitFor(() => expect(client.snapshot.runId).toBe(runId));

    client.requestStop();
    await vi.waitFor(() => expect(client.snapshot.stopState).toBe("accepted"));
    streamController.enqueue(frame({ type: "run.finished", payload: terminal }, 2));
    streamController.close();

    await expect(pending).resolves.toMatchObject({
      phase: "finished",
      stopState: "accepted",
      terminal,
    });
  });

  it("keeps an unfinished tool incomplete until a confirmed canceled terminal arrives", async () => {
    const changes: AnalysisTurnView[] = [];
    const result = await clientFor(
      transport(
        body(
          unfinishedToolEvents({
            status: "canceled",
            reason: "user",
            cleanup: "confirmed",
          }),
        ),
      ),
      (turn) => changes.push(turn),
    ).start();

    const beforeTerminal = changes.find(
      (turn) => turn.steps[0]?.status === "incomplete" && !turn.terminal,
    );
    expect(beforeTerminal?.steps[0]).toMatchObject({
      status: "incomplete",
      tools: [{ status: "incomplete" }],
    });
    expect(result).toMatchObject({
      terminal: { status: "canceled", cleanup: "confirmed" },
      steps: [{ status: "canceled", tools: [{ status: "canceled" }] }],
    });
  });

  it("never presents unfinished work as canceled when cleanup is unconfirmed", async () => {
    const changes: AnalysisTurnView[] = [];
    const result = await clientFor(
      transport(
        body(
          unfinishedToolEvents({
            status: "cleanup_unconfirmed",
            reason: "execution_error",
            cleanup: "unconfirmed",
          }),
        ),
      ),
      (turn) => changes.push(turn),
    ).start();

    expect(result).toMatchObject({
      terminal: { status: "cleanup_unconfirmed" },
      steps: [{ status: "incomplete", tools: [{ status: "incomplete" }] }],
    });
    expect(
      changes.some((turn) =>
        turn.steps.some(
          (step) =>
            step.status === "canceled" || step.tools.some((tool) => tool.status === "canceled"),
        ),
      ),
    ).toBe(false);
  });

  it("records already_terminal without committing early, then trusts only the clean original stream", async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    const api = transport(stream, async () => ({
      runId,
      status: "already_terminal",
      terminalStatus: "completed",
    }));
    const client = clientFor(api, () => undefined);
    const pending = client.start();
    streamController.enqueue(frame({ type: "run.started", payload: {} }, 1));
    await vi.waitFor(() => expect(client.snapshot.runId).toBe(runId));

    client.requestStop();
    await vi.waitFor(() => expect(client.snapshot.stopState).toBe("already_terminal"));
    expect(client.snapshot).toMatchObject({
      phase: "running",
      acknowledgedTerminalStatus: "completed",
    });
    expect(client.snapshot.terminal).toBeUndefined();
    expect(completedAnalysisHistory([client.snapshot])).toEqual([]);

    streamController.enqueue(
      frame(
        {
          type: "step.started",
          payload: { stepId: channelStepId, stepIndex: 1, modelCallId: channelModelCallId },
        },
        2,
      ),
    );
    streamController.enqueue(
      frame({ type: "step.finished", payload: { stepId: channelStepId, status: "completed" } }, 3),
    );
    streamController.enqueue(
      frame(
        {
          type: "run.finished",
          payload: {
            status: "completed",
            finalAnswer: "原流在干净 EOF 后提交的回答。",
            file: { status: "none" },
          },
        },
        4,
      ),
    );
    streamController.close();

    const result = await pending;
    expect(result).toMatchObject({
      phase: "finished",
      acknowledgedTerminalStatus: "completed",
      terminal: { status: "completed", finalAnswer: "原流在干净 EOF 后提交的回答。" },
    });
    expect(completedAnalysisHistory([result])).toHaveLength(1);
  });

  it("reports a confirmed server terminal but withholds results when its original stream breaks", async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    const api = transport(stream, async () => ({
      runId,
      status: "already_terminal",
      terminalStatus: "completed",
    }));
    const client = clientFor(api, () => undefined);
    const pending = client.start();
    streamController.enqueue(frame({ type: "run.started", payload: {} }, 1));
    const [, stepStarted, toolStarted] = unfinishedToolEvents();
    streamController.enqueue(frame(stepStarted, 2));
    streamController.enqueue(frame(toolStarted, 3));
    await vi.waitFor(() => expect(client.snapshot.steps[0]?.tools).toHaveLength(1));
    client.requestStop();
    await vi.waitFor(() => expect(client.snapshot.stopState).toBe("already_terminal"));
    streamController.close();

    const result = await pending;
    expect(result).toMatchObject({
      phase: "interrupted",
      stopState: "already_terminal",
      acknowledgedTerminalStatus: "completed",
    });
    expect(result.terminal).toBeUndefined();
    expect(result.errorMessage).toContain("服务端已确认");
    expect(result.errorMessage).toContain("原结果流不完整");
    expect(completedAnalysisHistory([result])).toEqual([]);
  });

  it("preserves validated progress but suppresses answer, file, and history after a truncated stream", async () => {
    const events = successfulEvents().slice(0, 4);
    const api = transport(body(events));
    const result = await clientFor(api, () => undefined).start();

    expect(result.phase).toBe("interrupted");
    expect(result.terminal).toBeUndefined();
    expect(result.steps[0]).toMatchObject({
      status: "incomplete",
      tools: [{ status: "incomplete", toolCallId: channelToolCallId }],
    });
    expect(result.errorMessage).toContain("连接已中断");
    expect(completedAnalysisHistory([result])).toEqual([]);
  });

  it("abandons an obsolete cancellation request when the original stream disconnects", async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    let cancelSignal!: AbortSignal;
    const api = transport(
      stream,
      async (options) =>
        new Promise((_resolve, reject) => {
          cancelSignal = options!.signal!;
          const aborted = () => reject(cancelSignal.reason);
          if (cancelSignal.aborted) aborted();
          else cancelSignal.addEventListener("abort", aborted, { once: true });
        }),
    );
    const client = clientFor(api, () => undefined);
    const pending = client.start();
    streamController.enqueue(frame({ type: "run.started", payload: {} }, 1));
    const [, stepStarted, toolStarted] = unfinishedToolEvents();
    streamController.enqueue(frame(stepStarted, 2));
    streamController.enqueue(frame(toolStarted, 3));
    await vi.waitFor(() => expect(client.snapshot.steps[0]?.tools).toHaveLength(1));
    client.requestStop();
    await vi.waitFor(() => expect(api.requestCalls).toHaveLength(1));

    streamController.close();
    const result = await pending;

    expect(cancelSignal.aborted).toBe(true);
    expect(result).toMatchObject({
      phase: "interrupted",
      stopState: "requesting",
      steps: [{ status: "incomplete", tools: [{ status: "incomplete" }] }],
    });
    expect(result.errorMessage).toContain("终止期间中断");
    await Promise.resolve();
    expect(client.snapshot.stopState).toBe("requesting");
  });

  it("selects a bounded contiguous suffix of complete history", () => {
    const items: AnalysisHistoryItem[] = Array.from({ length: 14 }, (_, index) => ({
      question: `问题 ${index + 1}`,
      answer: `回答 ${index + 1}`,
    }));
    expect(selectAnalysisHistory(items, "当前问题").map((item) => item.question)).toEqual(
      items.slice(-12).map((item) => item.question),
    );
    expect(
      selectAnalysisHistory([...items, { question: "超长", answer: "答".repeat(33_000) }], "问"),
    ).toEqual([]);
    expect(() => selectAnalysisHistory([], "问".repeat(33_000))).toThrow(ApiError);
  });

  it("matches the BFF newline-joined 32 KiB history boundary exactly", () => {
    const atLimit = { question: "h", answer: "x".repeat(32_764) };
    const overLimit = { question: "h", answer: "x".repeat(32_765) };

    expect(selectAnalysisHistory([atLimit], "q")).toEqual([atLimit]);
    expect(selectAnalysisHistory([overLimit], "q")).toEqual([]);
    expect(selectAnalysisHistory([{ question: " h ", answer: " a " }], " q ")).toEqual([
      { question: "h", answer: "a" },
    ]);
    const joined = ["q", atLimit.question, atLimit.answer].join("\n");
    expect(new TextEncoder().encode(joined).byteLength).toBe(32 * 1024);
  });
});
