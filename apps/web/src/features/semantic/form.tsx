import { type MetricView, type ScopeView } from "@/contracts/semantic";
import type { Suggestion } from "./suggestions";
import { useState } from "react";

export function MetricForm({
  mode,
  detail: m,
  suggestion,
  initial,
  scopes,
  personal,
  sources,
  busy,
  save,
  cancel,
}: {
  mode: string;
  detail?: MetricView;
  suggestion?: Suggestion;
  initial?: Record<string, unknown>;
  scopes: ScopeView[];
  personal: boolean;
  sources: { source_id: string; locator: string }[];
  busy: boolean;
  save: (f: Record<string, unknown>) => Promise<void>;
  cancel: () => void;
}) {
  const edit = mode === "edit";
  const [owner, setOwner] = useState(
    edit ? m!.owner_scope_id : personal ? "" : (scopes.find((s) => s.manage)?.id ?? ""),
  );
  const fields = [
    ["name", "指标名称", suggestion?.name ?? m?.name],
    ["code", "指标代码", edit ? m?.code : ""],
    ["domain", "业务域", m?.domain],
    ["responsible", "负责人", m?.definition.responsible ?? m?.responsible],
    ["summary", "业务含义", suggestion?.summary ?? m?.summary],
    ["formula", "公式", suggestion?.formula ?? m?.definition.formula],
    ["event_time", "业务时间", m?.definition.event_time],
    ["timezone", "时区", m?.definition.timezone ?? "Asia/Shanghai"],
    ["grain", "统计粒度", m?.grain],
    ["unit", "单位", m?.unit],
    ["aggregation", "聚合规则", m?.definition.aggregation],
    ["aliases", "别名", m?.aliases],
    ["dimensions", "允许维度", m?.definition.dimensions],
    ["filters", "允许筛选", m?.definition.filters],
    ["source_position", "来源位置", suggestion?.position ?? m?.source_position],
  ] as const;
  return (
    <form
      className="ui-panel semantic-form"
      onSubmit={(e) => {
        e.preventDefault();
        const f: Record<string, unknown> = Object.fromEntries(new FormData(e.currentTarget));
        for (const k of ["aliases", "dimensions", "filters"])
          f[k] = String(f[k] ?? "")
            .split(/[,，、]/)
            .map((s) => s.trim())
            .filter(Boolean);
        void save({
          ...f,
          kind: m?.kind ?? "metric",
          owner_scope_id: owner || undefined,
          usage_scope_id: f.usage_scope_id || owner || undefined,
          source_id: f.source_id || null,
          dependencies: edit ? m?.dependencies : [],
          ...(edit
            ? { asset_id: m!.asset_id, expected_digest: m!.digest }
            : mode === "derive"
              ? { derived_from: m!.version_id }
              : {}),
        });
      }}
    >
      <h2>
        {edit
          ? "编辑草稿"
          : mode === "receipt"
            ? "确认创建指标草稿"
            : mode === "derive"
              ? "从定义派生指标"
              : "新建指标"}
      </h2>
      <label>
        维护归属
        <select value={owner} onChange={(e) => setOwner(e.target.value)} disabled={edit}>
          {personal && <option value="">我</option>}
          {scopes
            .filter((s) => s.manage)
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
        </select>
      </label>
      <label>
        发布后谁可用
        <select key={owner} name="usage_scope_id" defaultValue={edit ? m?.usage_scope_id : owner}>
          <option value={owner}>{scopes.find((s) => s.id === owner)?.label ?? "仅我"}</option>
          {scopes
            .filter(
              (s) => s.share && s.id !== owner && s.kind !== "platform" && s.kind !== "personal",
            )
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
        </select>
      </label>
      {fields.map(([name, label, value]) => (
        <label key={name}>
          {label}
          <input
            name={name}
            defaultValue={
              Array.isArray(initial?.[name] ?? value)
                ? ((initial?.[name] ?? value) as string[]).join("，")
                : String(initial?.[name] ?? value ?? "")
            }
            required={["name", "code", "domain"].includes(name)}
            readOnly={edit && ["code", "domain"].includes(name)}
            autoFocus={name === "name"}
            maxLength={["summary", "formula"].includes(name) ? 2400 : 160}
          />
        </label>
      ))}
      {mode === "receipt" ? (
        <p>来源已关联所选查询回执。期间和地区可填写为参数示例。</p>
      ) : (
        <label>
          登记来源
          <select name="source_id" defaultValue={suggestion?.source_ref ?? m?.source_id ?? ""}>
            <option value="">待补充</option>
            {sources.map((s) => (
              <option key={s.source_id} value={s.source_id}>
                {s.locator}
              </option>
            ))}
          </select>
        </label>
      )}
      <div>
        <button className="ui-button" disabled={busy}>
          保存草稿
        </button>{" "}
        <button className="ui-button" data-variant="secondary" type="button" onClick={cancel}>
          取消
        </button>
      </div>
    </form>
  );
}
