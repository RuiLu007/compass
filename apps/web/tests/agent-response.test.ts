import { describe, expect, it } from "vitest";
import {
  agentResponse,
  compareStreamIds,
  reduceAgentResponse,
} from "../src/contracts/agent-response";
const sessionId = "10000000-0000-4000-8000-000000000001",
  runId = "20000000-0000-4000-8000-000000000001",
  requestId = "30000000-0000-4000-8000-000000000001",
  messageId = "40000000-0000-4000-8000-000000000001";
const packet = (event: unknown, seq: string | null, id = "packet") => ({
  packetId: id,
  runId,
  requestId,
  occurredAt: "2026-11-12T00:00:00+00:00",
  seq,
  event,
});
const snapshot = () => ({
  protocol: "compass.run",
  version: 2,
  sessionId,
  mode: "snapshot",
  packets: [
    packet(
      {
        type: "run.upsert",
        payload: {
          ordinal: 1,
          version: 1,
          status: "running",
          reasonCode: null,
          recoveryStatus: "ready",
          cleanupStatus: "none",
        },
      },
      null,
      "run",
    ),
    packet(
      {
        type: "message.upsert",
        payload: {
          messageId,
          ordinal: 2,
          role: "assistant",
          revision: 1,
          status: "streaming",
          content: { text: "", artifactIds: [] },
        },
      },
      null,
      "message",
    ),
  ],
  cursors: [{ runId, seq: "12-0" }],
  page: { nextCursor: null },
});
const delta = (revision: number, seq = "12-1") => ({
  protocol: "compass.run",
  version: 2,
  sessionId,
  mode: "delta",
  packets: [
    packet({ type: "message.delta", payload: { messageId, revision, text: "完整保存" } }, seq),
  ],
  cursors: [{ runId, seq }],
  page: null,
});
describe("durable response", () => {
  it("merges snapshots and deltas through one reducer with duplicate suppression", () => {
    const start = reduceAgentResponse(null, snapshot());
    const live = reduceAgentResponse(start, delta(2));
    expect(live.runs[runId].messages[messageId].content.text).toBe("完整保存");
    expect(start.runs[runId].messages[messageId].content.text).toBe("");
    expect(reduceAgentResponse(live, delta(2))).toEqual(live);
    expect(reduceAgentResponse(live, snapshot())).toEqual(live);
  });
  it("rejects revision gaps atomically", () => {
    const start = reduceAgentResponse(null, snapshot());
    expect(() => reduceAgentResponse(start, delta(3))).toThrow();
    expect(start.runs[runId].cursor).toBe("12-0");
  });
  it("rejects private data, wrong cursor and malformed mode", () => {
    expect(() => agentResponse({ ...delta(2), privateState: "secret" })).toThrow();
    expect(() => agentResponse({ ...delta(2), cursors: [{ runId, seq: "12-2" }] })).toThrow();
    expect(() => agentResponse({ ...snapshot(), mode: "delta" })).toThrow();
  });
  it("compares native IDs beyond JS number precision", () => {
    expect(compareStreamIds("18446744073709551615-2", "9999999999999999999-12")).toBe(1);
    expect(() => compareStreamIds("01-0", "1-0")).toThrow();
  });
});
