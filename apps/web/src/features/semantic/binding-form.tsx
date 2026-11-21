"use client";
import { useState } from "react";
import type { MetricView } from "@/contracts/semantic";

type Save = (path: string, fields: Record<string, unknown>) => Promise<void>;
export function BindingForm({
  asset,
  items,
  binding,
  save,
}: {
  asset: MetricView;
  items: MetricView[];
  binding: string;
  save: Save;
}) {
  const [mode, setMode] = useState("reuse"),
    [error, fail] = useState("");
  const candidates = items.filter(
    (v) => v.binding_id && v.grain === asset.grain && v.unit === asset.unit,
  );
  return (
    <details>
      <summary>执行方式与试算</summary>
      {asset.editable && (
        <form
          className="semantic-form"
          onSubmit={(e) => {
            e.preventDefault();
            fail("");
            const f = Object.fromEntries(new FormData(e.currentTarget));
            try {
              const fields =
                mode === "reuse"
                  ? { reuse_binding_id: f.template }
                  : {
                      mapping: {
                        connection_id: f.connection,
                        schema: f.schema,
                        relation: f.relation,
                        measure: f.measure,
                        event_time: f.time,
                        available_at: f.available,
                        aggregate: f.aggregate,
                        dimensions: JSON.parse(String(f.dimensions)),
                        filters: JSON.parse(String(f.filters)),
                        row_policies: String(f.policies)
                          .split("\n")
                          .map((x) => x.trim())
                          .filter(Boolean),
                        source_release: f.release,
                      },
                    };
              void save("bindings", {
                ...fields,
                version_id: asset.version_id,
                expected_digest: asset.digest,
              });
            } catch {
              fail("维度和筛选映射需填写 JSON 对象。");
            }
          }}
        >
          <label>
            执行方式
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="reuse">复用已发布的受控查询</option>
              <option value="aggregate">登记受管表聚合</option>
            </select>
          </label>
          {mode === "reuse" ? (
            <label>
              执行模板
              <select name="template" required defaultValue="">
                <option value="">选择相同单位和粒度的模板</option>
                {candidates.map((v) => (
                  <option key={v.binding_id} value={v.binding_id!}>
                    {v.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              {Object.entries({
                connection: "受管连接 ID",
                schema: "数据库 Schema",
                relation: "表名",
                measure: "指标列",
                time: "业务时间列",
                available: "数据可见时间列",
                release: "数据版本",
              }).map(([k, label]) => (
                <label key={k}>
                  {label}
                  <input name={k} required />
                </label>
              ))}
              <label>
                汇总规则
                <select name="aggregate">
                  {["sum", "count", "min", "max", "avg"].map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </label>
              <label>
                维度到列的映射
                <input name="dimensions" defaultValue="{}" />
              </label>
              <label>
                筛选到列的映射
                <input name="filters" defaultValue="{}" />
              </label>
              <label>
                获准读取的行策略表达式，每行一项
                <textarea name="policies" required />
              </label>
            </>
          )}
          <button className="ui-button">登记不可变绑定</button>
        </form>
      )}
      {binding && (
        <form
          className="semantic-form"
          onSubmit={(e) => {
            e.preventDefault();
            const f = Object.fromEntries(new FormData(e.currentTarget));
            fail("");
            try {
              void save("trial", {
                binding_id: binding,
                version_id: asset.version_id,
                parameters: {
                  start: f.start,
                  end: f.end,
                  as_of: new Date(String(f.cutoff)).toISOString(),
                },
                expected: JSON.parse(String(f.expected)),
              });
            } catch {
              fail("请检查可见截止和独立预期行的格式。");
            }
          }}
        >
          <label>
            开始日
            <input type="date" name="start" required />
          </label>
          <label>
            结束日（不含）
            <input type="date" name="end" required />
          </label>
          <label>
            数据可见截止
            <input type="datetime-local" name="cutoff" required />
          </label>
          <label>
            独立预期行
            <textarea
              name="expected"
              required
              placeholder={'[{"group_key":"total","status":"OK","value":"42"}]'}
            />
          </label>
          <button className="ui-button" data-variant="secondary">
            执行并核对试算
          </button>
        </form>
      )}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
