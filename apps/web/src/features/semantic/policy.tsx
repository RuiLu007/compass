"use client";
import { useEffect, useState } from "react";
import type { ScopeView, MetricView, SemanticCapability } from "@/contracts/semantic";
import { useDocumentSession } from "@/components/shell/session-boundary";

type Policy = {
  pinned_asset_ids: string[];
  defaults: { term_ref: string; target_metric_ref: string }[];
  context_max_items: number;
  page_size: number;
  max_search_rounds: number;
  disabled_capabilities?: SemanticCapability[];
};
const initial: Policy = {
  pinned_asset_ids: [],
  defaults: [],
  context_max_items: 8,
  page_size: 12,
  max_search_rounds: 8,
};
export function PolicyEditor({
  target,
  scopes,
  items,
  sources,
  revision,
  save,
}: {
  target: string;
  scopes: ScopeView[];
  items: MetricView[];
  sources: { source_id: string; locator: string }[];
  revision: number;
  save: (v: Record<string, unknown>) => Promise<void>;
}) {
  const api = useDocumentSession(),
    [id, setId] = useState(""),
    [generation, gen] = useState(0),
    [source, selectSource] = useState(""),
    [policy, edit] = useState<Policy>(initial);
  const [busy, load] = useState(false),
    [error, fail] = useState("");
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    let live = true;
    api
      .request<{ generation: number; policy: Partial<Policy> | null; source_id: string | null }>(
        "semantic/policies/" + id + "?" + target,
        "GET",
        undefined,
        { signal: controller.signal, retainOnNotFound: true },
      )
      .then((v) => {
        if (live) {
          gen(v.generation);
          edit({
            ...initial,
            ...v.policy,
            defaults: (v.policy?.defaults ?? []).map(({ term_ref, target_metric_ref }) => ({
              term_ref,
              target_metric_ref,
            })),
          });
          selectSource(v.source_id ?? "");
          load(false);
        }
      })
      .catch((e) => {
        if (live) {
          fail(e.message);
          load(false);
        }
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [api, id, target, revision]);
  const published = items.filter((v) => v.state === "published");
  return (
    <details className="ui-panel">
      <summary>默认口径与检索预算</summary>
      <form
        className="semantic-form"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          void save({
            scope_id: id,
            expected_generation: generation,
            source_id: source,
            policy,
            note: f.get("note"),
          });
        }}
      >
        <label>
          策略范围
          <select
            value={id}
            required
            onChange={(e) => {
              setId(e.target.value);
              load(!!e.target.value);
              fail("");
            }}
          >
            <option value="">选择生效范围</option>
            {scopes
              .filter((s) => s.release)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
          </select>
        </label>
        {id && !busy && !error && (
          <>
            <label>
              策略来源
              <select value={source} required onChange={(e) => selectSource(e.target.value)}>
                <option value="">选择来源</option>
                {sources.map((s) => (
                  <option key={s.source_id} value={s.source_id}>
                    {s.locator}
                  </option>
                ))}
              </select>
            </label>
            <label>
              首屏置顶项
              <select
                multiple
                value={policy.pinned_asset_ids}
                onChange={(e) =>
                  edit({
                    ...policy,
                    pinned_asset_ids: Array.from(e.target.selectedOptions, (x) => x.value),
                  })
                }
              >
                {published.map((i) => (
                  <option key={i.asset_id} value={i.asset_id}>
                    {i.name}
                  </option>
                ))}
              </select>
            </label>
            {policy.defaults.map((d, i) => (
              <fieldset key={i}>
                <legend>默认映射 {i + 1}</legend>
                {(["term_ref", "target_metric_ref"] as const).map((k) => (
                  <label key={k}>
                    {k === "term_ref" ? "术语" : "默认指标"}
                    <select
                      required
                      value={d[k]}
                      onChange={(e) =>
                        edit({
                          ...policy,
                          defaults: policy.defaults.map((a, j) =>
                            i === j ? { ...a, [k]: e.target.value } : a,
                          ),
                        })
                      }
                    >
                      <option value="">请选择</option>
                      {published
                        .filter((a) => a.kind === (k === "term_ref" ? "term" : "metric"))
                        .map((a) => (
                          <option key={a.asset_id} value={a.asset_id}>
                            {a.name}
                          </option>
                        ))}
                    </select>
                  </label>
                ))}
                <button
                  type="button"
                  className="ui-button"
                  data-variant="secondary"
                  onClick={() =>
                    edit({ ...policy, defaults: policy.defaults.filter((_, j) => j !== i) })
                  }
                >
                  移除映射
                </button>
              </fieldset>
            ))}
            <button
              type="button"
              className="ui-button"
              data-variant="secondary"
              onClick={() =>
                edit({
                  ...policy,
                  defaults: [...policy.defaults, { term_ref: "", target_metric_ref: "" }],
                })
              }
            >
              添加默认映射
            </button>
            {(["context_max_items", "page_size", "max_search_rounds"] as const).map((k, i) => (
              <label key={k}>
                {["首屏条数", "搜索每页条数", "最大检索轮数"][i]}
                <input
                  type="number"
                  required
                  min={i === 0 ? 0 : 1}
                  max={i === 1 ? 50 : 16}
                  value={policy[k]}
                  onChange={(e) => edit({ ...policy, [k]: Number(e.target.value) })}
                />
              </label>
            ))}
            <fieldset>
              <legend>在此范围停用的 Chat 能力</legend>
              {(["read", "query", "draft", "govern"] as const).map((key) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={policy.disabled_capabilities?.includes(key) ?? false}
                    onChange={(e) => {
                      const next = new Set(policy.disabled_capabilities);
                      if (e.target.checked) next.add(key);
                      else next.delete(key);
                      edit({ ...policy, disabled_capabilities: [...next] });
                    }}
                  />
                  {{ read: "读取定义", query: "正式查询", draft: "草稿提议", govern: "送审" }[key]}
                </label>
              ))}
            </fieldset>
            <label>
              变更理由
              <input name="note" required maxLength={160} />
            </label>
            <button className="ui-button">发布策略</button>
          </>
        )}
        {error && <p role="alert">{error}</p>}
      </form>
    </details>
  );
}
