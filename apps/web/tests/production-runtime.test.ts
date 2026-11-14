import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { agentResponse, reduceAgentResponse } from "../src/contracts/agent-response";
import { completedAnalysisHistory } from "../src/client/analysis-run";
import { requestDurableControl, terminalRun, turnFromRun } from "../src/client/durable-analysis";
import { ApiError, type DocumentSession } from "../src/client/request-scope";
import {
  allowedDetail,
  answerText,
  RuntimeProtocol,
  runtimeIds,
} from "./e2e/production-runtime.fixture";

const mocks = vi.hoisted(() => ({ execute: vi.fn(), authorize: vi.fn(), fetch: vi.fn() }));
vi.mock("server-only", () => ({}));
vi.mock("../src/server/composition", () => ({ execute: mocks.execute }));
import {
  handleDurableAnalysis,
  isDurableAnalysisRoute,
} from "../src/server/http/durable-analysis-handler";
import { csrfToken } from "../src/server/http/security";
import { ApplicationError } from "../src/server/platform/errors";
import { createModelAuthorizer } from "../src/server/modules/models/public";
import type { AccessStore, CurrentAccess } from "../src/server/modules/access/public";
import type { IdentityStore } from "../src/server/modules/identity/public";

const scope = {
  tenantId: "98400000-0000-4000-8000-000000000010",
  workspaceId: "98400000-0000-4000-8000-000000000020",
};
const token = "x".repeat(43),
  origin = "https://compass.invalid";

