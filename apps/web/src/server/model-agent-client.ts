import "server-only";
import { fetchAgent, boundedJson } from "./agent-transport";
import { traceHeaders } from "../contracts/trace-context";

const errorTraces = new WeakMap<Error, Record<string, string>>();
export const modelErrorTraceHeaders = (error: unknown) =>
  error instanceof Error ? (errorTraces.get(error) ?? {}) : {};
import { isModelContract } from "../contracts/model-contract";
import type { components } from "../generated/api";
import type { TrustedModelContext } from "./modules/models/public";
import { ApplicationError, MODEL_ERRORS, type ErrorCode } from "./platform/errors";
import { privateHeaders } from "./http/responses";

const LIMIT = 2 * 1024 * 1024;
const unavailable = () => new ApplicationError("unavailable", "模型服务暂时不可用。");
const protocolError = () => new ApplicationError("provider_protocol_error", "模型响应格式异常。");
type ModelEvent = components["schemas"]["ModelEvent"] | components["schemas"]["ShowcaseEvent"];
type Options = {
  path: string;
  method?: "GET" | "POST" | "PATCH";
  input?: unknown;
  schema: string;
  context: TrustedModelContext;
  revision: string;
  signal: AbortSignal;
  callId?: string;
  providerId?: string;
  stream?: boolean;
  timeoutMs?: number;
  onStreamFailure?: (error: unknown) => Promise<void>;
};

function domainError(value: unknown): ApplicationError {
  if (!isModelContract("ModelErrorResponse", value)) return protocolError();
  const { error } = value as components["schemas"]["ModelErrorResponse"];
  if (Object.hasOwn(MODEL_ERRORS, error.code)) {
    const code = error.code as keyof typeof MODEL_ERRORS;
    return new ApplicationError(
      code,
      MODEL_ERRORS[code].message,
      error.currentVersion == null ? undefined : String(error.currentVersion),
    );
  }
  const safe: Partial<Record<ErrorCode, string>> = {
    forbidden: "无权进行此操作。",
    not_found: "模型或记录不存在。",
    validation_failed: "请检查输入内容。",
    unavailable: "模型服务暂时不可用。",
  };
  const code = error.code as ErrorCode;
  return Object.hasOwn(safe, code) ? new ApplicationError(code, safe[code]!) : unavailable();
}

/** The database authorization transaction has committed before this function runs. */
export async function requestModelAgent(options: Options): Promise<Response> {
  const serviceToken = process.env.COMPASS_AGENT_SERVICE_TOKEN;
  if (!serviceToken) throw unavailable();
  const abort = new AbortController();
  const cancel = () => abort.abort(options.signal.reason);
  options.signal.addEventListener("abort", cancel, { once: true });
  if (options.signal.aborted) cancel();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    abort.abort();
  }, options.timeoutMs ?? 190_000);
  const cleanup = () => {
    clearTimeout(timer);
    options.signal.removeEventListener("abort", cancel);
  };
  let correlation: Record<string, string> = {};
  try {
    const upstream = await fetchAgent(options, serviceToken, abort.signal);
    correlation = traceHeaders(upstream.headers);
    if (!upstream.ok) throw domainError(await boundedJson(upstream, protocolError));
    if (options.stream) {
      if (
        !upstream.headers.get("content-type")?.startsWith("text/event-stream") ||
        !upstream.body ||
        !options.callId
      ) {
        await upstream.body?.cancel();
        throw protocolError();
      }
      return validatedStream(upstream.body, options, abort, cleanup, correlation);
    }
    if (!upstream.headers.get("content-type")?.includes("application/json")) {
      await upstream.body?.cancel();
      throw protocolError();
    }
    const value = await boundedJson(upstream, protocolError);
    if (!isModelContract(options.schema, value)) throw protocolError();
    if (options.callId && (value as { call_id?: string }).call_id !== options.callId)
      throw protocolError();
    if (
      options.providerId &&
      (value as { provider_id?: string }).provider_id !== options.providerId
    )
      throw protocolError();
    cleanup();
    return Response.json(value, {
      status: upstream.status,
      headers: { ...privateHeaders, ...correlation, "X-Compass-View-Revision": options.revision },
    });
  } catch (error) {
    abort.abort();
    cleanup();
    const failure = timedOut
      ? new ApplicationError("model_timeout", MODEL_ERRORS.model_timeout.message)
      : error instanceof ApplicationError
        ? error
        : unavailable();
    errorTraces.set(failure, correlation);
    throw failure;
  }
}

function validatedStream(
  body: ReadableStream<Uint8Array>,
  options: Options,
  abort: AbortController,
  cleanup: () => void,
  correlation: Record<string, string>,
): Response {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const encoder = new TextEncoder();
  let buffer = "",
    total = 0,
    start: Extract<ModelEvent, { type: "start" }> | undefined;
  let final: Extract<ModelEvent, { type: "final" }> | undefined;
  let canceled = false;
  const output = new ReadableStream<Uint8Array>({
    async start(controller) {
      const emit = (event: ModelEvent) =>
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      const frame = (value: string) => {
        const lines = value.split("\n");
        const data = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (!data) return;
        const event: unknown = JSON.parse(data);
        if (
          !isModelContract(
            options.schema === "ShowcaseReport" ? "ShowcaseEvent" : "ModelEvent",
            event,
          )
        )
          throw protocolError();
        const typed = event as ModelEvent;
        if (typed.call_id !== options.callId || final) throw protocolError();
        if (typed.type === "start") {
          if (start) throw protocolError();
          if (
            options.providerId &&
            (!("provider_id" in typed) || typed.provider_id !== options.providerId)
          )
            throw protocolError();
          start = typed;
        } else if (!start) throw protocolError();
        if (typed.type === "final") {
          if (options.schema === "ShowcaseReport") {
            if (!("simulation" in typed) || typed.simulation !== true) throw protocolError();
          } else if (
            !("provider_id" in typed) ||
            !(start && "provider_id" in start) ||
            typed.provider_id !== start.provider_id ||
            typed.config_version !== start.config_version
          )
            throw protocolError();
          final = typed; // Validate clean EOF before announcing completion.
        } else emit(typed);
      };
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) {
            buffer += decoder.decode();
            break;
          }
          total += value.byteLength;
          if (total > LIMIT) throw protocolError();
          buffer += decoder.decode(value, { stream: true });
          buffer = buffer.replace(/\r\n/g, "\n");
          let boundary: number;
          while ((boundary = buffer.indexOf("\n\n")) >= 0) {
            frame(buffer.slice(0, boundary));
            buffer = buffer.slice(boundary + 2);
          }
        }
        if (buffer.trim() || !final) throw protocolError();
        if (!canceled) {
          emit(final);
          controller.close();
        }
      } catch (error) {
        abort.abort();
        if (!canceled) {
          controller.error(protocolError());
          await options.onStreamFailure?.(error);
        }
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
        cleanup();
      }
    },
    async cancel(reason) {
      canceled = true;
      abort.abort(reason);
      await reader.cancel(reason).catch(() => {});
      cleanup();
    },
  });
  return new Response(output, {
    headers: {
      ...privateHeaders,
      ...correlation,
      "Content-Type": "text/event-stream; charset=utf-8",
      "X-Accel-Buffering": "no",
      "X-Compass-View-Revision": options.revision,
    },
  });
}
