import "server-only";
import type { components } from "../generated/api";
import { isModelContract } from "../contracts/model-contract";
import {
  parseAnalysisCancelAck,
  RUN_STREAM_LIMITS,
  autonomyExtensions,
  projectAutonomyExtension,
  projectRunBody,
  ProtocolError,
  type AnalysisCancelAck,
} from "../contracts/analysis-run";
import { traceHeaders } from "../contracts/trace-context";
import type { TrustedModelContext } from "./modules/models/public";
import { ApplicationError, MODEL_ERRORS, type ErrorCode } from "./platform/errors";
import { privateHeaders } from "./http/responses";

const JSON_LIMIT = 64 * 1024;
const START_TIMEOUT_MS = (RUN_STREAM_LIMITS.timeoutSeconds + 15) * 1_000;
const CANCEL_TIMEOUT_MS = 5_000;

type AnalysisRunContract = components["schemas"]["AnalysisRunInput"];
type WorkflowEntry = "workflowAssetRef";
export type AnalysisRunAgentInput = Omit<AnalysisRunContract, WorkflowEntry | "constraints"> &
  Partial<Pick<AnalysisRunContract, WorkflowEntry>> & {
    constraints?: Partial<components["schemas"]["RunConstraints"]> | null;
  };

type CommonOptions = {
  context: TrustedModelContext;
  revision: string;
  signal: AbortSignal;
};

type StartOptions = CommonOptions & {
  input: AnalysisRunAgentInput;
  onStreamFailure: (error: unknown) => Promise<void>;
};

type CancelOptions = CommonOptions & { runId: string };

const errorTraces = new WeakMap<Error, Record<string, string>>();
export const analysisErrorTraceHeaders = (error: unknown) =>
  error instanceof Error ? (errorTraces.get(error) ?? {}) : {};

const unavailable = () => new ApplicationError("unavailable", "Agent 执行服务暂时不可用。");
const protocolError = () => new ApplicationError("unavailable", "Agent 运行响应格式异常。");

async function boundedJson(response: Response): Promise<unknown> {
  if (!response.body) throw protocolError();
  const reader = response.body.getReader();
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > JSON_LIMIT) throw protocolError();
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    if (error instanceof ApplicationError) throw error;
    throw protocolError();
  } finally {
    reader.releaseLock();
  }
}

function domainError(value: unknown, status: number): ApplicationError {
  if (status === 429) return new ApplicationError("rate_limited", "当前运行任务较多，请稍后重试。");
  if (!isModelContract("ModelErrorResponse", value)) return protocolError();
  const { error } = value as components["schemas"]["ModelErrorResponse"];
  if (status === 403 && ["source_access_denied", "context_access_changed"].includes(error.code))
    return new ApplicationError("forbidden", "当前权限无法读取这份结果的来源。");
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
    not_found: "运行任务不存在。",
    validation_failed: "请检查输入内容。",
    rate_limited: "当前运行任务较多，请稍后重试。",
    unavailable: "Agent 执行服务暂时不可用。",
  };
  const code = error.code as ErrorCode;
  return Object.hasOwn(safe, code) ? new ApplicationError(code, safe[code]!) : unavailable();
}

function lifetime(signal: AbortSignal, timeoutMs: number) {
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort(signal.reason);
  signal.addEventListener("abort", cancel, { once: true });
  if (signal.aborted) cancel();
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException("Timed out", "TimeoutError"));
  }, timeoutMs);
  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  };
  return { controller, dispose, timedOut: () => timedOut };
}

function agentHeaders(context: TrustedModelContext, serviceToken: string, accept: string) {
  return {
    Authorization: `Bearer ${serviceToken}`,
    "X-Compass-Context": Buffer.from(JSON.stringify(context)).toString("base64url"),
    Accept: accept,
    "Content-Type": "application/json",
  };
}

function ownedStream(
  source: ReadableStream<Uint8Array>,
  owner: AbortController,
  dispose: () => void,
  onFailure: (error: unknown) => Promise<void>,
) {
  const reader = source.getReader();
  let closed = false;
  let reported = false;
  const finish = () => {
    if (closed) return;
    closed = true;
    dispose();
    reader.releaseLock();
  };
  const report = async (error: unknown) => {
    if (reported) return;
    reported = true;
    await onFailure(error);
  };
  return new ReadableStream<Uint8Array>(
    {
      async pull(controller) {
        try {
          owner.signal.throwIfAborted();
          const { done, value } = await reader.read();
          if (done) {
            controller.close();
            finish();
          } else {
            controller.enqueue(value);
          }
        } catch (error) {
          owner.abort(error);
          controller.error(protocolError());
          await reader.cancel(error).catch(() => undefined);
          try {
            await report(error);
          } finally {
            finish();
          }
        }
      },
      async cancel(reason) {
        owner.abort(reason);
        await reader.cancel(reason).catch(() => undefined);
        finish();
      },
    },
    { highWaterMark: 0 },
  );
}

