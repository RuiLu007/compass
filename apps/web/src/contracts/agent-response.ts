import type { components } from "../generated/api";
import { parseModelContract } from "./model-contract";

export type AgentResponse = components["schemas"]["AgentResponse"];
export type AgentPacket = components["schemas"]["AgentPacket"];
export type PublicObject = Extract<AgentPacket["event"], { type: "view.upsert" }>["payload"];
export type RunView = {
  runId: string;
  requestId: string;
  cursor: string;
  submittedAtMs?: number;
  finishedAtMs?: number;
  latestExecutionId?: string;
  run?: components["schemas"]["RunPublicSnapshot"];
  messages: Record<string, components["schemas"]["MessageSnapshot"]>;
  objects: Record<string, PublicObject>;
};
export type Conversation = {
  sessionId: string;
  runs: Record<string, RunView>;
  nextCursor: string | null;
};
const fail = (): never => {
  throw new Error("运行响应格式异常，请重新加载会话。");
};
export function compareStreamIds(left: string, right: string) {
  const parse = (value: string) => {
    if (!/^(0|[1-9]\d{0,19})-(0|[1-9]\d{0,19})$/.test(value)) return fail();
    const result = value.split("-").map(BigInt);
    if (result.some((part) => part > 18446744073709551615n)) return fail();
    return result;
  };
  const a = parse(left),
    b = parse(right);
  return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0;
}
export function agentResponse(value: unknown): AgentResponse {
  const response = parseModelContract<AgentResponse>("AgentResponse", value);
  const cursors = new Map(response.cursors.map((cursor) => [cursor.runId, cursor.seq]));
  if (
    cursors.size !== response.cursors.length ||
    response.packets.some((p) => !cursors.has(p.runId))
  )
    fail();
  for (const cursor of cursors.values()) compareStreamIds(cursor, "0-0");
  if (response.mode === "snapshot") {
    if (
      !response.page ||
      response.packets.some((p) => p.seq !== null || p.event.type === "message.delta")
    )
      fail();
  } else {
    if (response.page !== null || cursors.size !== 1 || !response.packets.length) fail();
    let previous = "0-0";
    for (const packet of response.packets) {
      if (packet.seq === null || compareStreamIds(packet.seq, previous) <= 0) fail();
      previous = packet.seq!;
    }
    if (previous !== response.cursors[0].seq) fail();
  }
  return response;
}
const canonical = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`)
      .join(",")}}`;
  return JSON.stringify(value);
};
function upsert<T extends { revision: number }>(rows: Record<string, T>, id: string, value: T) {
  const old = rows[id];
  if (old && old.revision >= value.revision) {
    if (old.revision === value.revision && canonical(old) !== canonical(value)) fail();
    return false;
  }
  rows[id] = value;
  return true;
}
/** Both full history and live packets enter this reducer; a failed envelope changes no state. */
export function reduceAgentResponse(state: Conversation | null, raw: unknown): Conversation {
  const response = agentResponse(raw);
  if (state && state.sessionId !== response.sessionId) fail();
  const next: Conversation = structuredClone(
    state ?? { sessionId: response.sessionId, runs: {}, nextCursor: null },
  );
  const ignored = new Set<string>();
  for (const cursor of response.cursors) {
    const old = next.runs[cursor.runId];
    if (response.mode === "snapshot") {
      if (old && compareStreamIds(old.cursor, cursor.seq) > 0) ignored.add(cursor.runId);
      else
        next.runs[cursor.runId] = {
          runId: cursor.runId,
          requestId: "",
          cursor: cursor.seq,
          finishedAtMs: old?.finishedAtMs,
          latestExecutionId: old?.cursor === cursor.seq ? old.latestExecutionId : undefined,
          messages: {},
          objects: {},
        };
    } else if (!old) fail();
  }
  for (const packet of response.packets) {
    if (ignored.has(packet.runId)) continue;
    const run = next.runs[packet.runId];
    if (packet.seq && compareStreamIds(packet.seq, run.cursor) <= 0) continue;
    if (run.requestId && run.requestId !== packet.requestId) fail();
    run.requestId = packet.requestId;
    const event = packet.event;
    const occurredAtMs = Date.parse(packet.occurredAt);
    switch (event.type) {
      case "run.upsert":
        if (!run.run || event.payload.version >= run.run.version) {
          if (
            run.run?.version === event.payload.version &&
            canonical(run.run) !== canonical(event.payload)
          )
            fail();
          run.run = event.payload;
          if (
            Number.isFinite(occurredAtMs) &&
            ["completed", "failed", "canceled"].includes(event.payload.status)
          )
            run.finishedAtMs ??= occurredAtMs;
        }
        break;
      case "view.upsert": {
        const changed = upsert(run.objects, event.payload.objectId, event.payload);
        if (packet.seq && changed && event.payload.kind === "execution.node")
          run.latestExecutionId = event.payload.value.id;
        break;
      }
      case "message.upsert":
        upsert(run.messages, event.payload.messageId, event.payload);
        if (event.payload.role === "user" && Number.isFinite(occurredAtMs))
          run.submittedAtMs ??= occurredAtMs;
        break;
      case "message.delta": {
        const message = run.messages[event.payload.messageId];
        if (
          !message ||
          message.status !== "streaming" ||
          message.revision + 1 !== event.payload.revision
        )
          fail();
        message.content.text += event.payload.text;
        message.revision = event.payload.revision;
        break;
      }
    }
    if (packet.seq) run.cursor = packet.seq;
  }
  if (response.mode === "snapshot") next.nextCursor = response.page!.nextCursor;
  for (const cursor of response.cursors) {
    if (!ignored.has(cursor.runId) && !next.runs[cursor.runId].run) fail();
  }
  return next;
}

export async function* readAgentResponses(body: ReadableStream<Uint8Array>, signal?: AbortSignal) {
  const reader = body.getReader(),
    decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "",
    total = 0;
  try {
    for (;;) {
      if (signal?.aborted) throw new DOMException("读取已停止", "AbortError");
      const { value, done } = await reader.read();
      if (done) {
        if (buffer.trim()) fail();
        return;
      }
      total += value.byteLength;
      if (total > 5 * 1024 * 1024) fail();
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        if (new TextEncoder().encode(frame).byteLength > 256 * 1024) fail();
        const lines = frame.split("\n");
        const data = lines.filter((line) => line.startsWith("data: ")).map((line) => line.slice(6));
        if (!data.length) continue;
        if (data.length !== 1 || !lines.includes("event: agent")) fail();
        const response = agentResponse(JSON.parse(data[0]));
        if (
          response.mode !== "delta" ||
          !lines.includes(`id: ${response.cursors[0].runId}:${response.cursors[0].seq}`)
        )
          fail();
        yield response;
      }
      if (new TextEncoder().encode(buffer).byteLength > 256 * 1024) fail();
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
