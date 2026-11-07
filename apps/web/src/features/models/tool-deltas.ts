import type { ModelEvent, ToolCall } from "./types";

export function accumulateToolDelta(
  parts: Map<number, ToolCall>,
  event: Extract<ModelEvent, { type: "tool_delta" }>,
): ToolCall[] {
  const prior = parts.get(event.index);
  parts.set(event.index, {
    id: event.id ?? prior?.id ?? "",
    type: "function",
    function: {
      name: (prior?.function.name ?? "") + (event.name ?? ""),
      arguments: (prior?.function.arguments ?? "") + event.arguments,
    },
  });
  return [...parts.entries()].sort(([a], [b]) => a - b).map(([, value]) => value);
}