describe("scheduled public Run projection", () => {
  it.each(["created", "paused", "waiting"] as const)(
    "keeps queued %s Runs active without a final answer",
    (status) => {
      const protocol = new RuntimeProtocol();
      protocol.transition(status, "queued");
      const run = reduceAgentResponse(null, protocol.snapshot()).runs[runtimeIds.run];
      expect(run.run).toMatchObject({ status, reasonCode: "queued" });
      expect(terminalRun(run)).toBe(false);
      expect(turnFromRun(run).terminal).toBeUndefined();
      expect(completedAnalysisHistory([turnFromRun(run)])).toEqual([]);
    },
  );

  it("requires a completed Run and a completed assistant message before publishing success", () => {
    const protocol = new RuntimeProtocol();
    protocol.begin();
    protocol.complete();
    const snapshot = protocol.snapshot();
    for (const status of ["streaming", "incomplete"] as const) {
      const input = structuredClone(snapshot);
      const packet = input.packets.find(
        (p) => p.event.type === "message.upsert" && p.event.payload.role === "assistant",
      )!;
      if (packet.event.type !== "message.upsert") throw new Error("Missing assistant fixture");
      packet.event.payload.status = status;
      const run = reduceAgentResponse(null, input).runs[runtimeIds.run];
      expect(turnFromRun(run).terminal?.status).not.toBe("completed");
      expect(completedAnalysisHistory([turnFromRun(run)])).toEqual([]);
    }
  });

  it("withholds an empty successful answer when the assistant message is absent", () => {
    const protocol = new RuntimeProtocol();
    protocol.complete();
    protocol.messages.delete(runtimeIds.assistant);
    const run = reduceAgentResponse(null, protocol.snapshot()).runs[runtimeIds.run];
    expect(turnFromRun(run).terminal?.status).not.toBe("completed");
    expect(completedAnalysisHistory([turnFromRun(run)])).toEqual([]);
  });

  it("applies the full answer and completed state together and deduplicates their frame", () => {
    const protocol = new RuntimeProtocol();
    protocol.begin();
    const initial = reduceAgentResponse(null, protocol.snapshot());
    expect(turnFromRun(initial.runs[runtimeIds.run]).terminal).toBeUndefined();
    protocol.complete();
    const delta = protocol.delta(initial.runs[runtimeIds.run].cursor)!;
    const completed = reduceAgentResponse(initial, delta);
    expect(turnFromRun(completed.runs[runtimeIds.run]).terminal).toMatchObject({
      status: "completed",
      finalAnswer: answerText,
    });
    expect(completedAnalysisHistory([turnFromRun(completed.runs[runtimeIds.run])])).toEqual([
      { question: protocol.question, answer: answerText },
    ]);
    expect(reduceAgentResponse(completed, delta)).toEqual(completed);
    expect(reduceAgentResponse(completed, protocol.snapshot())).toEqual(completed);
  });

  it("restores a completed workflow with child tool events and an execution tree", () => {
    const protocol = new RuntimeProtocol();
    protocol.begin();
    const stepId = "98400000-0000-4000-8000-000000000007";
    const payload = { stepId, toolCallId: "child-query", toolIndex: 1 };
    protocol.append([
      {
        type: "view.upsert",
        payload: {
          objectId: "timeline:child-query",
          revision: 1,
          kind: "timeline",
          value: {
            ordinal: 1,
            event: {
              type: "tool.started",
              payload: { ...payload, toolKind: "database_query", params: null },
            },
          },
        },
      },
      {
        type: "view.upsert",
        payload: {
          objectId: "timeline:child-query-finished",
          revision: 1,
          kind: "timeline",
          value: {
            ordinal: 2,
            event: { type: "tool.finished", payload: { ...payload, status: "ok", rowCount: 2 } },
          },
        },
      },
    ]);
    protocol.complete();
    const run = reduceAgentResponse(null, protocol.snapshot()).runs[runtimeIds.run];
    expect(turnFromRun(run)).toMatchObject({
      execution: [protocol.node().value],
      terminal: { status: "completed", finalAnswer: answerText },
    });
  });

  it.each(["failed", "canceled"] as const)(
    "keeps %s partial text out of successful conversation history",
    (status) => {
      const protocol = new RuntimeProtocol();
      protocol.begin();
      protocol.complete(status);
      const run = reduceAgentResponse(null, protocol.snapshot()).runs[runtimeIds.run];
      expect(run.messages[runtimeIds.assistant].status).toBe("incomplete");
      expect(turnFromRun(run).terminal?.status).toBe(status);
      expect(completedAnalysisHistory([turnFromRun(run)])).toEqual([]);
    },
  );

  it("replaces formerly visible previews with the same-identity safe snapshot", () => {
    const protocol = new RuntimeProtocol();
    protocol.begin();
    const full = protocol.snapshot();
    const original = reduceAgentResponse(null, full);
    const identity = original.runs[runtimeIds.run].objects[runtimeIds.node];
    expect(JSON.stringify(original)).toContain(allowedDetail);
    protocol.restrictDetails();
    const safe = protocol.snapshot();
    const filtered = reduceAgentResponse(original, safe);
    const object = filtered.runs[runtimeIds.run].objects[runtimeIds.node];
    expect(object.objectId).toBe(identity.objectId);
    expect(object.revision).toBe(identity.revision);
    expect(filtered.runs[runtimeIds.run].cursor).toBe(original.runs[runtimeIds.run].cursor);
    expect(JSON.stringify(filtered)).not.toContain(allowedDetail);
    expect(reduceAgentResponse(filtered, protocol.delta("0-0")!)).toEqual(filtered);
  });

  it("rejects private Worker and checkpoint metadata in public Run payloads", () => {
    for (const privateField of ["workerId", "bootId", "executionEpoch", "checkpointCiphertext"]) {
      const snapshot = new RuntimeProtocol().snapshot();
      const run = snapshot.packets.find((packet) => packet.event.type === "run.upsert")!;
      Object.assign(run.event.payload, { [privateField]: "PRIVATE_RUNTIME_FIELD_984" });
      expect(() => agentResponse(snapshot)).toThrow();
    }
  });

  it("surfaces 429 on Resume without an automatic second control or new request ID", async () => {
    const error = new ApiError(429, "当前任务已满，请稍后重试。", "rate_limited");
    const request = vi.fn().mockRejectedValue(error);
    const body = { ...scope, requestId: runtimeIds.request, expectedVersion: 2 };
    await expect(
      requestDurableControl(
        { request } as unknown as DocumentSession,
        runtimeIds.session,
        runtimeIds.run,
        "resume",
        body,
      ),
    ).rejects.toBe(error);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0][2]).toEqual(body);
  });
});

