import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import Ajv2020 from "ajv/dist/2020.js";
import schema from "./schema.json" with { type: "json" };
import { LIMITS, PROTOCOL, VERSION } from "./limits.ts";
import {
  encodePacket,
  projectBody,
  ProtocolError,
  readFrames,
  readRun,
  type Event,
  type Extension,
  type Packet,
} from "./stream.ts";

const validate = new Ajv2020({ strict: false }).compile<Packet>(schema);
function parse(value: unknown): Packet {
  if (!validate(value)) throw new ProtocolError("packet_schema");
  return value;
}

const requestId = "00000000-0000-4000-8000-000000000001";
const runId = "00000000-0000-4000-8000-000000000002";
const stepId = "00000000-0000-4000-8000-000000000003";
const modelCallId = "00000000-0000-4000-8000-000000000004";
const encoder = new TextEncoder();
const signal = () => new AbortController().signal;
const request = () => ({ requestId, parse, signal: signal() });
const frames = (events: Event[]) =>
  events.map((event, index) =>
    encodePacket({ protocol: PROTOCOL, version: VERSION, requestId, runId, seq: index + 1, event }),
  );

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

const start: Event = { type: "run.started", payload: {} };
const step: Event = { type: "step.started", payload: { stepId, stepIndex: 1, modelCallId } };
const stepDone: Event = { type: "step.finished", payload: { stepId, status: "completed" } };
const terminal: Event = {
  type: "run.finished",
  payload: { status: "completed", finalAnswer: "完成。", file: { status: "none" } },
};
const unknown: Event = {
  type: "stream.extension",
  payload: {
    name: "future.secret.payload",
    version: 1,
    required: false,
    data: { secret: "private-marker" },
  },
};
const toolKey = { stepId, toolCallId: "call_products", toolIndex: 1 };
const toolStart: Event = {
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
const toolDone: Event = {
  type: "tool.finished",
  payload: { ...toolKey, status: "ok", rowCount: 1 },
};

test("Python writer and generated contract decode across arbitrary UTF-8 / CRLF cuts", async () => {
  const sample = readFileSync(new URL("./sample.sse", import.meta.url), "utf8").replaceAll(
    "\n",
    "\r\n",
  );
  const bytes = encoder.encode(": keepalive\r\n\r\n" + sample);
  const split = Array.from(bytes, (byte) => Uint8Array.of(byte));
  const output = await projectBody(body(split), { ...request(), batch: true });
  const events = await collect(readRun(output, request()));
  assert.deepEqual(
    events.map(({ event }) => event.type),
    ["run.started", "step.started", "step.finished", "run.finished"],
  );
  assert.equal(events.at(-1)?.runId, runId);
});

test("BFF reads a batch, aggregates registered extensions, drops unknown ones and re-sequences", async () => {
  const timing = (ms: number): Extension => ({
    type: "stream.extension",
    payload: {
      name: "compass.internal.timing",
      version: 1,
      required: false,
      data: { ms, worker: "private-marker" },
    },
  });
  const src = frames([
    start,
    { type: "stream.batch", payload: [step, timing(5), unknown, timing(7), stepDone] },
    terminal,
  ]);
  const publicExtensions = new Map([
    ["compass.progress.timing@1", (data: unknown) => JSON.stringify(data) === '{"elapsedMs":12}'],
  ]);
  const output = await projectBody(body(src), {
    ...request(),
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
          data: { elapsedMs: timings.reduce((sum, item) => sum + Number(item.payload.data.ms), 0) },
        },
      };
    },
  });
  const [wire, client] = output.tee();
  const packets = await collect(readFrames(wire, signal()));
  assert.deepEqual(
    packets.map(({ data }) => parse(data).seq),
    [1, 2, 3],
  );
  assert.deepEqual(
    packets.map(({ data }) => parse(data).event.type),
    ["run.started", "stream.batch", "run.finished"],
  );
  assert(!JSON.stringify(packets).includes("private-marker"));
  const events = await collect(readRun(client, { ...request(), extensions: publicExtensions }));
  assert.deepEqual(
    events.map(({ event }) => event.type),
    ["run.started", "step.started", "stream.extension", "step.finished", "run.finished"],
  );
});

test("BFF direct delivery preserves order and never forwards an unknown optional payload", async () => {
  const output = await projectBody(body(frames([start, step, unknown, stepDone, terminal])), {
    ...request(),
    batch: false,
  });
  const packets = await collect(readFrames(output, signal()));
  assert.deepEqual(
    packets.map(({ data }) => parse(data).seq),
    [1, 2, 3, 4],
  );
  assert.deepEqual(
    packets.map(({ data }) => parse(data).event.type),
    ["run.started", "step.started", "step.finished", "run.finished"],
  );
  assert(!JSON.stringify(packets).includes("private-marker"));
});

