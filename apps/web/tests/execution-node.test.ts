import { describe, expect, it } from "vitest";
import { ExecutionSequence, type ExecutionNode } from "../src/contracts/execution-node";
import {
  RUN_STREAM_LIMITS,
  autonomyExtensions,
  parseRunPacket,
  projectAutonomyExtension,
  RunSequence,
} from "../src/contracts/analysis-run";
import { applyAnalysisRunEvent, type AnalysisTurnView } from "../src/client/analysis-run";

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const node = (n: number, fields: Partial<ExecutionNode> = {}): ExecutionNode => ({
  id: id(n),
  revision: 1,
  kind: "workflow",
  status: "running",
  title: "渠道分析",
  startedAtMs: 1000,
  dependsOn: [],
  display: { policy: "summary" },
  ...fields,
});
const extension = (value: ExecutionNode) => ({
  type: "stream.extension" as const,
  payload: { name: "compass.execution.node", version: 1, required: false, data: value },
});

describe("unified execution protocol", () => {
  it("passes the BFF projection, preserves parentage and merges by identity", () => {
    const sequence = new RunSequence({ requestId: id(100), extensions: autonomyExtensions });
    const events = [
      { type: "run.started", payload: {} },
      extension(node(1)),
      extension(node(2, { parentId: id(1), kind: "group", scheduling: "parallel" })),
      extension(
        node(3, {
          parentId: id(2),
          kind: "tool",
          origin: { kind: "mcp", serverId: "retail", toolName: "query" },
          executor: { kind: "sandbox", runtime: "Python" },
        }),
      ),
      extension(
        node(3, { parentId: id(2), kind: "tool", revision: 2, status: "failed", endedAtMs: 2000 }),
      ),
    ];
    let turn = {
      id: id(99),
      requestId: id(100),
      question: "test",
      modelLabel: "model",
      steps: [],
      phase: "running",
      stopState: "idle",
    } as AnalysisTurnView;
    events.forEach((event, index) => {
      const packet = parseRunPacket({
        protocol: "compass.run",
        version: 1,
        requestId: id(100),
        runId: id(101),
        seq: index + 1,
        event,
      });
      sequence.accept(packet, 1000);
      turn = applyAnalysisRunEvent(turn, id(101), packet.event);
      if (packet.event.type === "stream.extension")
        expect(projectAutonomyExtension(packet.event)).toBeDefined();
    });
    expect(turn.execution?.map((entry) => entry.id)).toEqual([id(1), id(2), id(3)]);
    expect(turn.execution?.[2].status).toBe("failed");
  });

  it("rejects orphan, reparented, cyclic, replayed and terminal updates", () => {
    for (const invalid of [
      node(2, { parentId: id(99) }),
      node(1, { revision: 3 }),
      node(1, { revision: 2, parentId: id(2) }),
      node(1, { revision: 2, dependsOn: [id(2)] }),
    ]) {
      const sequence = new ExecutionSequence();
      sequence.accept(node(1), RUN_STREAM_LIMITS);
      sequence.accept(node(2, { dependsOn: [id(1)] }), RUN_STREAM_LIMITS);
      expect(() => sequence.accept(invalid, RUN_STREAM_LIMITS)).toThrow();
    }
    const sequence = new ExecutionSequence();
    sequence.accept(node(1, { status: "completed", endedAtMs: 1200 }), RUN_STREAM_LIMITS);
    expect(() => sequence.accept(node(1, { revision: 2 }), RUN_STREAM_LIMITS)).toThrow();
  });

  it("enforces display policy and MCP identity without guessing execution environment", () => {
    for (const invalid of [
      node(1, { kind: "tool", origin: { kind: "mcp", toolName: "query" } }),
      node(1, { display: { policy: "summary", inputPreview: "private" } }),
      node(1, { executor: { kind: "sandbox" }, endedAtMs: 1200 }),
    ]) {
      expect(() => new ExecutionSequence().accept(invalid, RUN_STREAM_LIMITS)).toThrow();
    }
  });
});
