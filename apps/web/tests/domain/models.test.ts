import { describe, it, expect, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { createModelAuthorizer } from "../../src/server/modules/models/public";
import type { AccessStore, CurrentAccess } from "../../src/server/modules/access/public";
import type { IdentityStore } from "../../src/server/modules/identity/public";
import { DEFAULT_TEMPLATES } from "../../src/server/modules/access/domain/permissions";

function setup(
  options: {
    platform?: boolean;
    role?: "admin" | "member";
    permissions?: CurrentAccess["tenantPermissions"];
    workspace?: string;
    status?: string;
  } = {},
) {
  const current = vi.fn().mockResolvedValue({
    tenantId: "tenant",
    memberId: "member",
    role: options.role ?? "member",
    authzVersion: "1",
    tenantPermissions: options.permissions ?? [],
    workspacePermissions: new Map(),
  });
  const workspace = vi.fn().mockResolvedValue({
    tenantId: options.workspace ?? "tenant",
    status: options.status ?? "active",
  });
  return {
    current,
    workspace,
    authorize: createModelAuthorizer({
      identity: {
        requireActor: vi
          .fn()
          .mockResolvedValue({ userId: "actor", isPlatformAdmin: options.platform ?? false }),
      } as unknown as IdentityStore,
      access: { current, workspace } as unknown as AccessStore,
    }),
  };
}
const chat = { kind: "tenant", purpose: "chat", tenantId: "tenant", workspaceId: "space" } as const;
describe("model authorization boundary", () => {
  it("allows a platform administrator without tenant membership and forbids a tenant administrator", async () => {
    const global = setup({ platform: true });
    expect(await global.authorize("token", { kind: "platform", purpose: "test" })).toEqual({
      actor_user_id: "actor",
      scope: { kind: "platform" },
      purpose: "test",
    });
    expect(global.current).not.toHaveBeenCalled();
    await expect(
      setup({ role: "admin" }).authorize("token", { kind: "platform", purpose: "manage" }),
    ).rejects.toMatchObject({ code: "forbidden" });
  });
  it("requires both chat grants without requiring workspace read and leaves default templates unchanged", async () => {
    for (const permissions of [[], ["module.analysis"], ["models.invoke"]] as const) {
      await expect(setup({ permissions }).authorize("token", chat)).rejects.toMatchObject({
        code: "forbidden",
      });
    }
    expect(
      await setup({ permissions: ["module.analysis", "models.invoke"] }).authorize("token", chat),
    ).toMatchObject({ purpose: "chat", scope: { tenant_id: "tenant", workspace_id: "space" } });
    expect(Object.values(DEFAULT_TEMPLATES).flat()).not.toContain("models.invoke");
  });
  it("checks real active workspace ownership even for tenant administrators", async () => {
    for (const options of [{ workspace: "other" }, { status: "archived" }])
      await expect(
        setup({ role: "admin", ...options }).authorize("token", chat),
      ).rejects.toMatchObject({ code: "forbidden" });
    const missing = setup({ role: "admin" });
    missing.workspace.mockRejectedValue({ code: "not_found" });
    await expect(missing.authorize("token", chat)).rejects.toMatchObject({ code: "not_found" });
  });
  it("keeps module read and chat invocation grants separate", async () => {
    const moduleAccess = setup({ permissions: ["module.admin.capabilities"] });
    expect(
      await moduleAccess.authorize("token", { ...chat, purpose: "module_read" }),
    ).toMatchObject({
      purpose: "module_read",
    });
    await expect(moduleAccess.authorize("token", chat)).rejects.toMatchObject({
      code: "forbidden",
    });
  });
});
