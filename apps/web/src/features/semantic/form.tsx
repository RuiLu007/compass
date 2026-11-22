import { useState, type RefObject } from "react";
import { UiButton } from "@/components/ui/button";
import type { MetricView, ScopeView, AssetRelation } from "@/contracts/semantic";
import { RelationFields } from "./relation-fields";

const extraMetricFields = {
  components: "计算要素",
  numerator: "分子",
  denominator: "分母",
  additivity: "可加性",
  calendar: "经营日历",
  window: "统计窗口",
  known_at: "数据可见截止",
  exceptions: "例外处理",
  display: "展示规则",
  blocked_reason: "取数限制",
};
type MetricMode = "new" | "edit" | "derive" | "receipt";
export const formTitles = {
  new: "新建资产",
  edit: "编辑资产",
  derive: "从定义派生指标",
  receipt: "确认创建指标草稿",
  source: "登记来源",
};

export function MetricForm({
  mode,
  detail: m,
  defaultKind = "metric",
  items = [],
  initial,
  scopes,
  personal,
  sources,
  busy,
  save,
  cancel,
  id,
  initialFocus,
}: {
  mode: MetricMode;
  detail?: MetricView & { relations?: AssetRelation[] };
  defaultKind?: string;
  items?: MetricView[];
  initial?: Record<string, unknown>;
  scopes: ScopeView[];
  personal: boolean;
  sources: { source_id: string; locator: string; source_version?: string }[];
  busy: boolean;
  save: (f: Record<string, unknown>) => Promise<void>;
  cancel?: () => void;
  id?: string;
  initialFocus?: RefObject<HTMLInputElement | null>;
}) {
  const [kind, setKind] = useState(m?.kind ?? defaultKind);
  const edit = mode === "edit";
  const [owner, setOwner] = useState(
    edit
      ? m!.owner_scope_id
      : personal
        ? (scopes.find((s) => s.kind === "personal")?.id ?? "")
        : (scopes.find((s) => s.manage)?.id ?? ""),
  );
  function field(name: string, label: string, fallback: unknown, multiline = false) {
    const value = initial?.[name] ?? fallback;
    const props = {
      name,
      defaultValue: Array.isArray(value) ? value.join("，") : String(value ?? ""),
      maxLength: multiline ? 2400 : name === "code" ? 120 : 160,
      required:
        ["name", "code", "domain"].includes(name) ||
        (mode !== "receipt" &&
          (["responsible", "summary", "source_position"].includes(name) ||
            (kind === "metric" &&
              ["grain", "unit", "event_time", "timezone", "formula", "aggregation"].includes(
                name,
              )) ||
            (kind === "term" && ["question", "counterexample"].includes(name)) ||
            (kind === "entity" && ["grain", "table", "primary_key", "fields"].includes(name)))),
      readOnly: edit && ["code", "domain"].includes(name),
    };
    return (
      <label className={multiline ? "semantic-wide" : undefined}>
        <span>{label}</span>
        {multiline ? (
          <textarea {...props} rows={2} />
        ) : (
          <input
            {...props}
            ref={name === "name" ? initialFocus : undefined}
            autoFocus={!id && name === "name"}
            pattern={name === "code" ? "[a-z][a-z0-9_.\\-]*" : undefined}
            title={
              name === "code" ? "以小写字母开头，可用小写字母、数字、点、下划线和连字符" : undefined
            }
            placeholder={name === "code" ? "例如 sales.order_count" : undefined}
          />
        )}
      </label>
    );
  }
  return (
    <form
      id={id}
      className={id ? "semantic-editor" : "ui-panel semantic-editor"}
      onSubmit={(e) => {
        e.preventDefault();
        const data = new FormData(e.currentTarget);
        const f: Record<string, unknown> = Object.fromEntries(data);
        if (kind === "metric" && mode !== "receipt") {
          const details: Record<string, string> = {};
          for (const key of Object.keys(extraMetricFields)) {
            if (f[key] || m?.definition[key]) details[key] = String(f[key] ?? "");
            delete f[key];
          }
          f.metric_details = details;
          f.links = data.getAll("lineage").map((target_id) => ({ target_id }));
          delete f.lineage;
          delete f.inputs;
        }
        for (const k of [
          "aliases",
          "dimensions",
          "filters",
          ...(kind === "entity" ? ["primary_key"] : []),
        ])
          f[k] = String(f[k] ?? "")
            .split(/[,，、]/)
            .map((s) => s.trim())
            .filter(Boolean);
        if (typeof f.links === "string") f.links = JSON.parse(f.links);
        if (typeof f.fields === "string") {
          const input = e.currentTarget.elements.namedItem("fields") as HTMLTextAreaElement;
          const entries = f.fields
            .split(/\r?\n/)
            .filter((line) => line.trim())
            .map((line) => line.match(/^([^=＝]+)[=＝](.+)$/));
          if (
            entries.some((row) => !row?.[1].trim() || !row?.[2].trim()) ||
            new Set(entries.map((row) => row?.[1].trim())).size !== entries.length
          ) {
            input.setCustomValidity("每行填写一个字段名 = 业务含义，字段名须唯一。");
            input.reportValidity();
            return;
          }
          f.fields = Object.fromEntries(entries.map((row) => [row![1].trim(), row![2].trim()]));
        }
        void save({
          ...f,
          kind,
          owner_scope_id: owner || undefined,
          usage_scope_id: kind === "term" ? undefined : f.usage_scope_id || owner || undefined,
          source_id: f.source_id || null,
          dependencies:
            kind === "metric" && mode !== "receipt"
              ? data.getAll("inputs")
              : edit
                ? m?.dependencies
                : [],
          ...(edit
            ? { asset_id: m!.asset_id, expected_digest: m!.digest }
            : mode === "derive"
              ? { derived_from: m!.version_id }
              : {}),
        });
      }}
    >
      {!id && <h2>{formTitles[mode]}</h2>}
      {edit && m?.binding_id && (
        <p className="ui-notice">修改会生成新的指标版本。新版本的取数绑定需要单独核验。</p>
      )}
      <fieldset disabled={busy}>
        <legend>基本信息</legend>
        <div className="semantic-field-grid">
          {mode !== "receipt" && (
            <label>
              资产类型
              <select value={kind} disabled={edit} onChange={(e) => setKind(e.target.value)}>
                <option value="metric">指标</option>
                <option value="entity">实体</option>
                <option value="term">术语</option>
              </select>
            </label>
          )}
          {field("name", mode === "receipt" ? "指标名称" : "名称", m?.name)}
          {field("code", mode === "receipt" ? "指标代码" : "代码", edit ? m?.code : "")}
          {field("domain", "业务域", m?.domain)}
          {field("responsible", "负责人", m?.definition.responsible ?? m?.responsible)}
          {field("summary", "业务含义", m?.summary, true)}
          {field("aliases", "别名", m?.aliases)}
        </div>
      </fieldset>
      {kind === "metric" && (
        <fieldset disabled={busy}>
          <legend>计算口径</legend>
          <div className="semantic-field-grid">
            {field("grain", "统计粒度", m?.grain)}
            {field("unit", "单位", m?.unit)}
            {field("event_time", "业务时间", m?.definition.event_time)}
            {field("timezone", "时区", m?.definition.timezone ?? "Asia/Shanghai")}
            {field("formula", "公式", m?.definition.formula, true)}
            {field("aggregation", "聚合规则", m?.definition.aggregation)}
            {field("dimensions", "允许维度", m?.definition.dimensions)}
            {field("filters", "允许筛选", m?.definition.filters)}
            {mode !== "receipt" && (
              <details className="semantic-wide semantic-extra">
                <summary>计算边界与关联资产</summary>
                <div className="semantic-field-grid">
                  {Object.entries(extraMetricFields).map(([key, label]) => (
                    <div key={key}>{field(key, label, m?.definition[key], true)}</div>
                  ))}
                  {(["inputs", "lineage"] as const).map((group) => (
                    <label key={group}>
                      {group === "inputs" ? "依赖指标" : "来源实体"}
                      <select
                        multiple
                        name={group}
                        defaultValue={
                          m?.relations
                            ?.filter(
                              (r) =>
                                r.kind === (group === "inputs" ? "depends_on" : "derived_from") &&
                                (r.editable || group === "inputs"),
                            )
                            .map((r) => r.target_id) ?? []
                        }
                      >
                        {items
                          .filter(
                            (v) =>
                              v.state === "published" &&
                              v.asset_id !== m?.asset_id &&
                              v.kind === (group === "inputs" ? "metric" : "entity"),
                          )
                          .map((v) => (
                            <option key={v.asset_id} value={v.asset_id}>
                              {v.name} · {v.code}
                            </option>
                          ))}
                      </select>
                    </label>
                  ))}
                </div>
              </details>
            )}
          </div>
        </fieldset>
      )}
      {kind === "term" && (
        <fieldset disabled={busy}>
          <legend>术语解释</legend>
          <div className="semantic-field-grid">
            {field("question", "示例问题", m?.definition.question, true)}
            {field("counterexample", "易混场景", m?.definition.counterexample, true)}
          </div>
        </fieldset>
      )}
      {kind === "entity" && (
        <fieldset disabled={busy}>
          <legend>数据实体</legend>
          <div className="semantic-field-grid">
            {field("grain", "实体粒度", m?.grain)}
            {field("table", "数据表", m?.definition.table)}
            {field("primary_key", "主键（多个用逗号分隔）", m?.definition.primary_key)}
            {field("event_time", "业务时间", m?.definition.event_time)}
            {field("visible_time", "数据可见时间", m?.definition.visible_time)}
            <label className="semantic-wide">
              <span>字段字典（每行填写 字段名 = 业务含义）</span>
              <textarea
                required
                name="fields"
                rows={5}
                onChange={(e) => e.currentTarget.setCustomValidity("")}
                defaultValue={Object.entries(m?.definition.fields ?? {})
                  .map(([k, v]) => k + " = " + v)
                  .join("\n")}
              />
            </label>
          </div>
        </fieldset>
      )}
      {kind !== "metric" && <RelationFields key={kind} kind={kind} items={items} detail={m} />}
      <fieldset disabled={busy}>
        <legend>归属与来源</legend>
        <div className="semantic-field-grid">
          <label>
            维护归属
            <select value={owner} onChange={(e) => setOwner(e.target.value)} disabled={edit}>
              {personal && !scopes.some((s) => s.kind === "personal") && (
                <option value="">仅我</option>
              )}
              {scopes
                .filter((s) => s.manage)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
            </select>
          </label>
          {kind === "term" ? (
            <p className="semantic-caption">
              {edit && scopes.some((s) => s.kind === "personal" && s.id === m?.usage_scope_id)
                ? "此历史术语仅本人可用。"
                : "已发布术语供所有成员使用，自动加入数据分析上下文。"}
            </p>
          ) : (
            <label>
              发布范围
              <select
                key={owner}
                name="usage_scope_id"
                defaultValue={edit ? m?.usage_scope_id : owner}
              >
                <option value={owner}>{scopes.find((s) => s.id === owner)?.label ?? "仅我"}</option>
                {scopes
                  .filter(
                    (s) =>
                      s.share && s.id !== owner && s.kind !== "platform" && s.kind !== "personal",
                  )
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {mode === "receipt" ? (
            <p>来源已关联所选查询回执。期间和地区可填写为参数示例。</p>
          ) : (
            <label>
              登记来源
              <select name="source_id" required defaultValue={m?.source_id ?? ""}>
                <option value="">选择来源</option>
                {sources.map((s) => (
                  <option key={s.source_id} value={s.source_id}>
                    {s.locator}
                    {s.source_version && " · v" + s.source_version}
                  </option>
                ))}
              </select>
            </label>
          )}
          {field("source_position", "来源位置", m?.source_position)}
        </div>
      </fieldset>
      {!id && (
        <div className="ui-actions">
          <UiButton variant="secondary" disabled={busy} onClick={cancel}>
            取消
          </UiButton>
          <UiButton type="submit" busy={busy}>
            {busy ? "正在保存…" : "保存草稿"}
          </UiButton>
        </div>
      )}
    </form>
  );
}
