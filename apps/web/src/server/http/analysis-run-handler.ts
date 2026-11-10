import "server-only";
import { z } from "zod";
import { uuid } from "../../contracts/web";
import { execute, recordFailure } from "../composition";
import type { TrustedModelContext } from "../modules/models/public";
import { ApplicationError, reject } from "../platform/errors";
import {
  analysisErrorTraceHeaders,
  cancelAnalysisRunAgent,
  startAnalysisRunAgent,
  type AnalysisRunAgentInput,
} from "../analysis-agent-client";
import { errorResponse, readJson } from "./responses";
import { requireCsrf, requireSameOrigin } from "./security";

const historyEntry = z.strictObject({
  question: z.string().trim().min(1).max(8_000),
  answer: z.string().trim().min(1).max(32_768),
});
const selection = z.discriminatedUnion("mode", [
  z.strictObject({ mode: z.literal("fixed"), provider_id: uuid }),
  z.strictObject({ mode: z.literal("auto"), provider_id: z.null().optional() }),
]);
const startInput = z
  .strictObject({
    tenantId: uuid,
    workspaceId: uuid,
    requestId: uuid,
    question: z.string().trim().min(1).max(8_000),
    history: z.array(historyEntry).max(12),
    selection,
  })
  .superRefine((value, ctx) => {
    const visible = [
      value.question,
      ...value.history.flatMap((item) => [item.question, item.answer]),
    ];
    if (new TextEncoder().encode(visible.join("\n")).byteLength > 32 * 1024)
      ctx.addIssue({ code: "custom", message: "上下文内容过长。" });
  });
const cancelInput = z.strictObject({ tenantId: uuid, workspaceId: uuid });

export function isAnalysisRunRoute(route: string) {
  return route === "analysis/runs" || route.startsWith("analysis/runs/");
}

function requireNoQuery(url: URL) {
  if (url.searchParams.size) reject("validation_failed");
}

/** HTTP validation and Agent proxying stay outside the committed authorization transaction. */
export async function handleAnalysisRunRequest(request: Request, token: string): Promise<Response> {
  let authorizationFailed = false;
  let context: TrustedModelContext | undefined;
  let revision: string | undefined;
  let requestId: string | undefined;
  let runId: string | undefined;
  const audit = (action: string, error: unknown) =>
    recordFailure(action, error, context?.actor_user_id, { requestId });
  try {
    const url = new URL(request.url);
    const route = url.pathname.slice("/api/web/v1/".length);
    const segments = route.split("/");
    if (request.method !== "POST") reject("not_found");
    requireSameOrigin(request);
    requireCsrf(request, token);
    requireNoQuery(url);

    let scope: { tenantId: string; workspaceId: string };
    let command: AnalysisRunAgentInput | undefined;
    let cancel = false;
    if (route === "analysis/runs") {
      const value = startInput.parse(await readJson(request));
      requestId = value.requestId;
      scope = { tenantId: value.tenantId, workspaceId: value.workspaceId };
      command = {
        requestId: value.requestId,
        question: value.question,
        history: value.history,
        selection:
          value.selection.mode === "auto" ? { mode: "auto", provider_id: null } : value.selection,
      };
    } else if (
      segments.length === 4 &&
      segments[0] === "analysis" &&
      segments[1] === "runs" &&
      segments[3] === "cancel"
    ) {
      runId = uuid.parse(segments[2]);
      scope = cancelInput.parse(await readJson(request));
      cancel = true;
    } else {
      reject("not_found");
    }

    const authorized = await execute(token, "query", ({ models }) =>
      models.authorize(token, { kind: "tenant", purpose: "chat", ...scope }),
    ).catch((error: unknown) => {
      authorizationFailed = true;
      throw error;
    });
    context = authorized.value;
    revision = authorized.viewRevision;

    if (cancel) {
      return await cancelAnalysisRunAgent({
        runId: runId!,
        context,
        revision,
        signal: request.signal,
      });
    }
    return await startAnalysisRunAgent({
      input: command!,
      context,
      revision,
      signal: request.signal,
      onStreamFailure: (error) => audit("analysis.run.stream", error),
    });
  } catch (error) {
    const failure =
      error instanceof z.ZodError
        ? new ApplicationError("validation_failed", "请检查输入字段与范围。")
        : error;
    const response = errorResponse(failure);
    for (const [key, value] of Object.entries(analysisErrorTraceHeaders(failure)))
      response.headers.set(key, value);
    if (revision) response.headers.set("X-Compass-View-Revision", revision);
    if (!authorizationFailed) await audit(runId ? "analysis.run.cancel" : "analysis.run", failure);
    return response;
  }
}
