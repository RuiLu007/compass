import { randomUUID } from "node:crypto";
import { expect, it, vi } from "vitest";
import pg from "pg";
vi.mock("server-only", () => ({}));
import { authorizeRuntime } from "../../src/server/composition";
import { semanticAccess, publishedScopeIds } from "../../src/server/modules/semantic/access";
import { saveResultDraft } from "../../src/server/modules/semantic/drafts";
import { previewRevision, saveRevision } from "../../src/server/modules/semantic/chat-revisions";
import { read, write } from "../../src/server/modules/semantic/service";
import { registry } from "../../src/server/modules/semantic/runtime";
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
    const actorContext = {
      actor_user_id: business.userId,
      purpose: "chat",
      scope: { kind: "tenant", tenant_id: tenantId, workspace_id: workspaceId },
    };
    expect(await authorizeRuntime(actorContext, false, false, true)).toMatchObject({
      runEnvironment: {
        tenantName: "team-" + tenantId.slice(0, 8),
        workspaceName: "north",
        timezone: "UTC",
      },
    });
    await expect(
      authorizeRuntime({ ...actorContext, actor_user_id: reader.userId }, false, false, true),
    ).rejects.toMatchObject({ code: "forbidden" });
    expect(await authorizeRuntime(actorContext, false)).not.toHaveProperty("runEnvironment");
    await withTransaction("query", async (conn) => {
      const platform = await semanticAccess(conn, { ...business, isPlatformAdmin: true }, target);
      expect(platform.memberId).toBe(business.member);
      expect(platform.tenantAdmin).toBe(true);
      expect(
        (await semanticAccess(conn, { ...reader, isPlatformAdmin: true }, target)).tenantAdmin,
      ).toBe(false);
    });
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
    await db.query("UPDATE web.tenant_member SET permission_keys=$2 WHERE id=$1", [
      reader.member,
      ["module.analysis"],
    ]);
    await withTransaction("query", async (conn) => {
      const access = await semanticAccess(conn, reader, target, true);
      expect(await registry(access, "manifest", {})).toMatchObject({
        items: [],
        capabilities: { read: false, query: false },
      });
      expect(await registry(access, "manifest", {}, [])).toMatchObject({ releases: [] });
      await expect(semanticAccess(conn, reader, target)).rejects.toMatchObject({
        code: "forbidden",
      });
    });
    await db.query("UPDATE web.tenant_member SET permission_keys=$2 WHERE id=$1", [
      reader.member,
      [
        "module.analysis",
        "semantics.read",
        "semantics.query",
        "semantics.draft",
        "semantics.govern",
      ],
    ]);
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
    const term = await post(author, "assets", {
      ...base,
      kind: "term",
      code: "work.glossary",
      name: "完成事件",
      summary: "事项进入完成状态时记录的业务事件",
      aliases: ["事项完成"],
      question: "如何确定完成时点？",
      counterexample: "创建时点",
    });
    const termRef = { asset_id: term.asset_id, version_id: term.version_id };
    for (const actor of [data, reader]) {
      expect(await get(actor, term.asset_id)).toMatchObject({ ...termRef, editable: false });
    }
    await db.query("UPDATE web.tenant_member SET permission_keys=$2 WHERE id=$1", [
      reader.member,
      ["module.analysis"],
    ]);
    await withTransaction("query", async (conn) => {
      const c = await semanticAccess(conn, reader, target, true);
      const context = (await registry(c, "context", {
        question: "盘点情况",
        scene_hint: "经营报告",
      })) as { releases: string[]; items: unknown[] };
      expect(context).toMatchObject({
        items: expect.arrayContaining([
          expect.objectContaining({ ...termRef, context_reason: "默认术语" }),
        ]),
      });
      expect(await registry(c, "glossary", {}, context.releases)).toMatchObject({
        items: context.items,
      });
      expect(await registry(c, "authorize", { refs: [termRef] }, context.releases)).toEqual({
        bindings: [],
      });
    });
    await db.query("UPDATE web.tenant_member SET permission_keys=$2 WHERE id=$1", [
      reader.member,
      [
        "module.analysis",
        "semantics.read",
        "semantics.query",
        "semantics.draft",
        "semantics.govern",
      ],
    ]);
    await expect(
      post(business, "asset-grants", {
        mode: "member",
        target_ids: [reader.member],
        asset_ids: [term.asset_id],
        active: true,
      }),
    ).rejects.toMatchObject({ code: "validation_failed" });
    const receipt = {
      result_ref: randomUUID(),
      columns: ["order_count"],
      captured_at: new Date().toISOString(),
      fingerprint: "a".repeat(64),
      definition_hash: "b".repeat(64),
      fields: {
        request_key: randomUUID(),
        name: "签收订单占比",
        domain: "履约",
        code: "qa.delivered_share",
        summary: "发运订单中已签收的订单占比，按发运日期归期。",
        unit: "%",
        formula: "签收订单数 ÷ 发运订单数 × 100%",
      },
    };
    const saveSuggested = () =>
      withTransaction("command", async (conn) =>
        saveResultDraft(await semanticAccess(conn, author, target), receipt),
      );
    const suggestedDraft = await saveSuggested();
    expect(await saveSuggested()).toEqual(suggestedDraft);
    const asset = await get(author, String(suggestedDraft.asset_id));
    expect(asset).toMatchObject({
      name: receipt.fields.name,
      summary: receipt.fields.summary,
      definition: { formula: receipt.fields.formula },
      state: "draft",
    });
    await expect(get(reader, String(suggestedDraft.asset_id))).rejects.toMatchObject({
      code: "not_found",
    });
    const original = await post(author, "drafts", {
      ...base,
      name: "跨日去重",
      code: "work.distinct",
    });
    const changes = { summary: "在统计期内去重", unit: "种" };
    const preview = (person: typeof author) =>
      withTransaction("query", async (conn) =>
        previewRevision(await semanticAccess(conn, person, target), {
          metric: "work.distinct",
          changes,
        }),
      );
    const proposal = await preview(author);
    expect(proposal.changes).toEqual([
      { field: "summary", before: base.summary, after: changes.summary },
      { field: "unit", before: base.unit, after: changes.unit },
    ]);
    expect(await get(author, original.asset_id)).toMatchObject({ version_id: original.version_id });
    await expect(preview(reader)).rejects.toMatchObject({ code: "not_found" });
    const requestKey = randomUUID();
    const save = (person: typeof author, request_key: string) =>
      withTransaction("command", async (conn) =>
        saveRevision(await semanticAccess(conn, person, target), {
          asset_id: original.asset_id,
          expected_digest: proposal.expected_digest,
          changes,
          request_key,
        }),
      );
    const saved = await save(author, requestKey);
    expect(await save(author, requestKey)).toEqual(saved);
    await expect(save(author, randomUUID())).rejects.toMatchObject({ code: "conflict" });
    await expect(save(reader, randomUUID())).rejects.toMatchObject({ code: "not_found" });
    expect(await get(author, original.asset_id)).toMatchObject({
      asset_id: original.asset_id,
      draft_id: original.draft_id,
      version_no: 2,
      ...changes,
      source_id: base.source_id,
      definition: { formula: base.formula },
      state: "draft",
    });
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
      initial_context: "prefer",
    });
    await approve(second);
    expect(await runtime(reader)).toMatchObject({
      items: expect.not.arrayContaining([
        expect.objectContaining({ version_id: second.version_id }),
      ]),
    });
    expect(await get(reader, first.asset_id)).toMatchObject({
      version_id: second.version_id,
      editable: false,
    });
    expect(await search()).toMatchObject({
      items: [
        { version_id: second.version_id, scope_label: "retention", match_reason: "全文词项匹配" },
      ],
    });
    expect(await runtime(reader, [])).toMatchObject({ releases: [], items: [] });
    await withTransaction("query", async (conn) => {
      for (const actor of [business, { ...author, isPlatformAdmin: true }]) {
        const c = await semanticAccess(conn, actor, target);
        expect(publishedScopeIds(c)).toContain(dept.scope_id);
        expect(c.scopes.some((s) => s.id === dept.scope_id && s.use)).toBe(false);
        expect(await registry(c, "context", {})).toMatchObject({
          items: expect.not.arrayContaining([
            expect.objectContaining({ version_id: second.version_id }),
          ]),
        });
        expect(await registry(c, "search", { query: "cartons warehouse" })).toMatchObject({
          items: [{ version_id: second.version_id }],
        });
      }
    });
    const active = (await runtime(reader)) as { releases: string[] };
    expect(await get(reader, first.asset_id)).not.toHaveProperty("feedback");
    for (const endpoint of ["feedback", "policies"]) {
      await expect(post(business, endpoint, {})).rejects.toMatchObject({ code: "not_found" });
    }
    const assetGrant = (
      mode: string,
      target_ids: string[],
      active: boolean,
      asset_ids = [first.asset_id],
    ) => post(business, "asset-grants", { mode, target_ids, asset_ids, active });
    await expect(
      post(reader, "asset-grants", {
        mode: "member",
        target_ids: [reader.member],
        asset_ids: [first.asset_id],
        active: true,
      }),
    ).rejects.toMatchObject({ code: "forbidden" });
    await assetGrant("department", [dept.scope_id], false);
    expect(await search()).toMatchObject({ items: [] });
    await expect(assetGrant("member", [reader.member, randomUUID()], true)).rejects.toMatchObject({
      code: "validation_failed",
    });
    expect(await search()).toMatchObject({ items: [] });
    await assetGrant("member", [reader.member, data.member], true);
    await assetGrant("department", [dept.scope_id], true);
    await assetGrant("member", [reader.member], false);
    expect(await search()).toMatchObject({ items: [{ asset_id: first.asset_id }] });
    expect(await get(data, first.asset_id)).toMatchObject({ asset_id: first.asset_id });
    await assetGrant("member", [data.member], false);
    await expect(get(data, first.asset_id)).rejects.toMatchObject({ code: "not_found" });
    await grant(reader, dept.scope_id, "member", false);
    expect(await runtime(reader, active.releases)).toMatchObject({
      items: expect.not.arrayContaining([expect.objectContaining({ asset_id: first.asset_id })]),
    });
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
