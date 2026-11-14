import { describe, expect, it, vi } from "vitest";
import { requestDurableControl } from "../src/client/durable-analysis";
import { ApiError, type DocumentSession } from "../src/client/request-scope";

const sessionId = "10000000-0000-4000-8000-000000000001";
const runId = "20000000-0000-4000-8000-000000000001";
const body = {
  tenantId: "30000000-0000-4000-8000-000000000001",
  workspaceId: "40000000-0000-4000-8000-000000000001",
  requestId: "50000000-0000-4000-8000-000000000001",
  expectedVersion: 2,
};

describe("durable controls under projection lag", () => {
  it.each(["pause", "cancel"])("uses the committed conflict version for %s", async (kind) => {
    const request = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, "状态已更新", "version_conflict", 6))
      .mockResolvedValueOnce({ status: kind === "pause" ? "running" : "cancelling" });
    await requestDurableControl(
      { request } as unknown as DocumentSession,
      sessionId,
      runId,
      kind,
      body,
    );
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[1][0]).toBe(`analysis/runs/${runId}/${kind}`);
    expect(request.mock.calls[1][2]).toMatchObject({
      ...body,
      expectedVersion: 6,
      requestId: expect.any(String),
    });
    expect(request.mock.calls[1][2].requestId).not.toBe(body.requestId);
  });

  it("retains a conflicting approval for explicit review", async () => {
    const conflict = new ApiError(409, "状态已更新", "version_conflict", 6);
    const request = vi.fn().mockRejectedValue(conflict);
    await expect(
      requestDurableControl(
        { request } as unknown as DocumentSession,
        sessionId,
        runId,
        "decisions",
        body,
      ),
    ).rejects.toBe(conflict);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("bounds retries while other control writers keep advancing", async () => {
    const request = vi
      .fn()
      .mockRejectedValue(new ApiError(409, "状态已更新", "version_conflict", 6));
    await expect(
      requestDurableControl(
        { request } as unknown as DocumentSession,
        sessionId,
        runId,
        "cancel",
        body,
      ),
    ).rejects.toMatchObject({ code: "version_conflict" });
    expect(request).toHaveBeenCalledTimes(3);
  });
});
