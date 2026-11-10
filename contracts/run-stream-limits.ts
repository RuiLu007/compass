export const RUN_STREAM_PROTOCOL = "compass.run" as const;
export const RUN_STREAM_VERSION = 1 as const;
export const RUN_STREAM_LIMITS = {
  frameBytes: 262144,
  streamBytes: 2097152,
  events: 96,
  batchItems: 16,
  batchBytes: 32768,
  batchWaitMs: 100,
  heartbeatMs: 15000,
  extensionBytes: 4096,
  jsonDepth: 20,
  answerBytes: 32768,
  fileBytes: 131072,
  fileRows: 200,
  steps: 6,
  toolsPerStep: 4,
} as const;
