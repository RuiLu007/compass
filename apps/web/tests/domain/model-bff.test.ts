import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({
  execute: vi.fn(),
  audit: vi.fn(),
  authorize: vi.fn(),
  fetch: vi.fn(),
  committed: false,
}));
vi.mock("server-only", () => ({}));
vi.mock("../../src/server/composition", () => ({
  execute: mocks.execute,
  recordFailure: mocks.audit,
}));
import { handleModelRequest } from "../../src/server/http/model-handler";
import { csrfToken } from "../../src/server/http/security";
import { ApplicationError } from "../../src/server/platform/errors";
const token = "a".repeat(43),
  origin = "http://127.0.0.1:8100";
const id = (n: number) => `99300000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const command = {
  call_id: id(1),
  input: { kind: "generate", messages: [{ role: "user", content: "hello" }] },
};
const scope = { tenantId: id(3), workspaceId: id(4) };
const providerTest = `platform/models/${id(2)}/tests`;
const start = { type: "start", call_id: id(1), provider_id: id(2), config_version: 1 };
const final = {
  type: "final",
  call_id: id(1),
  provider_id: id(2),
  config_version: 1,
  status: "completed",
  duration_ms: 2,
  requested_model: "deepseek-v4-pro",
};

function request(
  path: string,
  method = "GET",
  body?: unknown,
  headers: Record<string, string> = {},
  signal?: AbortSignal,
) {
  return handleModelRequest(
    new Request(`${origin}/api/web/v1/${path}`, {
      method,
      headers: {
        origin,
        "content-type": "application/json",
        "x-csrf-token": csrfToken(token),
        ...headers,
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal,
    }),
    token,
  );
}
function events(items: unknown[]) {
  return new Response(items.map((value) => `data: ${JSON.stringify(value)}\n\n`).join(""), {
    headers: { "content-type": "text/event-stream" },
  });
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("WEB_PUBLIC_ORIGIN", origin);
  vi.stubEnv("WEB_AUTH_ALLOW_LOCAL_HTTP", "true");
  vi.stubEnv("COMPASS_AGENT_SERVICE_TOKEN", "internal-test-service-token");
  vi.stubEnv("AGENT_API_URL", "http://agent.test");
  vi.stubGlobal("fetch", mocks.fetch);
  mocks.committed = false;
  mocks.audit.mockResolvedValue(undefined);
  mocks.authorize.mockImplementation(async (_token, value) => ({
    actor_user_id: id(5),
    scope:
      value.kind === "platform"
        ? { kind: "platform" }
        : { kind: "tenant", tenant_id: value.tenantId, workspace_id: value.workspaceId },
    purpose: value.purpose,
  }));
  mocks.execute.mockImplementation(async (_token, _mode, work) => {
    const value = await work({ models: { authorize: mocks.authorize } });
    mocks.committed = true;
    return { value, viewRevision: "a".repeat(64) };
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
describe("model BFF", () => {
  it.each(["chat", "module"])("preserves the %s list view and trusted purpose", async (view) => {
    mocks.fetch.mockImplementation(async (url, init) => {
      expect(new URL(url).searchParams.get("view")).toBe(view);
      const context = JSON.parse(
        Buffer.from(init.headers["X-Compass-Context"], "base64url").toString(),
      );
      expect(context.purpose).toBe(view === "chat" ? "chat" : "module_read");
      expect(context.scope).toEqual({
        kind: "tenant",
        tenant_id: scope.tenantId,
        workspace_id: scope.workspaceId,
      });
      return Response.json({ items: [] });
    });
    const query = new URLSearchParams({ ...scope, view });
    expect((await request(`models/available?${query}`)).status).toBe(200);
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
  });
  it("commits authorization before network I/O and replaces every browser-supplied internal header", async () => {
    mocks.fetch.mockImplementation(async (_url, init) => {
      expect(mocks.committed).toBe(true);
      expect(init.headers.Authorization).toBe("Bearer internal-test-service-token");
      expect(
        JSON.parse(Buffer.from(init.headers["X-Compass-Context"], "base64url").toString()),
      ).toEqual({ actor_user_id: id(5), scope: { kind: "platform" }, purpose: "manage" });
      expect(init.headers.Cookie).toBeUndefined();
      expect(init.headers.traceparent).toBeUndefined();
      expect(init.headers.tracestate).toBeUndefined();
      expect(init.headers.baggage).toBeUndefined();
      return Response.json({ items: [], total: 0, page: 1, page_size: 20 });
    });
    const result = await request("platform/models", "GET", undefined, {
      authorization: "Bearer browser",
      "X-Compass-Context": "forged",
      traceparent: "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
      tracestate: "forged=value",
      baggage: "private=secret",
    });
    expect(result.status).toBe(200);
    expect(result.headers.get("cache-control")).toContain("no-store");
    expect(result.headers.get("x-compass-view-revision")).toBe("a".repeat(64));
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
  });
  it("rejects trusted fields, invalid diagnostic input, duplicate queries, and invalid origin before forwarding", async () => {
    for (const body of [
      { ...command, context: {} },
      { ...command, input: { kind: "unknown" } },
    ])
      expect((await request(providerTest, "POST", body)).status).toBe(422);
    expect((await request("platform/models?page=1&page=2")).status).toBe(422);
    expect(
      (await request(providerTest, "POST", { ...command }, { origin: "https://foreign.test" }))
        .status,
    ).toBe(403);
    expect(mocks.fetch).not.toHaveBeenCalled();
    expect(mocks.audit).toHaveBeenCalledTimes(4);
  });
  it("retires the old product inference route before contacting Agent", async () => {
    const result = await request("models/calls", "POST", {
      ...scope,
      ...command,
      provider_id: id(2),
    });
    expect(result.status).toBe(404);
    expect(mocks.execute).not.toHaveBeenCalled();
    expect(mocks.fetch).not.toHaveBeenCalled();
  });
  it("keeps authentication failures to one existing failure audit", async () => {
    mocks.execute.mockRejectedValue(new ApplicationError("forbidden", "无权访问。"));
    expect((await request("platform/models")).status).toBe(403);
    expect(mocks.fetch).not.toHaveBeenCalled();
    expect(mocks.audit).not.toHaveBeenCalled();
  });
  it("preserves known safe conflicts and hides unknown upstream details", async () => {
    mocks.fetch.mockResolvedValueOnce(
      Response.json(
        {
          error: {
            code: "version_conflict",
            message: "private credential",
            requestId: "upstream",
            currentVersion: 7,
          },
        },
        { status: 409 },
      ),
    );
    const result = await request(`platform/models/${id(2)}`);
    expect(result.status).toBe(409);
    expect(await result.json()).toMatchObject({
      error: { code: "version_conflict", currentVersion: "7" },
    });
    mocks.fetch.mockResolvedValueOnce(
      Response.json(
        { error: { code: "unexpected_private_code", message: "secret", requestId: "id" } },
        { status: 500 },
      ),
    );
    const unknown = await request("platform/models");
    expect(unknown.status).toBe(503);
    expect(await unknown.text()).not.toContain("secret");
  });
  it("validates successful JSON and correlates call IDs without retrying", async () => {
    mocks.fetch.mockResolvedValueOnce(
      Response.json({ items: [], total: 0, page: 1, page_size: 20, api_key: "secret" }),
    );
    expect((await request("platform/models")).status).toBe(503);
    mocks.fetch.mockResolvedValueOnce(
      Response.json({ ...final, type: undefined, kind: "generate", call_id: id(9) }),
    );
    expect((await request(providerTest, "POST", { ...command })).status).toBe(503);
    expect(mocks.fetch).toHaveBeenCalledTimes(2);
  });
  it("relays a validated stream and refuses mismatched, missing, repeated, or post-final events", async () => {
    mocks.fetch.mockResolvedValueOnce(
      events([start, { type: "text_delta", call_id: id(1), text: "ok" }, final]),
    );
    const valid = await request(providerTest, "POST", {
      ...command,
      input: { ...command.input, stream: true },
    });
    expect(await valid.text()).toContain('"type":"final"');
    for (const items of [
      [start],
      [start, { ...final, call_id: id(9) }],
      [start, { ...final, provider_id: id(9) }],
      [start, { ...final, config_version: 2 }],
      [
        { ...start, provider_id: id(9) },
        { ...final, provider_id: id(9) },
      ],
      [start, start, final],
      [start, final, final],
      [final],
      [{ type: "text_delta", call_id: id(1), text: "early" }, final],
    ]) {
      mocks.fetch.mockResolvedValueOnce(events(items));
      const result = await request(providerTest, "POST", {
        ...command,
        input: { ...command.input, stream: true },
      });
      await expect(result.text()).rejects.toThrow();
    }
  });
  it("cancels the upstream request when the browser cancels its response", async () => {
    let upstreamSignal: AbortSignal | undefined;
    const canceled = vi.fn();
    mocks.fetch.mockImplementation(async (_url, init) => {
      upstreamSignal = init.signal;
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(start)}\n\n`));
          },
          cancel: canceled,
        }),
        { headers: { "content-type": "text/event-stream" } },
      );
    });
    const result = await request(providerTest, "POST", {
      ...command,
      input: { ...command.input, stream: true },
    });
    const reader = result.body!.getReader();
    await reader.read();
    await reader.cancel();
    expect(upstreamSignal?.aborted).toBe(true);
    expect(canceled).toHaveBeenCalledTimes(1);
  });
  it("propagates a request abort after headers to the upstream stream", async () => {
    const browser = new AbortController();
    let upstreamSignal: AbortSignal | undefined;
    mocks.fetch.mockImplementation(async (_url, init) => {
      upstreamSignal = init.signal;
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(start)}\n\n`));
            init.signal.addEventListener(
              "abort",
              () => controller.error(new Error("private cancellation")),
              { once: true },
            );
          },
        }),
        { headers: { "content-type": "text/event-stream" } },
      );
    });
    const response = await request(
      providerTest,
      "POST",
      { ...command, input: { ...command.input, stream: true } },
      {},
      browser.signal,
    );
    const reader = response.body!.getReader();
    await reader.read();
    browser.abort();
    await expect(reader.read()).rejects.toThrow("模型响应格式异常。");
    expect(upstreamSignal?.aborted).toBe(true);
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
  });
  it("rejects calls without a session or service credential before contacting Agent", async () => {
    const anonymous = await handleModelRequest(
      new Request(`${origin}/api/web/v1/${providerTest}`, {
        method: "POST",
        headers: { origin, "content-type": "application/json" },
        body: JSON.stringify({ ...command }),
      }),
      "",
    );
    expect(anonymous.status).toBe(401);
    expect(mocks.execute).not.toHaveBeenCalled();
    vi.stubEnv("COMPASS_AGENT_SERVICE_TOKEN", "");
    const unconfigured = await request(providerTest, "POST", { ...command });
    expect(unconfigured.status).toBe(503);
    expect(await unconfigured.json()).toMatchObject({ error: { code: "unavailable" } });
    expect(mocks.fetch).not.toHaveBeenCalled();
  });
  it("accepts the bounded continuation request size while keeping ordinary CRUD limits", async () => {
    mocks.fetch.mockResolvedValue(Response.json({ ...final, type: undefined, kind: "generate" }));
    const body = {
      ...command,
      input: { ...command.input, continuation_token: "x".repeat(70_000) },
    };
    expect((await request(providerTest, "POST", body)).status).toBe(200);
    expect((await request("platform/models", "POST", { junk: "x".repeat(70_000) })).status).toBe(
      422,
    );
  });
  it("maps the model deadline to a stable timeout without issuing a second call", async () => {
    vi.useFakeTimers();
    try {
      mocks.fetch.mockImplementation(
        (_url, init) =>
          new Promise((_resolve, reject) => {
            init.signal.addEventListener("abort", () => reject(new Error("private timeout")), {
              once: true,
            });
          }),
      );
      const pending = request(providerTest, "POST", { ...command });
      await vi.advanceTimersByTimeAsync(190_000);
      const result = await pending;
      expect(result.status).toBe(503);
      expect(await result.json()).toMatchObject({ error: { code: "model_timeout" } });
      expect(mocks.fetch).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

it("exposes local observability diagnostics and only the fixed safe project entry", async () => {
  vi.stubEnv("COMPASS_LANGFUSE_PROJECT_URL", "http://127.0.0.1:8104/project/local");
  const status = {
    instance_id: "agent-1",
    environment: "local",
    enabled: true,
    configured: true,
    sample_ratio: 1,
    queue_size: 0,
    queue_capacity: 512,
    dropped: 0,
    export_failures: 0,
    instrumentation_errors: 0,
    last_export: "never",
  };
  mocks.fetch.mockImplementation(async () => Response.json(status));
  const response = await request("platform/models/observability");
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({
    ...status,
    project_url: "http://127.0.0.1:8104/project/local",
  });
  expect(response.headers.get("cache-control")).toContain("no-store");
  const context = JSON.parse(
    Buffer.from(mocks.fetch.mock.calls[0][1].headers["X-Compass-Context"], "base64url").toString(),
  );
  expect(context.scope.kind).toBe("platform");
  expect(context.purpose).toBe("manage");
  vi.stubEnv("COMPASS_LANGFUSE_PROJECT_URL", "https://name:secret@example.com/project/private");
  expect((await (await request("platform/models/observability")).json()).project_url).toBeNull();
  mocks.fetch.mockResolvedValue(Response.json({ ...status, secret_key: "private" }));
  expect((await request("platform/models/observability")).status).toBe(503);
});

it("preserves safe trace correlation on upstream failures and discards malformed headers", async () => {
  for (const trace of ["0123456789abcdef0123456789abcdef", "forged secret value"]) {
    mocks.fetch.mockResolvedValue(
      Response.json(
        {
          error: {
            code: "model_timeout",
            message: "private upstream detail",
            requestId: "fixture",
          },
        },
        { status: 503, headers: { "X-Compass-Trace-Id": trace, "X-Compass-Trace-Sampled": "0" } },
      ),
    );
    const response = await request(providerTest, "POST", { ...command });
    expect(response.status).toBe(503);
    expect(response.headers.get("X-Compass-Trace-Id")).toBe(trace.includes(" ") ? null : trace);
    expect(await response.text()).not.toContain("private upstream detail");
  }
});

it("rejects unbounded observability queries and denied platform access before network I/O", async () => {
  expect(
    (await request("platform/models/observability/summary?provider_ids=x&window=all")).status,
  ).toBe(422);
  expect((await request("platform/models/observability?query=arbitrary")).status).toBe(422);
  mocks.authorize.mockRejectedValue(new ApplicationError("forbidden", "无权访问。"));
  expect((await request("platform/models/observability")).status).toBe(403);
  expect(mocks.fetch).not.toHaveBeenCalled();
});

it("retains a received trace when the nonstream body stalls until the BFF deadline", async () => {
  vi.useFakeTimers();
  try {
    const trace = "fedcba9876543210fedcba9876543210";
    mocks.fetch.mockImplementation(
      async (_url, init) =>
        new Response(
          new ReadableStream({
            start(controller) {
              init.signal.addEventListener(
                "abort",
                () => controller.error(new Error("private timeout")),
                { once: true },
              );
            },
          }),
          {
            headers: {
              "content-type": "application/json",
              "X-Compass-Trace-Id": trace,
              "X-Compass-Trace-Sampled": "1",
            },
          },
        ),
    );
    const pending = request(providerTest, "POST", { ...command });
    await vi.advanceTimersByTimeAsync(190_000);
    const response = await pending;
    expect(response.headers.get("X-Compass-Trace-Id")).toBe(trace);
    expect(await response.json()).toMatchObject({ error: { code: "model_timeout" } });
  } finally {
    vi.useRealTimers();
  }
});

it("authorizes the simulation as platform manage and validates its SSE protocol", async () => {
  mocks.fetch.mockImplementation(async (_url, init) => {
    const context = JSON.parse(
      Buffer.from(init.headers["X-Compass-Context"], "base64url").toString(),
    );
    expect(context.scope).toEqual({ kind: "platform" });
    expect(context.purpose).toBe("manage");
    return events([
      { type: "start", call_id: id(1) },
      {
        type: "final",
        call_id: id(1),
        scenario: "success",
        simulation: true,
        passed: true,
        duration_ms: 1,
        calls: [],
        capacity: {
          capacity: 2,
          max_inflight: 5,
          active: 0,
          inflight: 0,
          quarantined: 0,
          available: 2,
          peak_active: 1,
          peak_inflight: 1,
        },
      },
    ]);
  });
  const response = await request("platform/models/reliability/runs", "POST", {
    call_id: id(1),
    scenario: "success",
  });
  expect(response.status).toBe(200);
  expect(await response.text()).toContain('"simulation":true');
});

it("rejects arbitrary simulation destinations before proxying", async () => {
  const response = await request("platform/models/reliability/runs", "POST", {
    call_id: id(1),
    scenario: "success",
    base_url: "https://untrusted.invalid",
  });
  expect(response.status).toBe(422);
  expect(mocks.fetch).not.toHaveBeenCalled();
});
