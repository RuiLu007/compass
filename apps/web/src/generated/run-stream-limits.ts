export const RUN_STREAM_PROTOCOL = "compass.run" as const;
export const RUN_STREAM_VERSION = 1 as const;
export const RUN_STREAM_LIMITS = {
  frameBytes: 262144,
  streamBytes: 4194304,
  events: 4096,
  batchItems: 16,
  batchBytes: 32768,
  batchWaitMs: 24,
  heartbeatMs: 15000,
  extensionBytes: 4096,
  toolInputBytes: 8192,
  toolResultBytes: 4096,
  jsonDepth: 20,
  answerBytes: 32768,
  fileBytes: 131072,
  fileRows: 200,
  steps: 100,
  timeoutSeconds: 600,
  toolsPerStep: 4,
  executionNodes: 768,
  executionDepth: 12,
} as const;
