import "server-only";
import type { TrustedModelContext } from "./modules/models/public";

const LIMIT = 2 * 1024 * 1024;

/** The BFF rebuilds service credentials and trusted context for every request. */
export function fetchAgent(
  options: {
    path: string;
    context: TrustedModelContext;
    method?: string;
    input?: unknown;
    stream?: boolean;
  },
  token: string,
  signal: AbortSignal,
): Promise<Response> {
  return fetch(new URL(options.path, process.env.AGENT_API_URL ?? "http://127.0.0.1:8102"), {
    method: options.method ?? "GET",
    cache: "no-store",
    redirect: "error",
    signal,
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Compass-Context": Buffer.from(JSON.stringify(options.context)).toString("base64url"),
      Accept: options.stream ? "text/event-stream" : "application/json",
      ...(options.input === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(options.input === undefined ? {} : { body: JSON.stringify(options.input) }),
  });
}

export async function boundedJson(response: Response, invalid: () => Error): Promise<unknown> {
  if (!response.body) throw invalid();
  const reader = response.body.getReader();
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > LIMIT) throw invalid();
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    await reader.cancel().catch(() => {});
    throw invalid();
  } finally {
    reader.releaseLock();
  }
}
