import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import type { components } from "../generated/api";
import runStreamSchema from "../generated/run-stream.schema.json";
import {
  RUN_STREAM_LIMITS,
  RUN_STREAM_PROTOCOL,
  RUN_STREAM_VERSION,
} from "../generated/run-stream-limits";

export { RUN_STREAM_LIMITS, RUN_STREAM_PROTOCOL, RUN_STREAM_VERSION };

type Schemas = components["schemas"];
export type RunPacket = Schemas["RunPacket"];
export type RunEvent = RunPacket["event"];
export type RunFinished = Schemas["RunFinished"];
export type RunStarted = Schemas["RunStarted"];
export type RunProgressEvent = Schemas["StreamBatch"]["payload"][number];
export type StreamExtension = Schemas["StreamExtension"];
export type AnalysisCancelAck = Schemas["AnalysisCancelAck"];
export type ExtensionData = StreamExtension["payload"]["data"];
export type ExtensionSchemas = ReadonlyMap<string, (data: ExtensionData) => boolean>;
export type ParseRunPacket = (value: unknown) => RunPacket;

const ajv = new Ajv2020({ allErrors: false, strict: false });
addFormats(ajv);
ajv.addSchema(runStreamSchema);
const validator = <T>(ref: string): ValidateFunction<T> => ajv.compile<T>({ $ref: ref });
const runPacketValidator = validator<RunPacket>(runStreamSchema.$id);
const cancelAckValidator = validator<AnalysisCancelAck>(
  `${runStreamSchema.$id}#/$defs/AnalysisCancelAck`,
);

const encoder = new TextEncoder();
const heartbeat = encoder.encode(": keepalive\n\n");
const byteLength = (value: unknown) => encoder.encode(JSON.stringify(value)).byteLength;
const extensionKey = ({ payload }: StreamExtension) => `${payload.name}@${payload.version}`;

export class ProtocolError extends Error {
  constructor(code: string) {
    super(code);
    this.name = "ProtocolError";
  }
}

function check(condition: unknown, code: string): asserts condition {
  if (!condition) throw new ProtocolError(code);
}

function checkJsonDepth(root: unknown): void {
  const pending: [unknown, number][] = [[root, 0]];
  while (pending.length) {
    const [node, depth] = pending.pop()!;
    if (node === null || typeof node !== "object") continue;
    check(depth < RUN_STREAM_LIMITS.jsonDepth, "json_depth");
    for (const value of Object.values(node)) pending.push([value, depth + 1]);
  }
}

export function parseRunPacket(value: unknown): RunPacket {
  if (!runPacketValidator(value)) throw new ProtocolError("packet_schema");
  return value;
}

export function parseAnalysisCancelAck(value: unknown): AnalysisCancelAck {
  if (!cancelAckValidator(value)) throw new ProtocolError("cancel_ack_schema");
  return value;
}

