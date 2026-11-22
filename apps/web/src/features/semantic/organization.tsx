"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Building2, LoaderCircle, UsersRound } from "lucide-react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { UiButton } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import type { AssetGrantMode, AssetGrantDirectory } from "@/contracts/semantic";
import { KindBadge } from "./directory";

type Choice = { id: string; name: string; detail?: string; kind?: string };
function Choices({
  title,
  options,
  selected,
  change,
  status,
  filterByKind = false,
}: {
  title: string;
  options: Choice[];
  selected: Set<string>;
  change: (next: Set<string>) => void;
  status?: (id: string) => ReactNode;
  filterByKind?: boolean;
}) {
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const term = search.trim().toLowerCase();
  const shown = options.filter(
    (o) =>
      (!kind || o.kind === kind) && [o.name, o.detail].some((s) => s?.toLowerCase().includes(term)),
  );
  const count = shown.filter((o) => selected.has(o.id)).length;
  const selectAll = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (selectAll.current) selectAll.current.indeterminate = count > 0 && count < shown.length;
  }, [count, shown.length]);
  function select(ids: string[], active: boolean) {
    const next = new Set(selected);
    for (const id of ids) {
      if (active) next.add(id);
      else next.delete(id);
    }
    change(next);
  }
  return (
    <section className="semantic-grant-column" aria-label={title}>
      <header>
        <h3>{title}</h3>
        <span>已选 {selected.size}</span>
      </header>
      <div className="semantic-grant-filters">
        <input
          type="search"
          aria-label={"搜索" + title}
          placeholder={"搜索" + title}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {filterByKind && (
          <select
            aria-label="筛选授权资产类型"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="">全部类型</option>
            <option value="metric">指标</option>
            <option value="entity">实体</option>
          </select>
        )}
      </div>
      <label className="semantic-grant-all">
        <input
          type="checkbox"
          ref={selectAll}
          checked={!!shown.length && count === shown.length}
          disabled={!shown.length}
          onChange={(e) =>
            select(
              shown.map((o) => o.id),
              e.target.checked,
            )
          }
        />
        全选{term || kind ? "搜索结果" : title}（{shown.length}）
      </label>
      <div className="semantic-grant-options">
        {shown.map((o) => (
          <label className="semantic-grant-option" key={o.id}>
            <input
              type="checkbox"
              checked={selected.has(o.id)}
              onChange={(e) => select([o.id], e.target.checked)}
            />
            <span>
              <strong>{o.name}</strong>
              {o.detail && <small>{o.detail}</small>}
              {status?.(o.id)}
            </span>
            {o.kind && <KindBadge kind={o.kind} />}
          </label>
        ))}
        {!shown.length && (
          <p>{term || kind ? "没有匹配项，可调整筛选条件。" : "暂无可选" + title + "。"}</p>
        )}
      </div>
    </section>
  );
}

export function AssetGrantDrawer({
  target,
  busy,
  error,
  close,
  save,
}: {
  target: string;
  busy: boolean;
  error: string;
  close: () => void;
  save: (mode: AssetGrantMode, targets: string[], assets: string[], active: boolean) => void;
}) {
  const api = useDocumentSession();
  const first = useRef<HTMLButtonElement>(null);
  const [data, setData] = useState<AssetGrantDirectory>();
  const [loadError, setLoadError] = useState("");
  const [attempt, retry] = useState(0);
  const [mode, setMode] = useState<AssetGrantMode>("department");
  const [targets, setTargets] = useState(new Set<string>());
  const [assets, setAssets] = useState(new Set<string>());
  useEffect(() => {
    const controller = new AbortController();
    api
      .request<AssetGrantDirectory>("semantic/asset-grants?" + target, "GET", undefined, {
        signal: controller.signal,
        retainOnNotFound: true,
      })
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setLoadError(e.message);
      });
    return () => controller.abort();
  }, [api, target, attempt]);
  const granted = new Map<string, number>();
  for (const grant of data?.grants ?? []) {
    if (grant.mode === mode && targets.has(grant.target_id))
      granted.set(grant.asset_id, (granted.get(grant.asset_id) ?? 0) + 1);
  }
  const existing = [...assets].reduce((count, id) => count + (granted.get(id) ?? 0), 0);
  const combinations = targets.size * assets.size;
  const name = mode === "department" ? "部门" : "成员";
  return (
    <Modal
      open
      placement="right"
      title="资产授权"
      initialFocus={first}
      busy={busy}
      onClose={close}
      description="术语默认加入数据分析上下文。选择部门或成员，管理可使用的指标和实体。"
      footer={
        <>
          {error && (
            <p className="ui-notice semantic-save-error" role="alert" data-tone="error">
              {error}
            </p>
          )}
          <span className="semantic-grant-summary" role="status">
            {busy ? (
              <>
                <LoaderCircle className="ui-spinner" size={16} aria-hidden="true" /> 正在更新授权…
              </>
            ) : (
              `已选 ${targets.size} 个${name}、${assets.size} 项资产`
            )}
          </span>
          <UiButton variant="secondary" onClick={close} disabled={busy}>
            取消
          </UiButton>
          <UiButton
            variant="secondary"
            disabled={busy || !existing}
            onClick={() => save(mode, [...targets], [...assets], false)}
          >
            撤销授权
          </UiButton>
          <UiButton
            disabled={!data || busy || combinations === existing}
            onClick={() => save(mode, [...targets], [...assets], true)}
          >
            授予使用权
          </UiButton>
        </>
      }
    >
      <fieldset disabled={busy} className="semantic-grant-picker">
        <div className="semantic-views" role="group" aria-label="授权方式">
          {(["department", "member"] as const).map((key) => (
            <button
              type="button"
              ref={key === "department" ? first : undefined}
              key={key}
              aria-pressed={mode === key}
              onClick={() => {
                if (key !== mode) {
                  setMode(key);
                  setTargets(new Set());
                  setAssets(new Set());
                }
              }}
            >
              {key === "department" ? <Building2 size={16} /> : <UsersRound size={16} />}
              {key === "department" ? "按部门授权" : "按成员授权"}
            </button>
          ))}
        </div>
        {loadError ? (
          <div role="alert">
            {loadError}{" "}
            <UiButton
              variant="secondary"
              onClick={() => {
                setLoadError("");
                retry((i) => i + 1);
              }}
            >
              重新加载
            </UiButton>
          </div>
        ) : !data ? (
          <p role="status">正在读取授权范围和资产…</p>
        ) : (
          <div className="semantic-grant-columns">
            <Choices
              key={mode}
              title={name}
              options={
                mode === "department"
                  ? data.departments
                  : data.members.map((m) => ({ ...m, detail: m.login }))
              }
              selected={targets}
              change={setTargets}
            />
            <Choices
              title="资产"
              filterByKind
              options={data.assets.map((a) => ({ ...a, detail: a.code }))}
              selected={assets}
              change={setAssets}
              status={(id) => {
                const n = granted.get(id) ?? 0;
                return (
                  n > 0 && (
                    <small className="semantic-grant-status">
                      {n === targets.size ? "已授权" : `已授权 ${n}/${targets.size}`}
                    </small>
                  )
                );
              }}
            />
          </div>
        )}
      </fieldset>
    </Modal>
  );
}
