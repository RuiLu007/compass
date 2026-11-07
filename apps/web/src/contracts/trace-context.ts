export type TraceInfo = { traceId?: string; traceSampled?: boolean };

export function traceInfo(headers: Headers): TraceInfo {
  const id = headers.get("X-Compass-Trace-Id");
  const sampled = headers.get("X-Compass-Trace-Sampled");
  return id && /^[0-9a-f]{32}$/.test(id) && /^(0|1)$/.test(sampled ?? "")
    ? { traceId: id, traceSampled: sampled === "1" }
    : {};
}
export function traceHeaders(headers: Headers): Record<string, string> {
  const info = traceInfo(headers);
  return info.traceId
    ? {
        "X-Compass-Trace-Id": info.traceId,
        "X-Compass-Trace-Sampled": info.traceSampled ? "1" : "0",
      }
    : {};
}
