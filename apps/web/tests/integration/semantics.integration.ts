import { randomUUID } from "node:crypto";
import { expect, it, vi } from "vitest";
import pg from "pg";
vi.mock("server-only", () => ({}));
import { semanticAccess } from "../../src/server/modules/semantic/access";
import { read, write } from "../../src/server/modules/semantic/service";
import { registry, restrictRuntime } from "../../src/server/modules/semantic/runtime";
import { withTransaction, closeDatabase } from "../../src/server/platform/db";

it("protects personal drafts, nominates reviewers, publishes shares and revokes pinned reads in PostgreSQL", async () => {
  const database = process.env.WEB_TEST_DATABASE_URL,
    migration = process.env.WEB_TEST_MIGRATION_DATABASE_URL;
  if (
    !database ||
    !migration ||
    !/^\/compass_web_test_[a-z0-9_]+$/.test(new URL(database).pathname)
  )
    throw Error("dedicated Web test database required");
  process.env.WEB_DATABASE_URL = database;
  const db = new pg.Client({ connectionString: migration });
  await db.connect();
  const tenantId = randomUUID(),
    workspaceId = randomUUID(),
    people = Array.from({ length: 4 }, () => ({
      userId: randomUUID(),
      member: randomUUID(),
      isPlatformAdmin: false,
      sessionId: "test",
      expiresAt: "",
    }));
  const [author, business, data, reader] = people;
  try {
    await db.query("INSERT INTO web.tenant(id,name,timezone) VALUES($1,$2,'UTC')", [
      tenantId,
      "team-" + tenantId.slice(0, 8),
    ]);
    await db.query("INSERT INTO web.workspace(id,tenant_id,name) VALUES($1,$2,'north')", [
      workspaceId,
      tenantId,
    ]);
    for (const p of people) {
      await db.query(
        "INSERT INTO web.app_user(id,login_name,password_hash,display_name) VALUES ($1,$2,'test-only','Test')",
        [p.userId, "t." + p.userId.slice(0, 12)],
      );
      await db.query(
        "INSERT INTO web.tenant_member(id,tenant_id,user_id,role_key,permission_keys) VALUES($1,$2,$3,$4,$5)",
        [
          p.member,
          tenantId,
          p.userId,
          p === business ? "admin" : "member",
          p === business
            ? []
            : [
                "semantics.read",
                "semantics.draft",
                "semantics.govern",
                "semantics.query",
                "module.analysis",
              ],
        ],
      );
    }
    const target = { tenantId, workspaceId };
    const post = (p: typeof reader, path: string, body: Record<string, unknown>) =>
      withTransaction("command", async (conn) =>
        write(await semanticAccess(conn, p, target), path.split("/"), {
          request_key: randomUUID(),
          ...body,
        }),
      ) as Promise<Record<string, string>>;
    const get = (p: typeof reader, key: string) =>
      withTransaction("query", async (conn) =>
        read(await semanticAccess(conn, p, target), ["assets", key], {}),
      );
    const search = () =>
      withTransaction("query", async (conn) =>
        registry(await semanticAccess(conn, reader, target), "search", {
          query: "cartons warehouse",
        }),
      );
    const runtime = (p: typeof reader, pins?: string[]) =>
      withTransaction("query", async (conn) =>
        registry(await semanticAccess(conn, p, target), "context", {}, pins),
      );
    const tenant = await post(business, "scopes", { kind: "tenant" });
    const dept = await post(business, "scopes", { kind: "department", name: "retention" });
    const grant = (person: typeof reader, scope_id: string, capability: string, active = true) =>
      post(business, "grants", { scope_id, member_id: person.member, capability, active });
    await grant(data, tenant.scope_id, "data");
    const source = await post(author, "sources", {
      kind: "document",
      locator: "handbook",
      version: "1",
      excerpt: "完成事项计数",
      priority: "P1",
      responsible: "owner",
    });
    const base = {
      code: "work.count",
      name: "完成事项",
      unit: "次",
      summary: "按完成日期计数",
      aliases: ["warehouse hold cartons"],
      domain: "履约",
      grain: "事项",
      formula: "count(item)",
      aggregation: "count",
      event_time: "completed_at",
      timezone: "UTC",
      responsible: "审核组",
      source_id: source.source_id,
      source_position: "§1",
    };
    const key = randomUUID();
    const [first, retried] = await Promise.all([
      post(author, "drafts", { ...base, request_key: key }),
      post(author, "drafts", { ...base, request_key: key }),
    ]);
    expect(retried).toEqual(first);
    await expect(get(business, first.asset_id)).rejects.toMatchObject({ code: "not_found" });
    await expect(get(reader, first.asset_id)).rejects.toMatchObject({ code: "not_found" });
    const approve = async (v: typeof first) => {
      const step = (person: typeof reader, action: string) =>
        post(person, "revisions/" + v.version_id + "/" + action, {
          expected_digest: v.digest,
          note: "已核对",
        });
      await step(author, "submit");
      await expect(step(author, "business")).rejects.toMatchObject({ code: "forbidden" });
      expect(await get(business, v.asset_id)).toMatchObject({ state: "business" });
      await step(business, "business");
      await expect(step(business, "data")).rejects.toMatchObject({ code: "forbidden" });
      await step(data, "data");
      const detail = (await get(business, v.asset_id)) as { generation: number };
      return post(business, "releases", {
        version_id: v.version_id,
        expected_digest: v.digest,
        expected_generation: detail.generation,
        note: "开始使用",
      });
    };
    await approve(first);
    expect(await get(author, first.asset_id)).toMatchObject({
      state: "published",
      actions: expect.arrayContaining(["revise"]),
    });
    await expect(get(reader, first.asset_id)).rejects.toMatchObject({ code: "not_found" });
    expect(await search()).toMatchObject({ items: [] });
    await grant(author, dept.scope_id, "share");
    await grant(business, dept.scope_id, "business");
    await grant(business, dept.scope_id, "release");
    await grant(business, dept.scope_id, "data");
    await grant(data, dept.scope_id, "data");
    await grant(reader, dept.scope_id, "member");
    const second = await post(author, "drafts", {
      ...base,
      asset_id: first.asset_id,
      expected_digest: first.digest,
      usage_scope_id: dept.scope_id,
    });
    await approve(second);
    expect(await get(reader, first.asset_id)).toMatchObject({
      version_id: second.version_id,
      editable: false,
    });
    expect(await search()).toMatchObject({
      items: [
        { version_id: second.version_id, scope_label: "retention", match_reason: "全文词项匹配" },
      ],
    });
    const active = (await runtime(reader)) as { releases: string[] };
    await grant(business, dept.scope_id, "manage");
    const material = await post(business, "sources", {
      kind: "document",
      locator: "department-policy",
      version: "1",
      excerpt: "部门权限策略",
      owner_scope_id: dept.scope_id,
      priority: "P1",
      responsible: "owner",
    });
    const policy = (await withTransaction("query", async (conn) =>
      read(await semanticAccess(conn, business, target), ["policies", dept.scope_id], {}),
    )) as { generation: number };
    const restrict = (disabled: string[], generation: number) =>
      post(business, "policies", {
        scope_id: dept.scope_id,
        source_id: material.source_id,
        note: "更新查询授权",
        expected_generation: generation,
        policy: {
          defaults: [],
          pinned_asset_ids: [],
          page_size: 8,
          max_search_rounds: 2,
          context_max_items: 2,
          disabled_capabilities: disabled,
        },
      });
    const limited = await restrict(["query"], policy.generation);
    const limitedPins = ((await runtime(reader)) as { releases: string[] }).releases;
    const capabilities = (pins?: string[]) =>
      withTransaction("query", async (conn) => {
        const c = await restrictRuntime(await semanticAccess(conn, reader, target), pins);
        return [c.can("read"), c.can("query")];
      });
    expect(await capabilities(active.releases)).toEqual([true, false]);
    await restrict([], Number(limited.generation));
    expect(await capabilities(limitedPins)).toEqual([true, false]);
    expect(await capabilities()).toEqual([true, true]);
    await grant(reader, dept.scope_id, "member", false);
    expect(await search()).toMatchObject({ items: [] });
    await expect(get(reader, first.asset_id)).rejects.toMatchObject({ code: "not_found" });
    await expect(
      withTransaction("query", async (conn) =>
        registry(
          await semanticAccess(conn, reader, target),
          "definition",
          { asset_id: first.asset_id },
          active.releases,
        ),
      ),
    ).rejects.toMatchObject({ code: "not_found" });
  } finally {
    await db.end();
    await closeDatabase();
  }
});
