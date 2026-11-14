import { describe, expect, it, vi } from "vitest";
import {
  autonomyExtensions,
  encodeRunPacket,
  projectAutonomyExtension,
  parseAnalysisCancelAck,
  parseRunPacket,
  projectRunBody,
  ProtocolError,
  readRun,
  readRunFrames,
  RUN_STREAM_LIMITS,
  RUN_STREAM_PROTOCOL,
  RUN_STREAM_VERSION,
  type RunEvent,
  type RunPacket,
  type StreamExtension,
} from "../src/contracts/analysis-run";

const requestId = "98900000-0000-4000-8000-000000000001";
const runId = "98900000-0000-4000-8000-000000000002";
const stepId = "98900000-0000-4000-8000-000000000003";
const modelCallId = "98900000-0000-4000-8000-000000000004";
const encoder = new TextEncoder();
const signal = () => new AbortController().signal;
const options = () => ({ requestId, parse: parseRunPacket, signal: signal() });

const start: RunEvent = { type: "run.started", payload: {} };
const step: RunEvent = {
  type: "step.started",
  payload: { stepId, stepIndex: 1, modelCallId },
};
const stepDone: RunEvent = {
  type: "step.finished",
  payload: { stepId, status: "completed" },
};
const terminal: RunEvent = {
  type: "run.finished",
  payload: { status: "completed", finalAnswer: "完成。", file: { status: "none" } },
};
const toolKey = { stepId, toolCallId: "call_products", toolIndex: 1 };
const toolStart: RunEvent = {
  type: "tool.started",
  payload: {
    ...toolKey,
    toolKind: "retail_sales",
    params: {
      dimension: "product",
      channelCode: "POS",
      period: { start: "2025-11-17", end: "2025-11-24" },
      compareTo: null,
    },
  },
};
const toolDone: RunEvent = {
  type: "tool.finished",
  payload: { ...toolKey, status: "ok", rowCount: 1 },
};
const unknown: StreamExtension = {
  type: "stream.extension",
  payload: {
    name: "future.secret.payload",
    version: 1,
    required: false,
    data: { secret: "private-marker" },
  },
};

function frames(events: RunEvent[]) {
  return events.map((event, index) =>
    encodeRunPacket({
      protocol: RUN_STREAM_PROTOCOL,
      version: RUN_STREAM_VERSION,
      requestId,
      runId,
      seq: index + 1,
      event,
    }),
  );
}

function body(chunks: Uint8Array[]) {
  const iterator = chunks.values();
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      const { done, value } = iterator.next();
      if (done) controller.close();
      else controller.enqueue(value);
    },
  });
}

async function collect<T>(source: AsyncIterable<T>): Promise<T[]> {
  const values: T[] = [];
  for await (const value of source) values.push(value);
  return values;
}

