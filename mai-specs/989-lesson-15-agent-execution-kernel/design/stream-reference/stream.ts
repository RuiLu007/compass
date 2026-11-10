import type { components } from "./api.ts";
import { LIMITS, PROTOCOL, VERSION } from "./limits.ts";

type Schemas = components["schemas"];
export type Packet = Schemas["RunPacket"];
export type Event = Packet["event"];
export type Terminal = Schemas["RunFinished"];
export type Progress = Exclude<Event, Schemas["RunStarted"] | Terminal | Schemas["StreamBatch"]>;
export type Extension = Schemas["StreamExtension"];
export type ParsePacket = (value: unknown) => Packet;
type ExtensionData = Extension["payload"]["data"];
export type ExtensionSchemas = ReadonlyMap<string, (data: ExtensionData) => boolean>;

const encoder = new TextEncoder();
const heartbeat = encoder.encode(": keepalive\n\n");
const byteLength = (value: unknown) => encoder.encode(JSON.stringify(value)).byteLength;
const extensionKey = ({ payload }: Extension) =>
  String(payload.name) + "@" + String(payload.version);

export class ProtocolError extends Error {}

function check(condition: unknown, code: string): asserts condition {
  if (!condition) throw new ProtocolError(code);
}

function checkJsonDepth(root: unknown): void {
  const pending: [unknown, number][] = [[root, 0]];
  while (pending.length) {
    const [node, depth] = pending.pop()!;
    if (node === null || typeof node !== "object") continue;
    check(depth < LIMITS.jsonDepth, "json_depth");
    for (const value of Object.values(node)) pending.push([value, depth + 1]);
  }
}

export function encodePacket(packet: Packet): Uint8Array {
  return encoder.encode("data: " + JSON.stringify(packet) + "\n\n");
}

