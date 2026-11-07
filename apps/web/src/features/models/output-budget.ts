import type { ToolCall } from "./types";

/** Match Runtime's UTF-8 text plus individual tool JSON byte budget. */
export function assertModelOutputBudget(text: string, tools: ToolCall[]) {
  const encoder = new TextEncoder();
  const size =
    encoder.encode(text).byteLength +
    tools.reduce((total, tool) => total + encoder.encode(JSON.stringify(tool)).byteLength, 0);
  if (size > 1024 * 1024) throw new Error("模型输出已达到页面显示上限，结果不完整。");
}
