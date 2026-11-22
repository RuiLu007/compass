import { useState } from "react";
import { UiButton } from "@/components/ui/button";
import type { AssetRelation, MetricView } from "@/contracts/semantic";

type Link = {
  target_id: string;
  local?: string[];
  remote?: string[];
  cardinality?: string;
  nullable?: boolean;
  validity?: string;
  use?: string;
};
const parts = (v: string) =>
  v
    .split(/[,，、]/)
    .map((x) => x.trim())
    .filter(Boolean);
export function RelationFields({
  kind,
  items,
  detail,
}: {
  kind: string;
  items: MetricView[];
  detail?: MetricView & { relations?: AssetRelation[] };
}) {
  const [rows, setRows] = useState(() =>
    (detail?.relations ?? [])
      .filter((r) => r.editable && r.kind === (kind === "term" ? "denotes" : "entity_join"))
      .map((r, i) => ({
        key: r.target_id + "-" + i,
        link: { ...r.rule, target_id: r.target_id } as Link,
      })),
  );
  const options = items.filter(
    (i) => i.state === "published" && (kind === "term" ? i.kind !== "term" : i.kind === "entity"),
  );
  function patch(key: string, change: Partial<Link>) {
    setRows(rows.map((x) => (x.key === key ? { ...x, link: { ...x.link, ...change } } : x)));
  }
  return (
    <fieldset>
      <legend>{kind === "term" ? "指向的指标或实体" : "实体关系"}</legend>
      <input
        type="hidden"
        name="links"
        value={JSON.stringify(
          rows.map(({ link }) => ({
            target_id: link.target_id,
            ...(kind === "entity"
              ? {
                  local: link.local,
                  remote: link.remote,
                  cardinality: link.cardinality,
                  nullable: link.nullable,
                  validity: link.validity,
                  use: link.use,
                }
              : {}),
          })),
        )}
      />
      {rows.map(({ key, link }, index) => (
        <div className="semantic-field-grid semantic-edit-link" key={key}>
          <label>
            关联对象 {index + 1}
            <select
              required
              value={link.target_id}
              onChange={(e) => patch(key, { target_id: e.target.value })}
            >
              <option value="">选择资产</option>
              {options.map((i) => (
                <option key={i.asset_id} value={i.asset_id}>
                  {i.name} · {i.code}
                </option>
              ))}
            </select>
          </label>
          {kind === "entity" && (
            <>
              <label>
                关系基数
                <select
                  value={link.cardinality}
                  onChange={(e) => patch(key, { cardinality: e.target.value })}
                >
                  {Array.from(new Set([link.cardinality || "N:1", "N:1", "1:1", "1:N", "N:N"])).map(
                    (v) => (
                      <option key={v}>{v}</option>
                    ),
                  )}
                </select>
              </label>
              {(["local", "remote"] as const).map((k, i) => (
                <label key={k}>
                  {["本端连接键", "对端连接键"][i]}
                  <input
                    required
                    defaultValue={link[k]?.join(", ")}
                    placeholder="多个键以逗号分隔"
                    onChange={(e) => patch(key, { [k]: parts(e.target.value) })}
                  />
                </label>
              ))}
              <label>
                关联用途
                <input
                  required
                  value={link.use ?? ""}
                  onChange={(e) => patch(key, { use: e.target.value })}
                />
              </label>
              <label>
                有效期规则
                <input
                  value={link.validity ?? ""}
                  onChange={(e) => patch(key, { validity: e.target.value })}
                />
              </label>
              <label>
                匹配要求
                <select
                  value={link.nullable ? "optional" : "required"}
                  onChange={(e) => patch(key, { nullable: e.target.value === "optional" })}
                >
                  <option value="required">必须匹配</option>
                  <option value="optional">允许空关联</option>
                </select>
              </label>
            </>
          )}
          <UiButton variant="quiet" onClick={() => setRows(rows.filter((r) => r.key !== key))}>
            移除此关系
          </UiButton>
        </div>
      ))}
      <UiButton
        variant="secondary"
        onClick={() =>
          setRows([
            ...rows,
            { key: crypto.randomUUID(), link: { target_id: "", cardinality: "N:1" } },
          ])
        }
      >
        添加关联对象
      </UiButton>
    </fieldset>
  );
}
