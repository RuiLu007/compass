import type { components } from "@/generated/api";

export type Provider = components["schemas"]["ProviderView"];
export type ProviderPage = components["schemas"]["ProviderPage"];
export type CatalogItem = components["schemas"]["CatalogItem"];
export type CatalogPage = components["schemas"]["CatalogPage"];
export type AvailableModel = components["schemas"]["AvailableModel"];
export type AvailableModels = components["schemas"]["AvailableModels"];
export type GenerateInput = components["schemas"]["GenerateInput"];
export type DecisionInput = components["schemas"]["DecisionInput"];
export type CallResponse = components["schemas"]["CallResponse"];
export type ModelEvent = components["schemas"]["ModelEvent"];
export type ToolCall = components["schemas"]["ToolCall"];
export type SavedProvider = components["schemas"]["SavedProvider"];
export type ModelChangePage = components["schemas"]["ModelChangePage"];
export const modelStatus: Record<string, string> = {
  running: "生成中",
  completed: "已完成",
  failed: "失败",
  canceled: "已停止",
  incomplete: "结果不完整",
};
export const capabilityNames = {
  sync: "同步",
  stream: "流式",
  tools: "工具调用",
  thinking: "Thinking",
  json_output: "JSON",
  schema_output: "JSON Schema",
} as const;
