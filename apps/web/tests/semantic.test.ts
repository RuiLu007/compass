import { describe, it, expect, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { compileBinding } from "../src/server/modules/semantic/bindings";
import { digest } from "../src/server/modules/semantic/catalog";
import { normalize, validateDefinition } from "../src/server/modules/semantic/drafts";
import { metricFields, queryParams } from "../src/contracts/semantic";
import { initialItems, rank, resolveMetric } from "../src/server/modules/semantic/runtime";
import { relatedMetrics } from "../src/server/modules/semantic/retrieval";
import type { MetricView } from "../src/contracts/semantic";
import type { Context } from "../src/server/modules/semantic/access";
import type { Version } from "../src/server/modules/semantic/catalog";

describe("versioned metric contracts", () => {
  it("inherits all published terms across scenes and only adds matching metrics or entities", () => {
    const asset = (id: string, kind: string) =>
      ({
        asset_id: id,
        version_id: id,
        name: id,
        code: id,
        aliases: [],
        kind,
        summary: id,
        definition: { initial_context: kind === "term" ? "on_demand" : "prefer" },
      }) as unknown as MetricView;
    const terms = Array.from({ length: 70 }, (_, i) => asset("term-" + i, "term"));
    const all = [asset("count", "metric"), asset("store", "entity"), ...terms];
    const cold = initialItems(all, [], [], "", 8);
    expect(cold).toHaveLength(70);
    expect(cold.every((item) => item.context_reason === "默认术语")).toBe(true);
    expect(initialItems([...all].reverse(), [], [], "", 8)).toEqual(cold);
    expect(initialItems(all, ["store"], ["count"], "store", 1).map((a) => a.code)).toEqual([
      "store",
      ...cold.map((a) => a.code),
    ]);
  });
  it("retrieves distinct reporting rules when grain and filters interrupt a registered name", () => {
    const catalog = ["初检", "复检"].map((rule) => ({
      name: "仓库" + rule + "合格件数",
      code: "quality." + rule,
      asset_id: rule,
      aliases: [rule + "合格"],
      kind: "metric",
      summary: "统计" + rule + "验收通过的货物",
      definition: {},
      binding_id: "approved",
    })) as unknown as MetricView[];
    const found = relatedMetrics(catalog, "列出初检各仓的合格件数，与复检逐日的合格件数对比");
    expect(found.suggestions.map((m) => m.asset_id)).toEqual(["初检", "复检"]);
    expect(relatedMetrics([catalog[0]], "复检").suggestions).toEqual([]);
  });
  it("resolves contextual choices only inside the visible term family, including definition-only alternatives", async () => {
    const c = {
      scopes: [],
      can: () => true,
      db: {
        query: async (_sql: string, [terms]: string[][]) => ({
          rows: terms.includes("term")
            ? [{ to_asset_id: "paid" }, { to_asset_id: "net" }, { to_asset_id: "hidden" }]
            : [],
        }),
      },
    } as unknown as Context;
    const catalog = [
      ["term", "term", "销售额", null],
      ["metric", "paid", "支付 GMV", "sql1"],
      ["metric", "net", "现金流净 GMV", "sql2"],
      ["metric", "orders", "订单数", "sql3"],
    ].map(([kind, asset_id, name, binding_id]) => ({
      kind,
      asset_id,
      name,
      binding_id,
      version_id: asset_id,
      code: asset_id,
      summary: name,
      aliases: [],
      definition: { dimensions: ["region"] },
    })) as unknown as MetricView[];
    const resolve = (id?: string, all = catalog, dimensions: string[] = []) =>
      resolveMetric(c, { mention: "销售额", metric_id: id, dimensions }, all, "fixed");
    expect(await resolve()).toMatchObject({
      status: "needs_clarification",
      options: [{ asset_id: "paid" }, { asset_id: "net" }],
    });
    for (const id of ["paid", "net"])
      expect(await resolve(id)).toMatchObject({ status: "resolved", metric: { asset_id: id } });
    expect(await resolve("orders")).toMatchObject({
      status: "resolved",
      metric: { asset_id: "orders" },
    });
    expect(await resolve("hidden")).toMatchObject({ status: "no_match" });
    const unbound = catalog.map((m) => ({ ...m, binding_id: null }));
    expect(await resolve(undefined, unbound)).toMatchObject({
      status: "needs_clarification",
      options: [{ executable: false }, { executable: false }],
    });
    expect(await resolve("paid", unbound)).toMatchObject({
      status: "unavailable_binding",
      metric: { name: "支付 GMV" },
    });
    const restricted = { ...c, can: () => false };
    expect(
      await resolveMetric(restricted, { mention: "支付 GMV", dimensions: [] }, unbound, "fixed"),
    ).toMatchObject({ status: "query_forbidden", metric: { name: "支付 GMV" } });
    expect(await resolve("net", catalog, ["store"])).toMatchObject({ status: "invalid_dimension" });
    const partial = catalog.map((m) => (m.asset_id === "net" ? { ...m, binding_id: null } : m));
    expect(await resolve(undefined, partial)).toMatchObject({
      status: "needs_clarification",
      options: [{ executable: true }, { executable: false }],
    });
    expect(await resolve("net", partial)).toMatchObject({ status: "unavailable_binding" });
  });
  const v = {
    code: "new.measure",
    name: "准时件数",
    summary: "在承诺时间完成的履约件数",
    aliases: ["OT 件数"],
    unit: "件",
    definition: { dimensions: ["team"], filters: ["team"], timezone: "Asia/Shanghai" },
  };
  const m = {
    schema: "fulfillment",
    relation: "deliveries",
    measure: "units",
    event_time: "packed_at",
    available_at: "available_at",
    aggregate: "sum" as const,
    connection_id: "ade14486-78ce-497a-b56d-b51d5471dace",
    dimensions: { team: "team_id" },
    filters: { team: "team_id" },
    row_policies: ["tenant_id = current_setting('app.tenant')::uuid"],
    source_release: "2026-q1",
  };
  it("normalizes full-width and case variants, keeping an exact alias above a partial match", () => {
    expect(normalize("  ＯＴ  件数 ")).toBe("ot 件数");
    expect(rank(v, "OT 件数")).toBeGreaterThan(rank(v, "上一周期 OT 件数"));
    expect(rank(v, "上周ＯＴ件数")).toBe(rank(v, "上周 OT 件数"));
    expect(rank({ ...v, name: "仓库 QA 合格量" }, "仓库QA合格量")).toBe(4);
    expect(rank({ ...v, name: "Net Sales" }, "netsales")).toBe(0);
  });
  it("pins the parameterized read, unit, time and field allowlists for a new data source", () => {
    const b = compileBinding(v, m);
    expect(b.access_requirements.required_relations).toEqual(["deliveries"]);
    expect(b.sql_text).toContain('"fulfillment"."deliveries"');
    expect(b.sql_text).toContain("$1::jsonb");
    expect(b.sql_text).toContain("$2::jsonb");
    expect(b.parameter_schema.properties.group_by.enum).toEqual(["total", "team"]);
    expect(b.result_schema.properties.unit.const).toBe("件");
    expect(compileBinding(v, { ...m, measure: "returned" }).target_hash).not.toBe(b.target_hash);
  });
  it("rejects undeclared dimensions and invalid identifiers before issuing a binding", () => {
    expect(() => compileBinding(v, { ...m, dimensions: { route: "team_id" } })).toThrow();
    expect(() => compileBinding(v, { ...m, relation: "space in table" })).toThrow();
  });
  it("requires real dates and rejects undeclared draft fields", () => {
    expect(
      queryParams.safeParse({
        start: "2026-02-29",
        end: "2026-03-01",
        as_of: "2026-04-01T00:00:00Z",
      }).success,
    ).toBe(false);
    expect(metricFields.safeParse({ ...v, role: "admin" }).success).toBe(false);
    const fields = validateDefinition({
      ...v,
      kind: "metric",
      source_id: null,
      definition: { timezone: "invalid" },
    } as unknown as Version).map((f) => f.field);
    expect(fields).toEqual(expect.arrayContaining(["来源", "粒度"]));
    expect(fields).toContain("时区");
  });
  it("binds approval to content while ignoring JSON object order", () => {
    expect(digest({ x: 1, y: { a: 1, b: 2 } })).toBe(digest({ y: { b: 2, a: 1 }, x: 1 }));
    expect(digest({ x: 1 })).not.toBe(digest({ x: 2 }));
  });
  it("reports missing operands and constant zero denominators before review", () => {
    for (const formula of ["sum(units) / 0", "net +", "delete from fact"]) {
      const item = {
        ...v,
        kind: "metric",
        definition: { ...v.definition, formula },
      } as unknown as Version;
      expect(
        validateDefinition(item).some((f) => f.field === "公式" && f.severity === "error"),
      ).toBe(true);
    }
  });
});
