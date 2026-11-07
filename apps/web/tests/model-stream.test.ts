import { describe, expect, it } from "vitest";
import { readModelEvents } from "../src/client/model-stream";
import { accumulateToolDelta } from "../src/features/models/tool-deltas";
import type { ToolCall } from "../src/features/models/types";
import { assertModelOutputBudget } from "../src/features/models/output-budget";

const encoder = new TextEncoder();
function stream(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(chunk));
      controller.close();
    },
  });
}
async function collect(chunks: Uint8Array[]) {
  const result: unknown[] = [];
  for await (const event of readModelEvents(stream(chunks), new AbortController().signal))
    result.push(event);
  return result;
}
describe("bounded model SSE parser", () => {
  it("decodes split UTF-8, CRLF frames, multiline data and keepalive comments", async () => {
    const bytes = encoder.encode(
      ': keepalive\r\n\r\ndata: {"type":"text_delta",\r\ndata: "text":"你好"}\r\n\r\n',
    );
    const chunks = Array.from(bytes, (byte) => new Uint8Array([byte]));
    expect(await collect(chunks)).toEqual([{ type: "text_delta", text: "你好" }]);
  });
  it("rejects truncated and malformed JSON rather than publishing partial frames", async () => {
    await expect(collect([encoder.encode('data: {"text":"partial"}')])).rejects.toThrow(
      "结果不完整",
    );
    await expect(collect([encoder.encode("data: {broken}\n\n")])).rejects.toThrow("格式不完整");
    await expect(collect([new Uint8Array([255])])).rejects.toThrow();
  });
  it("enforces both partial-frame and complete-frame budgets", async () => {
    const body = "x".repeat(2 * 1024 * 1024);
    await expect(collect([encoder.encode(`data: ${body}`)])).rejects.toThrow("片段超过");
    await expect(collect([encoder.encode(`data: ${body}\n\n`)])).rejects.toThrow("片段超过");
  });
  it("enforces aggregate stream budget even for small valid events", async () => {
    const frame = encoder.encode(`data: ${JSON.stringify({ text: "a".repeat(900000) })}\n\n`);
    await expect(collect(Array.from({ length: 10 }, () => frame))).rejects.toThrow("返回内容超过");
  });
  it("aborts an idle reader and releases its lock without accepting a late event", async () => {
    const controller = new AbortController();
    let canceled = false;
    const input = new ReadableStream<Uint8Array>({
      cancel() {
        canceled = true;
      },
    });
    const reading = (async () => {
      for await (const value of readModelEvents(input, controller.signal)) return value;
    })();
    controller.abort();
    await expect(reading).rejects.toMatchObject({ name: "AbortError" });
    expect(canceled).toBe(true);
    expect(input.locked).toBe(false);
  });
});

it("reassembles interleaved tools without losing split function names or argument bytes", () => {
  const parts = new Map<number, ToolCall>();
  const call_id = "99500000-0000-4000-8000-000000000001";
  accumulateToolDelta(parts, {
    type: "tool_delta",
    call_id,
    index: 1,
    id: "tool-b",
    name: "get_",
    arguments: '{"region":',
  });
  accumulateToolDelta(parts, {
    type: "tool_delta",
    call_id,
    index: 0,
    id: "tool-a",
    name: "get_",
    arguments: '{"month":',
  });
  accumulateToolDelta(parts, {
    type: "tool_delta",
    call_id,
    index: 1,
    id: null,
    name: "stock",
    arguments: '"华东"}',
  });
  const tools = accumulateToolDelta(parts, {
    type: "tool_delta",
    call_id,
    index: 0,
    id: null,
    name: "sales",
    arguments: '"2026-10"}',
  });
  expect(tools).toEqual([
    {
      id: "tool-a",
      type: "function",
      function: { name: "get_sales", arguments: '{"month":"2026-10"}' },
    },
    {
      id: "tool-b",
      type: "function",
      function: { name: "get_stock", arguments: '{"region":"华东"}' },
    },
  ]);
});

it("accepts exactly 1 MiB and rejects one extra UTF-8 byte for plain and tool output", () => {
  const limit = 1024 * 1024;
  expect(() => assertModelOutputBudget("x".repeat(limit), [])).not.toThrow();
  expect(() => assertModelOutputBudget("x".repeat(limit + 1), [])).toThrow("显示上限");
  const tool: ToolCall = {
    id: "tool-a",
    type: "function",
    function: { name: "get_sales", arguments: '{"region":"华东"}' },
  };
  const size = new TextEncoder().encode(JSON.stringify(tool)).byteLength;
  expect(() => assertModelOutputBudget("x".repeat(limit - size), [tool])).not.toThrow();
  expect(() => assertModelOutputBudget("x".repeat(limit - size + 1), [tool])).toThrow("显示上限");
});