export async function* readFrames(body: ReadableStream<Uint8Array>, signal: AbortSignal) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  const frame = new Uint8Array(LIMITS.frameBytes);
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
      check(total <= LIMITS.streamBytes, "stream_bytes");
      for (const byte of value) {
        check(length < LIMITS.frameBytes, "frame_bytes");
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
        length = lineStart = 0;
      }
    }
    check(length === 0, "partial_eof");
  } finally {
    signal.removeEventListener("abort", abort);
    // Upstream fetch is aborted separately by the BFF if downstream stops reading.
    void reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

type ToolSlot = { start: Schemas["ToolStart"]; done?: Schemas["ToolFinished"]["payload"] };
type StepSlot = { tools: Map<string, ToolSlot> };

function checkFile(file: Schemas["FileReady"], runId: string, source: ToolSlot | undefined) {
  check(
    source?.start.toolKind === "retail_sales" &&
      source.start.params?.dimension === "product" &&
      source.done?.status === "ok" &&
      source.done.rowCount === file.rowCount &&
      file.filename === "retail-products-" + runId + ".csv",
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
      content.length <= LIMITS.fileBytes &&
      btoa(content) === file.contentBase64,
    "file_base64",
  );
}

/** Both Web consumers use the same ordered reducer, independent of packet grouping. */
export class RunSequence {
  runId: string | undefined;
  private seq = 0;
  private eventCount = 0;
  private routed = false;
  private activeStep: string | undefined;
  private lastStepStatus: Schemas["StepFinish"]["status"] | undefined;
  private readonly steps = new Map<string, StepSlot>();
  private readonly modelCalls = new Set<string>();
  private terminal: Terminal | undefined;

  constructor(options: { requestId: string; extensions?: ExtensionSchemas }) {
    this.requestId = options.requestId;
    this.extensions = options.extensions;
  }

  private readonly requestId: string;
  private readonly extensions: ExtensionSchemas | undefined;

  accept(packet: Packet, frameBytes: number): (Progress | Schemas["RunStarted"])[] {
    check(
      !this.terminal &&
        packet.protocol === PROTOCOL &&
        packet.version === VERSION &&
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
    if (event.type === "stream.batch") check(frameBytes <= LIMITS.batchBytes, "batch_bytes");
    this.eventCount += items.length;
    check(this.eventCount <= LIMITS.events, "event_count");

    const visible: (Progress | Schemas["RunStarted"])[] = [];
    // Validate all inner events before any from this packet can reach the caller.
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
          check(byteLength(info.data) <= LIMITS.extensionBytes, "extension_bytes");
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

  eof(): Terminal {
    check(this.terminal, "missing_terminal");
    return this.terminal;
  }

  private openStep(stepId: string): StepSlot {
    const step = this.steps.get(stepId);
    check(step && stepId === this.activeStep, "unknown_or_closed_step");
    return step;
  }

  private checkTerminal(runId: string, event: Terminal): void {
    check(!this.activeStep, "terminal_with_open_step");
    const { payload } = event;
    if (payload.status !== "completed") return;
    check(this.lastStepStatus === "completed", "success_without_completed_step");
    check(
      payload.finalAnswer.trim() && byteLength(payload.finalAnswer) <= LIMITS.answerBytes,
      "final_answer",
    );
    if (payload.file.status === "ready") {
      const file = payload.file;
      checkFile(file, runId, this.steps.get(file.stepId)?.tools.get(file.toolCallId));
    }
  }
}

type ReadOptions = {
  requestId: string;
  parse: ParsePacket;
  signal: AbortSignal;
  extensions?: ExtensionSchemas;
};

export async function* readRun(body: ReadableStream<Uint8Array>, options: ReadOptions) {
  const sequence = new RunSequence(options);
  for await (const { data, bytes } of readFrames(body, options.signal)) {
    const packet = options.parse(data);
    for (const event of sequence.accept(packet, bytes)) {
      yield { runId: packet.runId, event };
    }
  }
  options.signal.throwIfAborted();
  const event = sequence.eof();
  yield { runId: sequence.runId!, event };
}

type ProjectOptions = ReadOptions & {
  batch: boolean;
  publicExtensions?: ExtensionSchemas;
  // Only registered optional extension data can be mapped. Core events keep identity and payload.
  translateExtension?: (event: Extension, window: readonly Progress[]) => Extension | undefined;
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

/** BFF owns output sequence. The caller aborts the fetch when response consumption ends. */
export async function* projectRun(
  body: ReadableStream<Uint8Array>,
  options: ProjectOptions,
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
  let window: Progress[] = [];
  let due = Infinity;
  let windowBytes = 0;
  let heartbeatAt = Infinity;

  const packetFor = (event: Event): Packet => ({
    protocol: PROTOCOL,
    version: VERSION,
    requestId: options.requestId,
    runId,
    seq: seq + 1,
    event,
  });

  function charge(frame: Uint8Array) {
    signal.throwIfAborted();
    check(sentBytes + frame.byteLength <= LIMITS.streamBytes, "stream_bytes");
    sentBytes += frame.byteLength;
    heartbeatAt = performance.now() + LIMITS.heartbeatMs;
    return frame;
  }

  function encode(event: Event) {
    signal.throwIfAborted();
    const candidate = packetFor(event);
    checkJsonDepth(candidate);
    const packet = options.parse(candidate);
    const frame = encodePacket(packet);
    check(frame.byteLength <= LIMITS.frameBytes, "frame_bytes");
    projected.accept(packet, frame.byteLength);
    seq += 1;
    return charge(frame);
  }

  function* flush() {
    const previous = window;
    window = [];
    due = Infinity;
    windowBytes = 0;
    let group: Progress[] = [];
    for (const item of previous) {
      let mapped: Progress | undefined = item;
      if (item.type === "stream.extension") {
        if (options.translateExtension) {
          const snapshot = structuredClone(previous);
          const index = previous.indexOf(item);
          mapped = options.translateExtension(snapshot[index] as Extension, snapshot);
        } else {
          mapped = undefined;
        }
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
        encodePacket(packetFor({ type: "stream.batch", payload: combined })).byteLength >
          LIMITS.batchBytes
      ) {
        yield encode(group.length === 1 ? group[0] : { type: "stream.batch", payload: group });
        group = [];
      }
      group.push(mapped);
    }
    if (group.length) {
      yield encode(group.length === 1 ? group[0] : { type: "stream.batch", payload: group });
    }
  }

  // Catch read failures even when downstream demand is paused during a timed flush.
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
        yield encode(event); // readRun yields this only after upstream EOF.
        return;
      } else {
        const itemBytes = byteLength(event) + 1;
        if (window.length && windowBytes + itemBytes > LIMITS.batchBytes) yield* flush();
        if (!window.length) due = performance.now() + LIMITS.batchWaitMs;
        window.push(event);
        windowBytes += itemBytes;
        if (!options.batch || window.length >= LIMITS.batchItems) yield* flush();
      }
      pending = next();
    }
  } finally {
    local.abort();
    await source.return(undefined);
  }
}

/** Pull adapter so the downstream HTTP writer supplies demand instead of growing a queue. */
export async function projectBody(body: ReadableStream<Uint8Array>, options: ProjectOptions) {
  const local = new AbortController();
  const output = projectRun(body, {
    ...options,
    signal: AbortSignal.any([options.signal, local.signal]),
  });
  const first = await output.next();
  check(!first.done, "missing_start");
  let initial: Uint8Array | undefined = first.value;
  const abort = () => void stop(options.signal.reason).catch(() => undefined);
  const dispose = () => options.signal.removeEventListener("abort", abort);
  async function stop(reason: unknown) {
    dispose();
    local.abort(reason);
    await output.return(undefined);
  }
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
          } else controller.enqueue(value);
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