/** Authorization has committed before this private Agent request starts. */
export async function startAnalysisRunAgent(options: StartOptions): Promise<Response> {
  const serviceToken = process.env.COMPASS_AGENT_SERVICE_TOKEN;
  if (!serviceToken) throw unavailable();
  const active = lifetime(options.signal, START_TIMEOUT_MS);
  let correlation: Record<string, string> = {};
  try {
    const upstream = await fetch(
      new URL("/v1/analysis/runs", process.env.AGENT_API_URL ?? "http://127.0.0.1:8102"),
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        signal: active.controller.signal,
        headers: agentHeaders(options.context, serviceToken, "text/event-stream"),
        body: JSON.stringify(options.input),
      },
    );
    correlation = traceHeaders(upstream.headers);
    if (!upstream.ok) throw domainError(await boundedJson(upstream), upstream.status);
    if (
      upstream.status !== 200 ||
      !upstream.headers.get("content-type")?.toLowerCase().startsWith("text/event-stream") ||
      !upstream.body
    ) {
      await upstream.body?.cancel().catch(() => undefined);
      throw protocolError();
    }
    const projected = await projectRunBody(upstream.body, {
      requestId: options.input.requestId,
      signal: active.controller.signal,
      batch: true,
      extensions: autonomyExtensions,
      publicExtensions: autonomyExtensions,
      translateExtension: projectAutonomyExtension,
    });
    const body = ownedStream(projected, active.controller, active.dispose, options.onStreamFailure);
    return new Response(body, {
      status: 200,
      headers: {
        ...privateHeaders,
        ...correlation,
        "Content-Type": "text/event-stream; charset=utf-8",
        "X-Accel-Buffering": "no",
        "X-Compass-View-Revision": options.revision,
      },
    });
  } catch (error) {
    active.controller.abort(error);
    active.dispose();
    const failure =
      active.timedOut() || error instanceof ProtocolError
        ? protocolError()
        : error instanceof ApplicationError
          ? error
          : unavailable();
    errorTraces.set(failure, correlation);
    throw failure;
  }
}

/** Cancellation is a bounded control request; the original stream owns the terminal event. */
export async function cancelAnalysisRunAgent(options: CancelOptions): Promise<Response> {
  const serviceToken = process.env.COMPASS_AGENT_SERVICE_TOKEN;
  if (!serviceToken) throw unavailable();
  const active = lifetime(options.signal, CANCEL_TIMEOUT_MS);
  let correlation: Record<string, string> = {};
  try {
    const upstream = await fetch(
      new URL(
        `/v1/analysis/runs/${encodeURIComponent(options.runId)}/cancel`,
        process.env.AGENT_API_URL ?? "http://127.0.0.1:8102",
      ),
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        signal: active.controller.signal,
        headers: agentHeaders(options.context, serviceToken, "application/json"),
        body: "{}",
      },
    );
    correlation = traceHeaders(upstream.headers);
    if (!upstream.ok) throw domainError(await boundedJson(upstream), upstream.status);
    if (
      ![200, 202].includes(upstream.status) ||
      !upstream.headers.get("content-type")?.toLowerCase().includes("application/json")
    ) {
      await upstream.body?.cancel().catch(() => undefined);
      throw protocolError();
    }
    const ack: AnalysisCancelAck = parseAnalysisCancelAck(await boundedJson(upstream));
    if (
      ack.runId !== options.runId ||
      (upstream.status === 202) !== (ack.status === "accepted") ||
      (upstream.status === 200) !== (ack.status === "already_terminal")
    )
      throw protocolError();
    active.dispose();
    return Response.json(ack, {
      status: upstream.status,
      headers: {
        ...privateHeaders,
        ...correlation,
        "X-Compass-View-Revision": options.revision,
      },
    });
  } catch (error) {
    active.controller.abort(error);
    active.dispose();
    const failure =
      active.timedOut() || error instanceof ProtocolError
        ? protocolError()
        : error instanceof ApplicationError
          ? error
          : unavailable();
    errorTraces.set(failure, correlation);
    throw failure;
  }
}

