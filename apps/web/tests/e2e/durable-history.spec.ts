import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import {
  agentResponse,
  reduceAgentResponse,
  type AgentResponse,
  type Conversation,
} from "../../src/contracts/agent-response";

type DurableCase = { name: string; sse: AgentResponse[]; snapshot: AgentResponse };
const source = process.env.DURABLE_MATRIX_FILE;

function readCases(path: string): DurableCase[] {
  const raw: unknown = JSON.parse(readFileSync(resolve(path), "utf8"));
  if (!Array.isArray(raw) || raw.length === 0)
    throw new Error("DURABLE_MATRIX_FILE must contain at least one executed business case");
  const names = new Set<string>();
  return raw.map((entry: unknown) => {
    if (!entry || typeof entry !== "object") throw new Error("Invalid durable matrix entry");
    const value = entry as Record<string, unknown>;
    if (
      typeof value.name !== "string" ||
      !value.name.trim() ||
      names.has(value.name) ||
      !Array.isArray(value.sse) ||
      value.sse.length === 0
    )
      throw new Error("Each durable case needs a unique name and nonempty actual SSE frames");
    names.add(value.name);
    const sse = value.sse.map(agentResponse);
    const snapshot = agentResponse(value.snapshot);
    if (snapshot.mode !== "snapshot" || sse.some((frame) => frame.mode !== "delta"))
      throw new Error("Expected actual SSE delta frames and a Message snapshot");
    if (sse.some((frame) => frame.sessionId !== snapshot.sessionId))
      throw new Error("Durable case crosses session ownership");
    return { name: value.name, sse, snapshot };
  });
}

function emptyStreamState(frames: AgentResponse[]): Conversation {
  // Replay starts at 0-0. Seed only transport identities, never future Message contents.
  const ids = [...new Set(frames.flatMap((frame) => frame.cursors.map((cursor) => cursor.runId)))];
  return {
    sessionId: frames[0].sessionId,
    nextCursor: null,
    runs: Object.fromEntries(
      ids.map((runId) => [
        runId,
        { runId, requestId: "", cursor: "0-0", messages: {}, objects: {} },
      ]),
    ),
  };
}

function businessView(conversation: Conversation) {
  return {
    sessionId: conversation.sessionId,
    runs: Object.fromEntries(
      Object.entries(conversation.runs).map(([id, run]) => [
        id,
        {
          runId: run.runId,
          requestId: run.requestId,
          run: run.run,
          messages: run.messages,
          objects: run.objects,
        },
      ]),
    ),
  };
}

if (!source) {
  test("持久业务矩阵需要显式运行记录", () => {
    test.skip(true, "Set DURABLE_MATRIX_FILE to this run's exported real Agent business matrix");
  });
} else {
  for (const scenario of readCases(source)) {
    test(`${scenario.name}：实时结果与完整历史一致且重复交付幂等`, () => {
      let live = emptyStreamState(scenario.sse);
      for (const frame of scenario.sse) {
        live = reduceAgentResponse(live, frame);
        const duplicate = reduceAgentResponse(live, frame);
        expect(duplicate).toEqual(live);
      }
      const history = reduceAgentResponse(null, scenario.snapshot);
      expect(Object.keys(live.runs).sort()).toEqual(Object.keys(history.runs).sort());
      expect(businessView(live)).toEqual(businessView(history));

      const reloaded = reduceAgentResponse(live, scenario.snapshot);
      const reloadedAgain = reduceAgentResponse(reloaded, scenario.snapshot);
      expect(businessView(reloaded)).toEqual(businessView(history));
      expect(reloadedAgain).toEqual(reloaded);
      let lateFrames = reloadedAgain;
      for (const frame of scenario.sse) lateFrames = reduceAgentResponse(lateFrames, frame);
      expect(lateFrames).toEqual(reloadedAgain);
    });
  }
}