export function encodeRunPacket(packet: RunPacket): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(packet)}\n\n`);
}

export async function* readRunFrames(body: ReadableStream<Uint8Array>, signal: AbortSignal) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  const frame = new Uint8Array(RUN_STREAM_LIMITS.frameBytes);
  let length = 0;
  let lineStart = 0;
  let total = 0;
  const abort = () => void reader.cancel(signal.reason).catch(() => undefined);
  signal.addEventListener("abort", abort, { once: true });
  try {
    for (;;) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      total += value.byteLength;
      check(total <= RUN_STREAM_LIMITS.streamBytes, "stream_bytes");
      for (const byte of value) {
        check(length < RUN_STREAM_LIMITS.frameBytes, "frame_bytes");
        frame[length++] = byte;
        if (byte !== 10) continue;
        const emptyLine =
          length === lineStart + 1 || (length === lineStart + 2 && frame[lineStart] === 13);
        if (!emptyLine) {
          lineStart = length;
          continue;
        }
        let wire: string;
        try {
          wire = decoder.decode(frame.subarray(0, length));
        } catch {
          throw new ProtocolError("invalid_utf8");
        }
        const match = /^(?:data: ([^\r\n]+)|: keepalive)\r?\n\r?\n$/.exec(wire);
        check(match && match[0].length === wire.length, "frame_shape");
        if (match[1] !== undefined) {
          let data: unknown;
          try {
            data = JSON.parse(match[1]);
          } catch {
            throw new ProtocolError("invalid_json");
          }
          checkJsonDepth(data);
          yield { data, bytes: length };
        }
        length = 0;
        lineStart = 0;
      }
    }
    check(length === 0, "partial_eof");
  } finally {
    signal.removeEventListener("abort", abort);
    void reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

type ToolStart = Extract<RunProgressEvent, { type: "tool.started" }>["payload"];
type ToolFinished = Extract<RunProgressEvent, { type: "tool.finished" }>["payload"];
type StepStatus = Extract<RunProgressEvent, { type: "step.finished" }>["payload"]["status"];
type ToolSlot = { start: ToolStart; done?: ToolFinished };
type StepSlot = { tools: Map<string, ToolSlot> };

function checkFile(
  file: Extract<RunFinished["payload"], { status: "completed" }>["file"] & {
    status: "ready";
  },
  runId: string,
  source: ToolSlot | undefined,
) {
  check(
    source?.start.toolKind === "retail_sales" &&
      source.start.params?.dimension === "product" &&
      source.done?.status === "ok" &&
      source.done.rowCount === file.rowCount &&
      file.filename === `retail-products-${runId}.csv`,
    "file_source",
  );
  let content: string;
  try {
    content = atob(file.contentBase64);
  } catch {
    throw new ProtocolError("file_base64");
  }
  check(
    content.length === file.byteLength &&
      content.length <= RUN_STREAM_LIMITS.fileBytes &&
      btoa(content) === file.contentBase64,
    "file_base64",
  );
}

/** Ordered state validation shared by the BFF and the browser. */
export class RunSequence {
  runId: string | undefined;
  private seq = 0;
  private eventCount = 0;
  private routed = false;
  private activeStep: string | undefined;
  private lastStepStatus: StepStatus | undefined;
  private readonly steps = new Map<string, StepSlot>();
  private readonly modelCalls = new Set<string>();
  private terminal: RunFinished | undefined;
  private readonly requestId: string;
  private readonly extensions: ExtensionSchemas | undefined;

  constructor(options: { requestId: string; extensions?: ExtensionSchemas }) {
    this.requestId = options.requestId;
    this.extensions = options.extensions;
  }

  accept(packet: RunPacket, frameBytes: number): (RunProgressEvent | RunStarted)[] {
    check(
      !this.terminal &&
        packet.protocol === RUN_STREAM_PROTOCOL &&
        packet.version === RUN_STREAM_VERSION &&
        packet.requestId === this.requestId &&
        packet.seq === this.seq + 1 &&
        (this.runId === undefined || this.runId === packet.runId),
      "packet_identity_or_order",
    );
    const event = packet.event;
    check((this.seq === 0) === (event.type === "run.started"), "start_order");
    this.seq = packet.seq;
    this.runId = packet.runId;
    const items = event.type === "stream.batch" ? event.payload : [event];
    if (event.type === "stream.batch")
      check(frameBytes <= RUN_STREAM_LIMITS.batchBytes, "batch_bytes");
    this.eventCount += items.length;
    check(this.eventCount <= RUN_STREAM_LIMITS.events, "event_count");

    const visible: (RunProgressEvent | RunStarted)[] = [];
    for (const item of items) {
      switch (item.type) {
        case "run.started":
          break;
        case "run.routed":
          check(!this.routed && this.steps.size === 0, "route_order");
          this.routed = true;
          break;
        case "step.started": {
          const { stepId, stepIndex, modelCallId } = item.payload;
          check(
            !this.activeStep &&
              (!this.lastStepStatus || this.lastStepStatus === "completed") &&
              !this.steps.has(stepId) &&
              !this.modelCalls.has(modelCallId) &&
              stepIndex === this.steps.size + 1,
            "step_order",
          );
          this.activeStep = stepId;
          this.modelCalls.add(modelCallId);
          this.steps.set(stepId, { tools: new Map() });
          break;
        }
        case "tool.started": {
          const info = item.payload;
          const step = this.openStep(info.stepId);
          check(
            !step.tools.has(info.toolCallId) &&
              info.toolIndex === step.tools.size + 1 &&
              [...step.tools.values()].every((tool) => tool.done) &&
              (info.toolKind === "retail_sales" || info.params === null),
            "tool_start_order",
          );
          step.tools.set(info.toolCallId, { start: info });
          break;
        }
        case "tool.finished": {
          const info = item.payload;
          const tool = this.openStep(info.stepId).tools.get(info.toolCallId);
          check(
            tool &&
              !tool.done &&
              tool.start.toolIndex === info.toolIndex &&
              (info.status === "error" || tool.start.params !== null),
            "tool_finish_order",
          );
          tool.done = info;
          break;
        }
        case "step.finished": {
          const step = this.openStep(item.payload.stepId);
          check(
            item.payload.status !== "completed" ||
              [...step.tools.values()].every((tool) => tool.done),
            "step_has_open_tool",
          );
          this.activeStep = undefined;
          this.lastStepStatus = item.payload.status;
          break;
        }
        case "stream.extension": {
          const info = item.payload;
          check(!info.required, "required_extension");
          check(byteLength(info.data) <= RUN_STREAM_LIMITS.extensionBytes, "extension_bytes");
          const validator = this.extensions?.get(extensionKey(item));
          if (!validator) continue;
          check(validator(info.data), "extension_shape");
          break;
        }
        case "run.finished":
          this.checkTerminal(packet.runId, item);
          this.terminal = item;
          continue;
      }
      visible.push(item);
    }
    return visible;
  }

  eof(): RunFinished {
    check(this.terminal, "missing_terminal");
    return this.terminal;
  }

  private openStep(stepId: string): StepSlot {
    const step = this.steps.get(stepId);
    check(step && stepId === this.activeStep, "unknown_or_closed_step");
    return step;
  }

  private checkTerminal(runId: string, event: RunFinished): void {
    check(!this.activeStep, "terminal_with_open_step");
    const { payload } = event;
    if (payload.status !== "completed") return;
    check(this.lastStepStatus === "completed", "success_without_completed_step");
    check(
      payload.finalAnswer.trim() &&
        byteLength(payload.finalAnswer) <= RUN_STREAM_LIMITS.answerBytes,
      "final_answer",
    );
    if (payload.file.status === "ready") {
      const file = payload.file;
      checkFile(file, runId, this.steps.get(file.stepId)?.tools.get(file.toolCallId));
    }
  }
}

export type ReadRunOptions = {
  requestId: string;
  parse?: ParseRunPacket;
  signal: AbortSignal;
  extensions?: ExtensionSchemas;
};

export async function* readRun(body: ReadableStream<Uint8Array>, options: ReadRunOptions) {
  const sequence = new RunSequence(options);
  const parse = options.parse ?? parseRunPacket;
  for await (const { data, bytes } of readRunFrames(body, options.signal)) {
    const packet = parse(data);
    for (const event of sequence.accept(packet, bytes)) yield { runId: packet.runId, event };
  }
  options.signal.throwIfAborted();
  const event = sequence.eof();
  yield { runId: sequence.runId!, event };
}

export type ProjectRunOptions = ReadRunOptions & {
  batch: boolean;
  publicExtensions?: ExtensionSchemas;
  translateExtension?: (
    event: StreamExtension,
    window: readonly RunProgressEvent[],
  ) => StreamExtension | undefined;
};

async function untilFlush<T>(read: Promise<T>, due: number): Promise<T | undefined> {
  if (!Number.isFinite(due)) return await read;
  const waitMs = due - performance.now();
  if (waitMs <= 0) return undefined;
  let cancelTimer: (() => void) | undefined;
  try {
    return await Promise.race([
      read,
      new Promise<undefined>((resolve) => {
        const timer = setTimeout(resolve, waitMs);
        cancelTimer = () => clearTimeout(timer);
      }),
    ]);
  } finally {
    cancelTimer?.();
  }
}

/** Projects a validated Agent stream to a separately sequenced public stream. */
export async function* projectRun(
  body: ReadableStream<Uint8Array>,
  options: ProjectRunOptions,
): AsyncGenerator<Uint8Array> {
  const local = new AbortController();
  const signal = AbortSignal.any([options.signal, local.signal]);
  const source = readRun(body, { ...options, signal });
  const projected = new RunSequence({
    requestId: options.requestId,
    extensions: options.publicExtensions,
  });
  let runId = "";
  let seq = 0;
  let sentBytes = 0;
  let window: RunProgressEvent[] = [];
  let due = Infinity;
  let windowBytes = 0;
  let heartbeatAt = Infinity;
  const parse = options.parse ?? parseRunPacket;

  const packetFor = (event: RunEvent): RunPacket => ({
    protocol: RUN_STREAM_PROTOCOL,
    version: RUN_STREAM_VERSION,
    requestId: options.requestId,
    runId,
    seq: seq + 1,
    event,
  });

  function charge(frame: Uint8Array) {
    signal.throwIfAborted();
    check(sentBytes + frame.byteLength <= RUN_STREAM_LIMITS.streamBytes, "stream_bytes");
    sentBytes += frame.byteLength;
    heartbeatAt = performance.now() + RUN_STREAM_LIMITS.heartbeatMs;
    return frame;
  }

  function encode(event: RunEvent) {
    signal.throwIfAborted();
    const candidate = packetFor(event);
    checkJsonDepth(candidate);
    const packet = parse(candidate);
    const frame = encodeRunPacket(packet);
    check(frame.byteLength <= RUN_STREAM_LIMITS.frameBytes, "frame_bytes");
    projected.accept(packet, frame.byteLength);
    seq += 1;
    return charge(frame);
  }

  function* flush() {
    const previous = window;
    window = [];
    due = Infinity;
    windowBytes = 0;
    let group: RunProgressEvent[] = [];
    for (const item of previous) {
      let mapped: RunProgressEvent | undefined = item;
      if (item.type === "stream.extension") {
        const snapshot = structuredClone(previous) as RunProgressEvent[];
        const index = previous.indexOf(item);
        mapped = options.translateExtension?.(snapshot[index] as StreamExtension, snapshot);
        if (mapped) {
          const validate = options.publicExtensions?.get(extensionKey(mapped));
          check(
            validate && !mapped.payload.required && validate(mapped.payload.data),
            "unsafe_projection",
          );
        }
      }
      if (!mapped) continue;
      if (!options.batch) {
        yield encode(mapped);
        continue;
      }
      const combined = [...group, mapped];
      if (
        group.length &&
        encodeRunPacket(packetFor({ type: "stream.batch", payload: combined })).byteLength >
          RUN_STREAM_LIMITS.batchBytes
      ) {
        yield encode(group.length === 1 ? group[0] : { type: "stream.batch", payload: group });
        group = [];
      }
      group.push(mapped);
    }
    if (group.length)
      yield encode(group.length === 1 ? group[0] : { type: "stream.batch", payload: group });
  }

  const next = () =>
    source.next().then(
      (value) => ({ value }),
      (error: unknown) => ({ error }),
    );
  let pending = next();
  try {
    for (;;) {
      const answer = await untilFlush(pending, Math.min(due, heartbeatAt));
      if (answer === undefined) {
        if (due <= heartbeatAt) yield* flush();
        if (performance.now() >= heartbeatAt) yield charge(heartbeat);
        continue;
      }
      if ("error" in answer) throw answer.error;
      const { done, value } = answer.value;
      check(!done, "missing_terminal");
      const { event } = value;
      runId = value.runId;
      if (event.type === "run.started") {
        yield encode(event);
      } else if (event.type === "run.finished") {
        yield* flush();
        yield encode(event);
        return;
      } else {
        const itemBytes = byteLength(event) + 1;
        if (window.length && windowBytes + itemBytes > RUN_STREAM_LIMITS.batchBytes) yield* flush();
        if (!window.length) due = performance.now() + RUN_STREAM_LIMITS.batchWaitMs;
        window.push(event);
        windowBytes += itemBytes;
        if (!options.batch || window.length >= RUN_STREAM_LIMITS.batchItems) yield* flush();
      }
      pending = next();
    }
  } finally {
    local.abort();
    await source.return(undefined);
  }
}

/** Pull adapter: performs first-frame preflight before returning the public stream. */
export async function projectRunBody(body: ReadableStream<Uint8Array>, options: ProjectRunOptions) {
  const local = new AbortController();
  const output = projectRun(body, {
    ...options,
    signal: AbortSignal.any([options.signal, local.signal]),
  });
  const first = await output.next();
  check(!first.done, "missing_start");
  let initial: Uint8Array | undefined = first.value;
  const stop = async (reason: unknown) => {
    dispose();
    local.abort(reason);
    await output.return(undefined);
  };
  const abort = () => void stop(options.signal.reason).catch(() => undefined);
  const dispose = () => options.signal.removeEventListener("abort", abort);
  options.signal.addEventListener("abort", abort, { once: true });
  if (options.signal.aborted) abort();

  return new ReadableStream<Uint8Array>(
    {
      async pull(controller) {
        try {
          options.signal.throwIfAborted();
          if (initial) {
            controller.enqueue(initial);
            initial = undefined;
            return;
          }
          const { done, value } = await output.next();
          if (done) {
            dispose();
            controller.close();
          } else {
            controller.enqueue(value);
          }
        } catch (error) {
          controller.error(error);
          await stop(error);
        }
      },
      cancel: stop,
    },
    { highWaterMark: 0 },
  );
}
