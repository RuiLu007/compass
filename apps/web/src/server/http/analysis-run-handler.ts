import "server-only";
import { z } from "zod";
import { RUN_STREAM_LIMITS } from "../../contracts/analysis-run";
import { uuid } from "../../contracts/web";
import { execute, recordFailure } from "../composition";
import type { TrustedModelContext } from "../modules/models/public";
import { ApplicationError, reject } from "../platform/errors";
import {
  analysisErrorTraceHeaders,
  analysisArtifactAgent,
  cancelAnalysisRunAgent,
  startAnalysisRunAgent,
  workflowAssetAgent,
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
const period = z.strictObject({
  start: z.iso.date(),
  end: z.iso.date(),
});
const assetRef = z.strictObject({
  assetId: uuid,
  definitionVersion: z.literal(1),
  baseline: period,
  current: period,
});
const patchInput = z.strictObject({
  name: z.string().trim().min(1).max(80),
  status: z.enum(["active", "disabled"]),
  metadataRevision: z.number().int().positive().lt(2147483647),
});
const confirmInput = patchInput.pick({ name: true });
const deleteInput = patchInput.pick({ metadataRevision: true });
const assetQuery = z.strictObject({
  tenantId: uuid,
  workspaceId: uuid,
  cursor: z.string().max(100).optional(),
});
const startInput = z
  .strictObject({
    tenantId: uuid,
    workspaceId: uuid,
    requestId: uuid,
    question: z.string().trim().min(1).max(8_000),
    history: z.array(historyEntry).max(12),
    selection,
    workflowAssetRef: assetRef.optional(),
    constraints: z
      .strictObject({
        plan: z.literal("required").optional(),
        maxSteps: z.number().int().min(1).max(RUN_STREAM_LIMITS.steps).optional(),
        timeoutSeconds: z.number().int().min(1).max(RUN_STREAM_LIMITS.timeoutSeconds).optional(),
      })
      .optional(),
  })
  .superRefine((value, ctx) => {
    if (value.constraints && value.workflowAssetRef)
      ctx.addIssue({ code: "custom", message: "运行约束用于直接分析任务。" });
    const visible = [
      value.question,
      ...value.history.flatMap((item) => [item.question, item.answer]),
    ];
    if (new TextEncoder().encode(visible.join("\n")).byteLength > 32 * 1024)
      ctx.addIssue({ code: "custom", message: "上下文内容过长。" });
  });
const cancelInput = z.strictObject({ tenantId: uuid, workspaceId: uuid });

export function isAnalysisRunRoute(route: string) {
  return /^analysis\/(?:runs|workflows)(?:\/|$)/.test(route);
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
    if (
      segments.length === 5 &&
      segments[1] === "runs" &&
      segments[3] === "artifacts" &&
      request.method === "GET"
    ) {
      runId = uuid.parse(segments[2]);
      const artifactId = uuid.parse(segments[4]);
      if (new Set(url.searchParams.keys()).size !== url.searchParams.size)
        reject("validation_failed");
      const scope = cancelInput.parse(Object.fromEntries(url.searchParams));
      const authorized = await execute(token, "query", ({ models }) =>
        models.authorize(token, { kind: "tenant", purpose: "chat", ...scope }),
      ).catch((error: unknown) => {
        authorizationFailed = true;
        throw error;
      });
      context = authorized.value;
      revision = authorized.viewRevision;
      return await analysisArtifactAgent({
        runId,
        artifactId,
        context,
        revision,
        signal: request.signal,
      });
    }
    const assetRequest = segments[1] === "workflows";
    if (assetRequest) {
      const draft = segments[2] === "drafts";
      const confirming = draft && request.method === "POST";
      const deleting = !draft && request.method === "DELETE";
      if (
        draft
          ? !(
              (request.method === "GET" && segments.length === 4) ||
              (confirming && segments.length === 5 && segments[4] === "confirm")
            )
          : segments.length > 3 ||
            (request.method !== "GET" &&
              !((request.method === "PATCH" || deleting) && segments.length === 3))
      )
        reject("not_found");
      const draftId = draft ? uuid.parse(segments[3]) : undefined;
      const assetId = !draft && segments.length === 3 ? uuid.parse(segments[2]) : undefined;
      if (request.method !== "GET") {
        requireSameOrigin(request);
        requireCsrf(request, token);
      }
      const { cursor, ...scope } = assetQuery.parse(Object.fromEntries(url.searchParams));
      if ((assetId || draftId) && cursor) reject("validation_failed");
      const patch =
        request.method === "PATCH" ? patchInput.parse(await readJson(request)) : undefined;
      const confirm = confirming ? confirmInput.parse(await readJson(request)) : undefined;
      const remove = deleting ? deleteInput.parse(await readJson(request)) : undefined;
      const authorized = await execute(token, "query", ({ models }) =>
        models.authorize(token, { kind: "tenant", purpose: "chat", ...scope }),
      ).catch((error: unknown) => {
        authorizationFailed = true;
        throw error;
      });
      context = authorized.value;
      revision = authorized.viewRevision;
      return await workflowAssetAgent({
        context,
        revision,
        signal: request.signal,
        assetId,
        draftId,
        cursor,
        patch,
        confirm,
        remove,
      });
    }
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
        ...(value.workflowAssetRef ? { workflowAssetRef: value.workflowAssetRef } : {}),
        ...(value.constraints ? { constraints: value.constraints } : {}),
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
