import { describe, it, expect, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { compileBinding } from "../src/server/modules/semantic/bindings";
import { digest } from "../src/server/modules/semantic/catalog";
import { normalize, validateDefinition } from "../src/server/modules/semantic/drafts";
import { metricFields, queryParams } from "../src/contracts/semantic";
import { rank } from "../src/server/modules/semantic/runtime";
import { relatedMetrics } from "../src/server/modules/semantic/retrieval";
import type { MetricView } from "../src/contracts/semantic";
import type { Version } from "../src/server/modules/semantic/catalog";

describe("versioned metric contracts", () => {
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
    expect(found.byClause.map((m) => m.asset_id)).toEqual(["初检", "复检"]);
    expect(relatedMetrics([catalog[0]], "复检").byClause).toEqual([]);
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
