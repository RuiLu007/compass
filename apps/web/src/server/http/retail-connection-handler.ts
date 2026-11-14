import "server-only";
import { z } from "zod";
import { isModelContract } from "../../contracts/model-contract";
import { uuid } from "../../contracts/web";
import { requestModelAgent } from "../agent-api";
import { execute } from "../composition";
import { ApplicationError, reject } from "../platform/errors";
import { readJson } from "./responses";
import { requireCsrf, requireSameOrigin } from "./security";

const scope = z.strictObject({ tenantId: uuid, workspaceId: uuid });

export async function handleRetailConnection(
  request: Request,
  token: string,
  query: Record<string, string>,
  parts: string[],
): Promise<Response> {
  const method = request.method;
  if (method !== "GET" && method !== "PATCH" && method !== "POST") reject("not_found");
  const [resource, item, action] = parts;
  let path: string;
  let responseSchema: string;
  let inputSchema: string | undefined;
  if (resource === "retail-connection") {
    path = "/retail-connection";
    if (parts.length === 1 && method === "GET") responseSchema = "RetailConnectionStatus";
    else if (parts.length === 1 && method === "PATCH") {
      inputSchema = "RetailConnectionSave";
      responseSchema = "RetailConnectionView";
    } else if (parts.length === 2 && item === "test" && method === "POST") {
      path += "/test";
      responseSchema = "RetailConnectionTest";
    } else reject("not_found");
  } else if (parts.length === 1 && method === "GET") {
    path = "/connections";
    responseSchema = "DataConnectionList";
  } else if (parts.length === 1 && method === "POST") {
    path = "/connections";
    inputSchema = "DataConnectionSave";
    responseSchema = "DataConnectionView";
  } else if (parts.length === 2 && method === "PATCH") {
    path = `/connections/${uuid.parse(item)}`;
    inputSchema = "DataConnectionSave";
    responseSchema = "DataConnectionView";
  } else if (parts.length === 3 && action === "test" && method === "POST") {
    path = `/connections/${uuid.parse(item)}/test`;
    responseSchema = "RetailConnectionTest";
  } else reject("not_found");
  if (method !== "GET") {
    requireSameOrigin(request);
    requireCsrf(request, token);
  }
  const target = scope.parse(query);
  const authorized = await execute(token, "query", ({ models }) =>
    models.authorize(token, { kind: "tenant", purpose: "data_manage", ...target }),
  );
  const input = method === "GET" ? undefined : await readJson(request, 8192);
  if (inputSchema && !isModelContract(inputSchema, input)) reject("validation_failed");
  if (method !== "GET" && !inputSchema) z.strictObject({}).parse(input);
  try {
    return await requestModelAgent({
      path: `/v1/data${path}`,
      method,
      input,
      schema: responseSchema,
      context: authorized.value,
      revision: authorized.viewRevision,
      signal: request.signal,
      timeoutMs: 12_000,
    });
  } catch (error) {
    if (error instanceof ApplicationError) {
      if (error.code === "endpoint_not_allowed")
        reject("endpoint_not_allowed", "此主机和端口未在部署允许范围内。");
      if (["unavailable", "provider_protocol_error", "model_timeout"].includes(error.code))
        reject("unavailable", "数据连接服务暂时不可用，请稍后重试。");
      if (error.code === "not_found") reject("not_found", "当前工作区找不到这条连接。");
      if (error.code === "alias_conflict") reject("alias_conflict", "连接名称已存在。");
    }
    throw error;
  }
}
