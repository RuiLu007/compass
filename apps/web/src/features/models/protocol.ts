import { parseModelContract } from "@/contracts/model-contract";
import type { CallResponse, ModelEvent } from "./types";

export function parseModelEvent(value: unknown): ModelEvent {
  try {
    return parseModelContract<ModelEvent>("ModelEvent", value);
  } catch {
    throw new Error("模型事件格式不兼容，已停止接收。");
  }
}
export function parseCallResponse(value: unknown): CallResponse {
  try {
    return parseModelContract<CallResponse>("CallResponse", value);
  } catch {
    throw new Error("模型结果格式不兼容，请稍后重试。");
  }
}

export function parseRoutedResponse(value: unknown) {
  return parseModelContract<import("./types").RoutedCallResponse>("RoutedCallResponse", value);
}
export function parseRoutedEvent(value: unknown) {
  return parseModelContract<import("./types").RoutedModelEvent>("RoutedModelEvent", value);
}
