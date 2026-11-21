import "server-only";
import { z } from "zod";
import { isModelContract } from "../../contracts/model-contract";
import { traceHeaders } from "../../contracts/trace-context";
import { uuid } from "../../contracts/web";
import { boundedJson, fetchAgent } from "../agent-transport";
import { execute } from "../composition";
import type { ModelScope } from "../modules/models/public";
import { ApplicationError, reject } from "../platform/errors";
import { privateHeaders, readJson } from "./responses";
import { requireCsrf, requireSameOrigin } from "./security";

const empty = z.strictObject({});
const path = z.string().min(1).max(512);
const version = z.coerce.number().int().min(1);
const cursor = z.string().max(512).optional();
const limit = z.coerce.number().int().min(1).max(200).optional();
const paged = z.strictObject({ cursor, limit });
const scope = z.strictObject({ tenantId: uuid, workspaceId: uuid });
const reads = {
  tree: [
    paged.extend({ prefix: z.string().max(512).optional(), q: z.string().max(512).optional() }),
    "PromptTree",
  ],
  file: [z.strictObject({ path, version: version.optional() }), "PromptFileDetail"],
  history: [paged.extend({ path }), "PromptHistory"],
  snapshots: [paged, "PromptSnapshots"],
  snapshot: [paged.extend({ snapshot_no: version }), "PromptSnapshot"],
  commands: [z.strictObject({ request_id: uuid }), "PromptReceipt"],
} as const;
const writes = {
  "file-draft": ["PromptDraft", "PromptReceipt"],
  "draft-from-version": ["PromptDraftFromVersion", "PromptReceipt"],
  "render-preview": ["PromptRenderInput", "PromptRendered"],
  "import-preview": ["PromptImportPreviewInput", "PromptImportPreview"],
  import: ["PromptImport", "PromptReceipt"],
  "release-preview": ["PromptReleaseTarget", "PromptReleasePreview"],
  releases: ["PromptRelease", "PromptReceipt"],
} as const;
const analysis = "analysis/prompt-starters";
const testing = "platform/models/prompt-starters";
const prefix = "platform/promptops/";
export function isPromptOpsRoute(route: string) {
  return route.startsWith(prefix) || route === analysis || route === testing;
}

export async function handlePromptOps(
  request: Request,
  token: string,
  route: string,
  query: Record<string, string>,
): Promise<Response> {
  if (!token) reject("unauthenticated");
  let access: ModelScope = { kind: "platform", purpose: "promptops_manage" };
  let suffix = "",
    schema = "",
    inputSchema: string | undefined;
  const method = request.method;
  let target = "/v1/prompt-starters";
  if (route === analysis || route === testing) {
    if (method !== "GET") reject("not_found");
    if (route === analysis) access = { kind: "tenant", purpose: "chat", ...scope.parse(query) };
    else {
      empty.parse(query);
      access = { kind: "platform", purpose: "test" };
    }
    schema = "PromptStarterCards";
  } else {
    suffix = route.slice(prefix.length);
    if (method === "GET" && Object.hasOwn(reads, suffix)) {
      const [validator, out] = reads[suffix as keyof typeof reads];
      validator.parse(query);
      schema = out;
    } else if (method === "POST" && Object.hasOwn(writes, suffix)) {
      requireSameOrigin(request);
      requireCsrf(request, token);
      empty.parse(query);
      [inputSchema, schema] = writes[suffix as keyof typeof writes];
    } else reject("not_found");
    const params = new URLSearchParams(query).toString();
    target = "/v1/promptops/" + suffix + (params ? "?" + params : "");
  }
  const granted = await execute(token, "query", ({ models }) => models.authorize(token, access));
  let input: unknown;
  if (inputSchema) {
    input = await readJson(request, 2 * 1024 * 1024, true);
    if (!isModelContract(inputSchema, input)) reject("validation_failed");
  }
  const unavailable = () =>
    new ApplicationError("unavailable", "提示词服务暂时不可用，请稍后重试。");
  const credential = process.env.COMPASS_AGENT_SERVICE_TOKEN;
  if (!credential) throw unavailable();
  try {
    const response = await fetchAgent(
      { path: target, context: granted.value, method, input },
      credential,
      AbortSignal.any([request.signal, AbortSignal.timeout(15000)]),
    );
    if (!response.headers.get("content-type")?.includes("application/json")) {
      await response.body?.cancel();
      throw unavailable();
    }
    const value = await boundedJson(response, unavailable);
    const headers = {
      ...privateHeaders,
      ...traceHeaders(response.headers),
      "X-Compass-View-Revision": granted.viewRevision,
    };
    if (!response.ok) {
      if (
        ![403, 404, 409, 413, 422].includes(response.status) ||
        !isModelContract("ModelErrorResponse", value)
      )
        throw unavailable();
      return Response.json(value, { status: response.status, headers });
    }
    if (!isModelContract(schema, value)) throw unavailable();
    return Response.json(value, { headers });
  } catch {
    throw unavailable();
  }
}
