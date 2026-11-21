import { randomUUID } from "node:crypto";
import { beforeAll, afterAll, afterEach, it, expect, vi } from "vitest";
import pg from "pg";
vi.mock("server-only", () => ({}));
import { handleWebRequest } from "../../src/server/http/handler";
import { closeDatabase } from "../../src/server/platform/db";
import { encodePassword } from "../../src/server/modules/identity/infrastructure/password";
import { ready } from "../../src/server/composition";
const origin = "http://127.0.0.1:8100",
  password = "model-integration-password";
const ids = {
  platform: randomUUID(),
  admin: randomUUID(),
  member: randomUUID(),
  tenant: randomUUID(),
  other: randomUUID(),
  space: randomUUID(),
  otherSpace: randomUUID(),
  membership: randomUUID(),
  binding: randomUUID(),
};
let owner: pg.Client;
type Session = { cookie: string; csrf: string };
let platform: Session, admin: Session, member: Session;
async function request(path: string, session?: Session, method = "GET", body?: unknown) {
  return handleWebRequest(
    new Request(`${origin}/api/web/v1/${path}`, {
      method,
      headers: {
        ...(session ? { cookie: session.cookie, "x-csrf-token": session.csrf } : {}),
        origin,
        "content-type": "application/json",
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    }),
  );
}
async function login(loginName: string): Promise<Session> {
  const response = await request("auth/login", undefined, "POST", { loginName, password });
  expect(response.status).toBe(200);
  const cookie = response.headers.get("set-cookie")!.split(";")[0];
  const view = await handleWebRequest(
    new Request(`${origin}/api/web/v1/session`, { headers: { cookie } }),
  );
  return { cookie, csrf: (await view.json()).csrfToken };
}
beforeAll(async () => {
  const runtime = process.env.WEB_TEST_DATABASE_URL,
    management = process.env.WEB_TEST_MIGRATION_DATABASE_URL;
  if (
    !runtime ||
    !management ||
    ![runtime, management].every((value) =>
      /^\/compass_web_test_[a-z0-9_]+$/.test(new URL(value).pathname),
    )
  )
    throw new Error("Dedicated Web test database required");
  process.env.WEB_DATABASE_URL = runtime;
  process.env.WEB_PUBLIC_ORIGIN = origin;
  process.env.WEB_AUTH_ALLOW_LOCAL_HTTP = "true";
  process.env.COMPASS_AGENT_SERVICE_TOKEN = "fixture-service-token";
  owner = new pg.Client({ connectionString: management });
  await owner.connect();
  await owner.query("SET ROLE compass_web_owner");
  await owner.query(
    `TRUNCATE web.semantic_feedback, web.semantic_reference, web.semantic_revocation, web.semantic_event, web.semantic_release_head, web.semantic_release, web.semantic_reviews, web.semantic_scope_grants, web.semantic_scope_memberships, web.semantic_policy, web.execution_bindings, web.semantic_relations, web.semantic_aliases,
     web.semantic_versions, web.semantic_assets, web.semantic_scopes, web.semantic_source,
     web.notification, web.audit_event, web.workspace_member, web.tenant_member, web.workspace,
     web.tenant, web.web_session, web.app_user`,
  );
  const hash = await encodePassword(password);
  for (const [kind, id] of [
    ["platform", ids.platform],
    ["admin", ids.admin],
    ["member", ids.member],
  ])
    await owner.query(
      "INSERT INTO web.app_user(id,login_name,password_hash,display_name,is_platform_admin) VALUES($1,$2,$3,$2,$4)",
      [id, `models.${kind}`, hash, kind === "platform"],
    );
  await owner.query(
    "INSERT INTO web.tenant(id,name,timezone) VALUES($1,'Model tenant','UTC'),($2,'Other tenant','UTC')",
    [ids.tenant, ids.other],
  );
  await owner.query(
    "INSERT INTO web.workspace(id,tenant_id,name) VALUES($1,$2,'Model space'),($3,$4,'Other space')",
    [ids.space, ids.tenant, ids.otherSpace, ids.other],
  );
  await owner.query(
    "INSERT INTO web.tenant_member(id,tenant_id,user_id,role_key) VALUES($1,$2,$3,'member'),($4,$2,$5,'admin')",
    [ids.membership, ids.tenant, ids.member, randomUUID(), ids.admin],
  );
  await owner.query(
    "INSERT INTO web.workspace_member(id,tenant_id,workspace_id,tenant_member_id,role_key,permission_keys) VALUES($1,$2,$3,$4,'analyst',ARRAY['module.analysis'])",
    [ids.binding, ids.tenant, ids.space, ids.membership],
  );
  platform = await login("models.platform");
  admin = await login("models.admin");
  member = await login("models.member");
});
afterAll(async () => {
  await closeDatabase();
  await owner?.end();
});
afterEach(() => vi.unstubAllGlobals());
const available = (view = "chat", space = ids.space) =>
  `models/available?tenantId=${ids.tenant}&workspaceId=${space}&view=${view}`;
it("publishes the new migration readiness and authorizes platform management without a tenant", async () => {
  expect(await ready()).toEqual({ status: "ready" });
  const fetch = vi.fn().mockImplementation(async () => {
    const active = await owner.query(
      "SELECT count(*)::int AS n FROM pg_stat_activity WHERE usename='compass_web_runtime' AND state='idle in transaction'",
    );
    expect(active.rows[0].n).toBe(0);
    return Response.json({ items: [], total: 0, page: 1, page_size: 20 });
  });
  vi.stubGlobal("fetch", fetch);
  expect((await request("platform/models", platform)).status).toBe(200);
  expect((await request("platform/models", admin)).status).toBe(403);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("allows platform chat and model modules without membership while preserving workspace state and ownership", async () => {
  const fetch = vi.fn().mockImplementation(async () => Response.json({ items: [] }));
  vi.stubGlobal("fetch", fetch);
  expect((await request(available(), platform)).status).toBe(200);
  expect((await request(available("module"), platform)).status).toBe(200);
  expect((await request(available("chat", ids.otherSpace), platform)).status).toBe(404);
  await owner.query("UPDATE web.workspace SET status='archived' WHERE id=$1", [ids.space]);
  try {
    expect((await request(available(), platform)).status).toBe(403);
  } finally {
    await owner.query("UPDATE web.workspace SET status='active' WHERE id=$1", [ids.space]);
  }
  expect(fetch).toHaveBeenCalledTimes(2);
});
it("enforces explicit model grants and active workspace ownership with a single denial audit", async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json({ items: [] }));
  vi.stubGlobal("fetch", fetch);
  const before = await owner.query(
    "SELECT count(*)::int AS n FROM web.audit_event WHERE outcome='denied'",
  );
  expect((await request(available(), member)).status).toBe(403);
  expect(
    (await owner.query("SELECT count(*)::int AS n FROM web.audit_event WHERE outcome='denied'"))
      .rows[0].n,
  ).toBe(before.rows[0].n + 1);
  await owner.query(
    "UPDATE web.workspace_member SET permission_keys=ARRAY['module.analysis','models.invoke'] WHERE id=$1",
    [ids.binding],
  );
  expect((await request(available(), member)).status).toBe(200);
  expect((await request(available("module"), member)).status).toBe(403);
  expect((await request(available("chat", ids.otherSpace), admin)).status).toBe(404);
  await owner.query("UPDATE web.workspace SET status='archived' WHERE id=$1", [ids.space]);
  expect((await request(available(), admin)).status).toBe(403);
  await owner.query("UPDATE web.workspace SET status='active' WHERE id=$1", [ids.space]);
  expect(fetch).toHaveBeenCalledTimes(1);
});
