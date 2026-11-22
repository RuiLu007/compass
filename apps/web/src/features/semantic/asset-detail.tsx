import type { AssetRelation, MetricView, ScopeView } from "@/contracts/semantic";

import { domainName } from "./directory";

const labels: Record<string, string> = {
  formula: "计算公式",
  components: "计算要素",
  aggregation: "聚合规则",
  numerator: "分子",
  denominator: "分母",
  additivity: "可加性",
  event_time: "业务时间",
  timezone: "经营时区",
  calendar: "经营日历",
  window: "统计窗口",
  known_at: "数据可见截止",
  exceptions: "例外处理",
  dimensions: "允许维度",
  filters: "允许筛选",
  display: "展示规则",
  blocked_reason: "取数限制",
  table: "数据表",
  primary_key: "主键",
  visible_time: "数据可见时间",
  question: "示例问题",
  counterexample: "易混场景",
  locale: "语言",
};
const vocabulary: Record<string, string> = {
  additive_across_disjoint_order_sets: "订单集合互不重叠时可加总",
  additive_at_declared_grain: "可按声明粒度加总",
  non_additive: "按汇总后的分子、分母重新计算",
  semi_additive_at_close: "同一期末可加总，跨时点分别取期末值",
  CNY_fen: "人民币分",
  piece: "件",
  ratio: "比值",
  order: "单",
};
function display(value: unknown): string {
  if (Array.isArray(value)) return value.map(display).join("、") || "无";
  if (value && typeof value === "object")
    return Object.entries(value)
      .map(([key, v]) => key + " · " + display(v))
      .join("\n");
  const result = String(value ?? "").trim();
  return vocabulary[result] ?? (result || "未填写");
}
export function AssetDetail({
  item,
  scopes,
  open,
}: {
  item: MetricView & { relations?: AssetRelation[] };
  scopes: ScopeView[];
  open: (id: string) => void;
}) {
  const d = item.definition;
  const keys =
    item.kind === "metric"
      ? [
          "formula",
          "components",
          "numerator",
          "denominator",
          "aggregation",
          "additivity",
          "event_time",
          "timezone",
          "calendar",
          "window",
          "known_at",
          "dimensions",
          "filters",
          "exceptions",
          "display",
          "blocked_reason",
        ]
      : item.kind === "term"
        ? ["question", "counterexample", "locale"]
        : ["table", "primary_key", "event_time", "visible_time"];
  const rows: [string, unknown][] = [
    ["业务定义", item.summary],
    ...keys
      .filter((k) => d[k] !== undefined && d[k] !== "")
      .map((k): [string, unknown] => [labels[k], d[k]]),
  ];
  if (item.kind !== "term")
    rows.push([item.kind === "metric" ? "统计粒度" : "实体粒度", item.grain]);
  if (item.kind === "metric")
    rows.push(
      ["计量单位", item.unit],
      ["查询方式", item.binding_id ? "可按授权连接取数" : "仅查定义"],
    );
  rows.push(
    ["别名", item.aliases.filter((a) => a !== item.name)],
    ["业务域", domainName(item.domain)],
    ["维护归属", scopes.find((s) => s.id === item.owner_scope_id)?.label ?? "受限范围"],
    ["使用范围", scopes.find((s) => s.id === item.usage_scope_id)?.label ?? "受限范围"],
    ["负责人", d.responsible ?? item.responsible],
    ["版本", "v" + item.version_no],
  );
  if (item.source_position) rows.push(["来源位置", item.source_position]);
  const fields = Object.entries(d.fields ?? {});
  const relations = item.relations ?? [];
  return (
    <>
      <table className="semantic-properties" aria-label="资产定义">
        <colgroup>
          <col />
          <col />
        </colgroup>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <th scope="row">{k}</th>
              <td>{display(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {fields.length > 0 && (
        <section>
          <h3>实体字段</h3>
          <table className="semantic-properties">
            <colgroup>
              <col />
              <col />
            </colgroup>
            <thead>
              <tr>
                <th>字段</th>
                <th>业务含义</th>
              </tr>
            </thead>
            <tbody>
              {fields.map(([k, v]) => (
                <tr key={k}>
                  <th scope="row">
                    <code>{k}</code>
                  </th>
                  <td>{display(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      <section>
        <h3>
          {item.kind === "entity"
            ? "实体关系"
            : item.kind === "term"
              ? "术语指向"
              : "计算依赖与来源实体"}
        </h3>
        {relations.length ? (
          <div className="semantic-table-scroll">
            <table className="semantic-relations">
              <thead>
                <tr>
                  <th>关联对象</th>
                  {item.kind === "entity" ? (
                    <>
                      <th>连接键（本端 → 对端）</th>
                      <th>基数</th>
                      <th>适用规则</th>
                    </>
                  ) : (
                    <th>关系</th>
                  )}
                </tr>
              </thead>
              <tbody>
                {relations.map((r, i) => (
                  <tr key={r.target_id + i}>
                    <td>
                      <button
                        type="button"
                        className="semantic-link"
                        onClick={() => open(r.target_id)}
                      >
                        {r.name}
                      </button>
                      <small>{r.code}</small>
                    </td>
                    {item.kind === "entity" ? (
                      <>
                        <td>
                          {display(r.rule.local)} → {display(r.rule.remote)}
                        </td>
                        <td>{display(r.rule.cardinality)}</td>
                        <td>
                          {[
                            r.rule.use,
                            r.rule.validity,
                            r.rule.nullable ? "允许空关联" : "必须匹配",
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </td>
                      </>
                    ) : (
                      <td>
                        {{ denotes: "解释为", derived_from: "取自实体", depends_on: "依赖口径" }[
                          r.kind
                        ] ?? r.kind}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="semantic-caption">当前范围内没有登记关联对象</p>
        )}
      </section>
    </>
  );
}