describe("analysis Run stream protocol", () => {
  it("accepts two parallel queries finishing in reverse order and rejects a third active query", async () => {
    const second: RunEvent = {
      ...toolStart,
      payload: { ...toolStart.payload, toolCallId: "second", toolIndex: 2 },
    };
    const secondDone: RunEvent = {
      ...toolDone,
      payload: { ...toolDone.payload, toolCallId: "second", toolIndex: 2 },
    };
    const events = [start, step, toolStart, second, secondDone, toolDone, stepDone, terminal];
    expect(await collect(readRun(body(frames(events)), options()))).toHaveLength(events.length);
    const third: RunEvent = {
      ...toolStart,
      payload: { ...toolStart.payload, toolCallId: "third", toolIndex: 3 },
    };
    await expect(
      collect(readRun(body(frames([start, step, toolStart, second, third])), options())),
    ).rejects.toThrow("tool_start_order");
  });

  it("projects a database query without exposing its SQL or requiring Retail date parameters", async () => {
    const query: RunEvent = {
      type: "tool.started",
      payload: { ...toolKey, toolKind: "database_query", params: null },
    };
    const events = [start, step, query, toolDone, stepDone, terminal];
    const projected = await projectRunBody(body(frames(events)), { ...options(), batch: false });
    expect((await collect(readRun(projected, options()))).map(({ event }) => event)).toEqual(
      events,
    );
  });

  it("passes only validated autonomy actions and matches the result to the core terminal", async () => {
    const plan: StreamExtension = {
      type: "stream.extension",
      payload: {
        name: "compass.autonomy.action",
        version: 1,
        required: false,
        data: {
          stepId,
          actionIndex: 1,
          action: "replace",
          code: "ok",
          planVersion: 1,
          selectedTodoId: null,
          items: [
            {
              id: "t1",
              goal: "channel_summary",
              status: "pending",
              dependsOn: [],
              optional: false,
              period: { start: "2025-11-17", end: "2025-11-24" },
              compareTo: { start: "2025-11-24", end: "2025-12-01" },
            },
          ],
        },
      },
    };
    const usage = { limit: 1, used: 1, remaining: 0 };
    const result: StreamExtension = {
      type: "stream.extension",
      payload: {
        name: "compass.autonomy.result",
        version: 1,
        required: false,
        data: {
          outcome: "budget_exhausted",
          reason: "max_tool_calls",
          planVersion: 1,
          firstPlanStep: 1,
          budget: {
            steps: usage,
            tools: usage,
            tokens: usage,
            costMicrousd: usage,
            attempts: 1,
            unknownAttempts: 0,
          },
          partial: [],
        },
      },
    };
    const failed: RunEvent = {
      type: "run.finished",
      payload: { status: "failed", errorCode: "internal_error" },
    };
    const m2Options = { ...options(), extensions: autonomyExtensions };
    const projected = await projectRunBody(
      body(frames([start, step, unknown, plan, stepDone, result, failed])),
      {
        ...m2Options,
        batch: false,
        publicExtensions: autonomyExtensions,
        translateExtension: projectAutonomyExtension,
      },
    );
    expect((await collect(readRun(projected, m2Options))).map(({ event }) => event)).toEqual([
      start,
      step,
      plan,
      stepDone,
      result,
      failed,
    ]);
    await expect(
      collect(readRun(body(frames([start, step, plan, stepDone, result, terminal])), m2Options)),
    ).rejects.toThrow("autonomy_terminal_mismatch");
    const invalid = {
      ...plan,
      payload: { ...plan.payload, data: { ...plan.payload.data, raw: "private" } },
    };
    await expect(
      collect(readRun(body(frames([start, step, invalid, stepDone, failed])), m2Options)),
    ).rejects.toThrow("extension_shape");
  });

  it("decodes arbitrary UTF-8 cuts, CRLF packets, and keepalive frames", async () => {
    const wire = frames([start, step, stepDone, terminal])
      .map((value) => new TextDecoder().decode(value))
      .join("")
      .replaceAll("\n", "\r\n");
    const bytes = encoder.encode(`: keepalive\r\n\r\n${wire}`);
    const output = await projectRunBody(body(Array.from(bytes, (byte) => Uint8Array.of(byte))), {
      ...options(),
      batch: true,
    });
    const values = await collect(readRun(output, options()));
    expect(values.map(({ event }) => event.type)).toEqual([
      "run.started",
      "step.started",
      "step.finished",
      "run.finished",
    ]);
    expect(values.at(-1)?.runId).toBe(runId);
  });

  it("validates batches atomically, filters unknown extensions, and re-sequences", async () => {
    const timing = (ms: number): StreamExtension => ({
      type: "stream.extension",
      payload: {
        name: "compass.internal.timing",
        version: 1,
        required: false,
        data: { ms, worker: "private-marker" },
      },
    });
    const upstream = frames([
      start,
      { type: "stream.batch", payload: [step, timing(5), unknown, timing(7), stepDone] },
      terminal,
    ]);
    const publicExtensions = new Map([
      ["compass.progress.timing@1", (data: Record<string, unknown>) => data.elapsedMs === 12],
    ]);
    const output = await projectRunBody(body(upstream), {
      ...options(),
      batch: true,
      extensions: new Map([["compass.internal.timing@1", (data) => typeof data.ms === "number"]]),
      publicExtensions,
      translateExtension(event, window) {
        const timings = window.filter((item) => item.type === "stream.extension");
        if (event !== timings.at(-1)) return undefined;
        return {
          type: "stream.extension",
          payload: {
            name: "compass.progress.timing",
            version: 1,
            required: false,
            data: {
              elapsedMs: timings.reduce((sum, item) => sum + Number(item.payload.data.ms), 0),
            },
          },
        };
      },
    });
    const [wire, client] = output.tee();
    const packets = await collect(readRunFrames(wire, signal()));
    expect(packets.map(({ data }) => parseRunPacket(data).seq)).toEqual([1, 2, 3]);
    expect(packets.map(({ data }) => parseRunPacket(data).event.type)).toEqual([
      "run.started",
      "stream.batch",
      "run.finished",
    ]);
    expect(JSON.stringify(packets)).not.toContain("private-marker");
    const values = await collect(readRun(client, { ...options(), extensions: publicExtensions }));
    expect(values.map(({ event }) => event.type)).toEqual([
      "run.started",
      "step.started",
      "stream.extension",
      "step.finished",
      "run.finished",
    ]);
  });

  it("requires clean EOF and never publishes a validated terminal early", async () => {
    const valid = frames([start, step, stepDone, terminal]);
    for (const tail of [encoder.encode("incomplete"), frames([start])[0]]) {
      const seen: RunEvent[] = [];
      await expect(async () => {
        for await (const { event } of readRun(body([...valid, tail]), options())) seen.push(event);
      }).rejects.toBeInstanceOf(ProtocolError);
      expect(seen.some((event) => event.type === "run.finished")).toBe(false);
    }
  });

  it("rejects bad identities, required extensions, nested batches, and incomplete tools", async () => {
    const base = {
      protocol: RUN_STREAM_PROTOCOL,
      version: RUN_STREAM_VERSION,
      requestId,
      runId,
      seq: 2,
    };
    const variants: unknown[] = [
      { ...base, version: 2, event: step },
      { ...base, seq: 3, event: step },
      { ...base, requestId: "98900000-0000-4000-8000-000000000099", event: step },
      { ...base, event: { type: "future.core", payload: {} } },
      { ...base, event: { ...unknown, payload: { ...unknown.payload, required: true } } },
      {
        ...base,
        event: { type: "stream.batch", payload: [{ type: "stream.batch", payload: [] }] },
      },
      { ...base, event: { type: "stream.batch", payload: [step, toolDone] } },
    ];
    for (const packet of variants) {
      const wire = encoder.encode(`data: ${JSON.stringify(packet)}\n\n`);
      const seen: RunEvent[] = [];
      await expect(async () => {
        for await (const { event } of readRun(body([frames([start])[0], wire]), options()))
          seen.push(event);
      }).rejects.toBeInstanceOf(ProtocolError);
      expect(seen).toEqual([start]);
    }
  });

  it("accepts only canonical files from a successful product call", async () => {
    const file = {
      status: "ready" as const,
      stepId,
      toolCallId: toolKey.toolCallId,
      filename: `retail-products-${runId}.csv`,
      mediaType: "text/csv; charset=utf-8" as const,
      rowCount: 1,
      byteLength: 4,
      contentBase64: btoa("csv\n"),
    };
    const input = (fileValue: typeof file) =>
      body(
        frames([
          start,
          step,
          toolStart,
          toolDone,
          stepDone,
          {
            type: "run.finished",
            payload: { status: "completed", finalAnswer: "完成。", file: fileValue },
          },
        ]),
      );
    expect((await collect(readRun(input(file), options()))).at(-1)?.event.type).toBe(
      "run.finished",
    );
    await expect(
      collect(readRun(input({ ...file, toolCallId: "other" }), options())),
    ).rejects.toThrow("file_source");
    await expect(
      collect(readRun(input({ ...file, contentBase64: "Y3N2Cg" }), options())),
    ).rejects.toThrow("file_base64");
  });

  it("enforces frame, stream, extension, and logical event budgets", async () => {
    const windows: RunEvent[] = [];
    for (let index = 0; index < RUN_STREAM_LIMITS.events / RUN_STREAM_LIMITS.batchItems; index += 1)
      windows.push({
        type: "stream.batch",
        payload: Array.from({ length: RUN_STREAM_LIMITS.batchItems }, () => unknown),
      });
    await expect(collect(readRun(body(frames([start, ...windows])), options()))).rejects.toThrow(
      "event_count",
    );
    const oversized: StreamExtension = {
      ...unknown,
      payload: {
        ...unknown.payload,
        data: { large: "x".repeat(RUN_STREAM_LIMITS.extensionBytes) },
      },
    };
    await expect(collect(readRun(body(frames([start, oversized])), options()))).rejects.toThrow(
      "extension_bytes",
    );
    await expect(
      collect(
        readRunFrames(body([Uint8Array.from([100, 97, 116, 97, 58, 32, 255, 10, 10])]), signal()),
      ),
    ).rejects.toThrow("invalid_utf8");
    await expect(
      collect(readRunFrames(body([new Uint8Array(RUN_STREAM_LIMITS.frameBytes + 1)]), signal())),
    ).rejects.toThrow("frame_bytes");
  });

  it("releases the upstream reader when preflight ownership is aborted", async () => {
    let canceled = false;
    const source = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(frames([start])[0]);
      },
      cancel() {
        canceled = true;
      },
    });
    const owner = new AbortController();
    await projectRunBody(source, { ...options(), signal: owner.signal, batch: true });
    owner.abort();
    await vi.waitFor(() => {
      expect(canceled).toBe(true);
      expect(source.locked).toBe(false);
    });
  });

  it("rejects extra fields in packet and cancellation contracts", () => {
    const packet: RunPacket = parseRunPacket(
      JSON.parse(new TextDecoder().decode(frames([start])[0]).slice(6)),
    );
    expect(() => parseRunPacket({ ...packet, privateValue: "secret" })).toThrow("packet_schema");
    const ack = parseAnalysisCancelAck({ runId, status: "accepted" });
    expect(ack).toEqual({ runId, status: "accepted" });
    expect(() => parseAnalysisCancelAck({ ...ack, privateValue: "secret" })).toThrow(
      "cancel_ack_schema",
    );
  });
});