describe("scheduled Run BFF contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("WEB_PUBLIC_ORIGIN", origin);
    vi.stubEnv("WEB_AUTH_ALLOW_LOCAL_HTTP", "true");
    vi.stubEnv("AGENT_API_URL", "http://agent.invalid");
    vi.stubEnv("COMPASS_AGENT_SERVICE_TOKEN", "internal-worker-test-token");
    vi.stubGlobal("fetch", mocks.fetch);
    mocks.authorize.mockResolvedValue({
      actor_user_id: runtimeIds.user,
      scope: { kind: "tenant", tenant_id: scope.tenantId, workspace_id: scope.workspaceId },
      purpose: "chat",
    });
    mocks.execute.mockImplementation(async (_token, _mode, work) => ({
      value: await work({ models: { authorize: mocks.authorize } }),
      viewRevision: "a".repeat(64),
    }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  function request(path: string, body?: unknown) {
    return handleDurableAnalysis(
      new Request(`${origin}/api/web/v1/analysis/${path}`, {
        method: body ? "POST" : "GET",
        headers: {
          origin,
          "content-type": "application/json",
          "x-csrf-token": csrfToken(token),
          "X-Compass-Context": "forged-browser-context",
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      }),
      token,
    );
  }

  it("returns safe 429 backpressure without exposing upstream text or retrying admission", async () => {
    mocks.fetch.mockResolvedValue(
      Response.json(
        {
          error: {
            code: "rate_limited",
            message: "PRIVATE_TENANT_CAPACITY_984",
            requestId: runtimeIds.request,
            currentVersion: null,
          },
        },
        { status: 429 },
      ),
    );
    const response = await request("sessions/submit", {
      ...scope,
      sessionId: null,
      requestId: runtimeIds.request,
      question: "保留我的问题",
      selection: { mode: "auto", provider_id: null },
    });
    expect(response.status).toBe(429);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(response.headers.get("retry-after")).toBe("1");
    const body = await response.json();
    expect(body.error.code).toBe("rate_limited");
    expect(body.error.message).toContain("稍后重试");
    expect(JSON.stringify(body)).not.toContain("PRIVATE_TENANT_CAPACITY_984");
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
  });

  it("keeps queued projections and safe object identities intact through current authorization", async () => {
    const protocol = new RuntimeProtocol();
    protocol.begin();
    protocol.transition("paused", "queued");
    protocol.restrictDetails();
    const payload = protocol.snapshot();
    mocks.fetch.mockResolvedValue(Response.json(payload));
    const response = await request(
      `sessions/${runtimeIds.session}/messages?${new URLSearchParams(scope)}`,
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(payload);
    const options = mocks.fetch.mock.calls[0][1];
    const trusted = JSON.parse(
      Buffer.from(options.headers["X-Compass-Context"], "base64url").toString(),
    );
    expect(trusted.scope).toEqual({
      kind: "tenant",
      tenant_id: scope.tenantId,
      workspace_id: scope.workspaceId,
    });
    expect(JSON.stringify(trusted)).not.toContain("forged-browser-context");
  });

  it("stops unauthorized history reads before contacting Agent", async () => {
    mocks.authorize.mockRejectedValue(new ApplicationError("forbidden", "无权访问。"));
    const response = await request(
      `sessions/${runtimeIds.session}/messages?${new URLSearchParams(scope)}`,
    );
    expect(response.status).toBe(403);
    expect(mocks.fetch).not.toHaveBeenCalled();
    expect(JSON.stringify(await response.json())).not.toContain(runtimeIds.run);
  });

  const readOnlyRoutes = [
    {
      path: `runs/${runtimeIds.run}/timeline`,
      authorization: { kind: "platform", purpose: "runtime_diagnostics" },
      trustedScope: { kind: "platform" },
      payload: { runId: runtimeIds.run, entries: [] },
    },
    {
      path: "runtime/usage",
      authorization: { kind: "tenant", purpose: "runtime_usage", ...scope },
      trustedScope: {
        kind: "tenant",
        tenant_id: scope.tenantId,
        workspace_id: scope.workspaceId,
      },
      payload: {
        tenantId: scope.tenantId,
        limits: { active: 1, backlog: 4 },
        jobs: { ready: 1, leased: 0, reclaiming: 0, parked: 1, done: 0 },
        backlog: 2,
        occupied: 0,
        settledTokens: 20,
        settledCostMicrousd: null,
        unknownAttempts: 1,
        reservedTokens: 50,
        reservedCostMicrousd: 100,
      },
    },
  ];

  it.each(readOnlyRoutes)(
    "authorizes $path with its explicit purpose and trusted scope",
    async (entry) => {
      mocks.authorize.mockResolvedValue({
        actor_user_id: runtimeIds.user,
        scope: entry.trustedScope,
        purpose: entry.authorization.purpose,
      });
      mocks.fetch.mockResolvedValue(Response.json(entry.payload));
      const route = `analysis/${entry.path}`;
      expect(isDurableAnalysisRoute(route, new Request(`${origin}/api/web/v1/${route}`))).toBe(
        true,
      );
      const response = await request(`${entry.path}?${new URLSearchParams(scope)}`);
      expect(response.status).toBe(200);
      expect(response.headers.get("cache-control")).toContain("no-store");
      expect(await response.json()).toEqual(entry.payload);
      expect(mocks.authorize).toHaveBeenCalledExactlyOnceWith(token, entry.authorization);
      expect(mocks.fetch).toHaveBeenCalledTimes(1);
      const [target, options] = mocks.fetch.mock.calls[0];
      expect(new URL(target).pathname).toBe(`/v1/${route}`);
      expect(options.method).toBe("GET");
      expect(options.body).toBeUndefined();
      expect(
        JSON.parse(Buffer.from(options.headers["X-Compass-Context"], "base64url").toString()),
      ).toEqual({
        actor_user_id: runtimeIds.user,
        scope: entry.trustedScope,
        purpose: entry.authorization.purpose,
      });
    },
  );

  it.each(readOnlyRoutes)("rejects unauthorized $path before any upstream call", async (entry) => {
    mocks.authorize.mockRejectedValue(new ApplicationError("forbidden", "无权访问。"));
    const response = await request(`${entry.path}?${new URLSearchParams(scope)}`);
    expect(response.status).toBe(403);
    expect(mocks.authorize).toHaveBeenCalledExactlyOnceWith(token, entry.authorization);
    expect(mocks.fetch).not.toHaveBeenCalled();
    expect(JSON.stringify(await response.json())).not.toContain(runtimeIds.run);
  });

  it.each(readOnlyRoutes)("keeps $path read only", async (entry) => {
    const response = await request(entry.path, scope);
    expect(response.status).toBe(404);
    expect(mocks.authorize).not.toHaveBeenCalled();
    expect(mocks.fetch).not.toHaveBeenCalled();
  });
});

describe("runtime diagnostic and tenant usage authorization", () => {
  function authorizer(
    options: {
      platform?: boolean;
      role?: "admin" | "member";
      memberId?: string | null;
      permissions?: CurrentAccess["tenantPermissions"];
      workspaceStatus?: string;
      workspaceTenant?: string;
    } = {},
  ) {
    const current = vi.fn().mockResolvedValue({
      tenantId: scope.tenantId,
      memberId: options.memberId === undefined ? runtimeIds.user : options.memberId,
      role: options.role ?? "member",
      authzVersion: "1",
      tenantPermissions: options.permissions ?? [],
      workspacePermissions: new Map(),
    });
    const workspace = vi.fn().mockResolvedValue({
      tenantId: options.workspaceTenant ?? scope.tenantId,
      status: options.workspaceStatus ?? "active",
    });
    return {
      current,
      workspace,
      authorize: createModelAuthorizer({
        identity: {
          requireActor: vi.fn().mockResolvedValue({
            userId: runtimeIds.user,
            isPlatformAdmin: options.platform ?? false,
          }),
        } as unknown as IdentityStore,
        access: { current, workspace } as unknown as AccessStore,
      }),
    };
  }
  const usage = { kind: "tenant", purpose: "runtime_usage", ...scope } as const;
  const diagnostics = { kind: "platform", purpose: "runtime_diagnostics" } as const;

  it("rejects tenant usage for an ordinary member even with run-module access", async () => {
    await expect(
      authorizer({ permissions: ["module.admin.runs"] }).authorize(token, usage),
    ).rejects.toMatchObject({ code: "forbidden" });
  });

  it("requires effective tenant membership for a platform administrator reading tenant usage", async () => {
    await expect(
      authorizer({ platform: true, role: "admin", memberId: null }).authorize(token, usage),
    ).rejects.toMatchObject({ code: "forbidden" });
  });

  it("allows a tenant administrator's usage while rejecting platform diagnostics", async () => {
    const tenantAdmin = authorizer({ role: "admin" });
    await expect(tenantAdmin.authorize(token, usage)).resolves.toEqual({
      actor_user_id: runtimeIds.user,
      scope: { kind: "tenant", tenant_id: scope.tenantId, workspace_id: scope.workspaceId },
      purpose: "runtime_usage",
    });
    await expect(tenantAdmin.authorize(token, diagnostics)).rejects.toMatchObject({
      code: "forbidden",
    });
  });

  it("allows platform diagnostics without tenant membership or a workspace lookup", async () => {
    const platformAdmin = authorizer({ platform: true, memberId: null });
    await expect(platformAdmin.authorize(token, diagnostics)).resolves.toEqual({
      actor_user_id: runtimeIds.user,
      scope: { kind: "platform" },
      purpose: "runtime_diagnostics",
    });
    expect(platformAdmin.current).not.toHaveBeenCalled();
    expect(platformAdmin.workspace).not.toHaveBeenCalled();
  });

  it.each([{ workspaceStatus: "archived" }, { workspaceTenant: runtimeIds.run }])(
    "requires an active workspace in the authorized tenant: %j",
    async (options) => {
      await expect(
        authorizer({ role: "admin", ...options }).authorize(token, usage),
      ).rejects.toMatchObject({ code: "forbidden" });
    },
  );
});