test("clean EOF is required even after a valid terminal frame", async () => {
  const valid = frames([start, step, stepDone, terminal]);
  for (const tail of [encoder.encode("incomplete"), frames([start])[0]]) {
    const seen: Event[] = [];
    await assert.rejects(async () => {
      for await (const { event } of readRun(body([...valid, tail]), request())) seen.push(event);
    }, ProtocolError);
    assert(!seen.some((event) => event.type === "run.finished"));
  }
  const output = await projectBody(body([...valid, encoder.encode("incomplete")]), {
    ...request(),
    batch: true,
  });
  const seen: Event[] = [];
  await assert.rejects(async () => {
    for await (const { data } of readFrames(output, signal())) seen.push(parse(data).event);
  }, ProtocolError);
  assert(!seen.some((event) => event.type === "run.finished"));
});

test("unknown required, unknown core, bad version, nested and incomplete batches are rejected", async () => {
  const base = { protocol: PROTOCOL, version: VERSION, requestId, runId, seq: 2 };
  const variants = [
    { ...base, version: 2, event: step },
    { ...base, seq: 3, event: step },
    { ...base, event: { type: "future.core", payload: {} } },
    { ...base, event: { ...unknown, payload: { ...unknown.payload, required: true } } },
    {
      ...base,
      event: { type: "stream.batch", payload: [{ type: "stream.batch", payload: [step] }] },
    },
    { ...base, event: { type: "stream.batch", payload: [step, toolDone] } },
  ];
  for (const packet of variants) {
    const wire = encoder.encode("data: " + JSON.stringify(packet) + "\n\n");
    const seen: Event[] = [];
    await assert.rejects(async () => {
      for await (const { event } of readRun(body([frames([start])[0], wire]), request()))
        seen.push(event);
    }, ProtocolError);
    assert.deepEqual(seen, [start]);
  }
  await assert.rejects(
    projectBody(body([frames([step])[0]]), { ...request(), batch: true }),
    ProtocolError,
  );
});

test("file must reference the successful product call and carry canonical Base64", async () => {
  const file = {
    status: "ready" as const,
    stepId,
    toolCallId: toolKey.toolCallId,
    filename: "retail-products-" + runId + ".csv",
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
  assert.equal((await collect(readRun(input(file), request()))).at(-1)?.event.type, "run.finished");
  await assert.rejects(
    collect(readRun(input({ ...file, toolCallId: "other" }), request())),
    /file_source/,
  );
  await assert.rejects(
    collect(readRun(input({ ...file, contentBase64: "Y3N2Cg" }), request())),
    /file_base64/,
  );
});

test("BFF time limit releases buffered progress during an upstream pause and cancels the reader", async () => {
  let canceled = false;
  const firstTwo = frames([start, step]);
  const paused = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of firstTwo) controller.enqueue(frame);
    },
    cancel() {
      canceled = true;
    },
  });
  const output = await projectBody(paused, { ...request(), batch: true });
  const reader = output.getReader();
  assert.equal(new TextDecoder().decode((await reader.read()).value).includes("run.started"), true);
  const started = performance.now();
  const released = await reader.read();
  assert(!released.done && new TextDecoder().decode(released.value).includes("step.started"));
  assert(performance.now() - started >= LIMITS.batchWaitMs - 10);
  await reader.cancel();
  assert(canceled);
});

test("logical event, packet and extension limits also apply to filtered progress", async () => {
  const windows: Event[] = [];
  for (let index = 0; index < LIMITS.events / LIMITS.batchItems; index += 1) {
    windows.push({
      type: "stream.batch",
      payload: Array.from({ length: LIMITS.batchItems }, () => unknown),
    });
  }
  await assert.rejects(
    collect(readRun(body(frames([start, ...windows])), request())),
    /event_count/,
  );
  const oversized: Extension = {
    ...unknown,
    payload: { ...unknown.payload, data: { large: "x".repeat(LIMITS.extensionBytes) } },
  };
  await assert.rejects(
    collect(readRun(body(frames([start, oversized])), request())),
    /extension_bytes/,
  );
  const invalidUtf8 = Uint8Array.from([100, 97, 116, 97, 58, 32, 255, 10, 10]);
  await assert.rejects(collect(readFrames(body([invalidUtf8]), signal())), /invalid_utf8/);
  await assert.rejects(
    collect(readFrames(body([new Uint8Array(LIMITS.frameBytes + 1)]), signal())),
    /frame_bytes/,
  );
});

test("aborting after BFF preflight without pulling releases the upstream reader", async () => {
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
  await projectBody(source, { ...request(), signal: owner.signal, batch: true });
  owner.abort();
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert(canceled);
  assert(!source.locked);
});