export async function workflowAssetAgent(
  options: CommonOptions & {
    assetId?: string;
    draftId?: string;
    cursor?: string;
    patch?: components["schemas"]["WorkflowAssetPatch"];
    confirm?: components["schemas"]["WorkflowDraftConfirm"];
    remove?: components["schemas"]["WorkflowAssetDelete"];
  },
): Promise<Response> {
  const serviceToken = process.env.COMPASS_AGENT_SERVICE_TOKEN;
  if (!serviceToken) throw unavailable();
  const active = lifetime(options.signal, CANCEL_TIMEOUT_MS);
  try {
    const suffix = options.draftId
      ? `/drafts/${encodeURIComponent(options.draftId)}${options.confirm ? "/confirm" : ""}`
      : options.assetId
        ? "/" + encodeURIComponent(options.assetId)
        : "";
    const url = new URL(
      "/v1/analysis/workflows" + suffix,
      process.env.AGENT_API_URL ?? "http://127.0.0.1:8102",
    );
    if (options.cursor) url.searchParams.set("cursor", options.cursor);
    const command = options.patch ?? options.confirm ?? options.remove;
    const upstream = await fetch(url, {
      method: options.remove
        ? "DELETE"
        : options.patch
          ? "PATCH"
          : options.confirm
            ? "POST"
            : "GET",
      headers: agentHeaders(options.context, serviceToken, "application/json"),
      body: command ? JSON.stringify(command) : undefined,
      signal: active.controller.signal,
      redirect: "error",
      cache: "no-store",
    });
    const headers = { ...privateHeaders, "X-Compass-View-Revision": options.revision };
    if (options.remove && upstream.status === 204)
      return new Response(null, { status: 204, headers });
    const value = await boundedJson(upstream);
    if (!upstream.ok) throw domainError(value, upstream.status);
    if (options.remove) throw protocolError();
    const schema =
      options.draftId && !options.confirm
        ? "WorkflowDraftView"
        : options.assetId || options.confirm
          ? "WorkflowAssetView"
          : "WorkflowAssetList";
    if (!isModelContract(schema, value)) throw protocolError();
    return Response.json(value, { headers });
  } catch (error) {
    throw error instanceof ApplicationError ? error : unavailable();
  } finally {
    active.dispose();
  }
}

export async function analysisArtifactAgent(
  options: CommonOptions & { runId: string; artifactId: string },
): Promise<Response> {
  const serviceToken = process.env.COMPASS_AGENT_SERVICE_TOKEN;
  if (!serviceToken) throw unavailable();
  const active = lifetime(options.signal, CANCEL_TIMEOUT_MS);
  try {
    const url = new URL(
      `/v1/analysis/runs/${encodeURIComponent(options.runId)}/artifacts/${encodeURIComponent(options.artifactId)}`,
      process.env.AGENT_API_URL ?? "http://127.0.0.1:8102",
    );
    const upstream = await fetch(url, {
      headers: agentHeaders(
        options.context,
        serviceToken,
        "image/png, text/csv, application/json, text/markdown",
      ),
      signal: active.controller.signal,
      redirect: "error",
      cache: "no-store",
    });
    if (!upstream.ok) throw domainError(await boundedJson(upstream), upstream.status);
    const media = upstream.headers.get("content-type") ?? "";
    const csv = media === "text/csv; charset=utf-8";
    const dashboard = media === "application/json";
    const report = media === "text/markdown; charset=utf-8";
    if ((!csv && !dashboard && !report && media !== "image/png") || !upstream.body)
      throw protocolError();
    const reader = upstream.body.getReader();
    let size = 0;
    const chunks: Uint8Array[] = [];
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 512 * 1024) throw protocolError();
        chunks.push(value);
      }
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
    const data = Buffer.concat(chunks);
    if (
      media === "image/png" &&
      (data.length < 8 || data.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a")
    )
      throw protocolError();
    if (dashboard && !isModelContract("DashboardDocumentV1", JSON.parse(data.toString("utf8"))))
      throw protocolError();
    if (report) new TextDecoder("utf-8", { fatal: true }).decode(data);
    return new Response(data, {
      headers: {
        ...privateHeaders,
        "Content-Type": media,
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition":
          media === "image/png"
            ? 'inline; filename="chart.png"'
            : `attachment; filename="${dashboard ? "dashboard.json" : report ? "report.md" : "analysis.csv"}"`,
        "X-Compass-View-Revision": options.revision,
      },
    });
  } catch (error) {
    throw error instanceof ApplicationError ? error : unavailable();
  } finally {
    active.dispose();
  }
}
