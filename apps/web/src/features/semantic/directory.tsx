import { useEffect, type RefObject } from "react";
import {
  BookOpen,
  Box,
  ChartColumn,
  FilePlus2,
  ListFilter,
  Plus,
  Search,
  UsersRound,
} from "lucide-react";
import { UiButton } from "@/components/ui/button";
import type { MetricView, ScopeView } from "@/contracts/semantic";

export function domainName(code: string) {
  const domains: Record<string, string> = {
    organization: "组织",
    product: "商品",
    sales: "销售",
    finance: "财务",
    marketing: "营销",
    inventory: "库存",
    channel: "渠道",
    quality: "数据质量",
    fulfillment: "履约",
    customer: "客户",
    trade: "交易",
  };
  return domains[code] ?? code;
}
export const emptyFilters = { kind: "", q: "", domain: "", owner: "", state: "" };
type Filters = typeof emptyFilters;

export function matchesFilters(m: MetricView, filters: Filters) {
  const query = filters.q.trim().toLowerCase();
  return (
    (!query || [m.name, m.code, ...m.aliases].some((s) => s.toLowerCase().includes(query))) &&
    (!filters.kind || filters.kind === m.kind) &&
    (!filters.domain || m.domain === filters.domain) &&
    (!filters.owner || m.owner_scope_id === filters.owner) &&
    (!filters.state || (m.state === "published" ? "published" : "inactive") === filters.state)
  );
}

export function MetricToolbar({
  items,
  scopes,
  filters,
  change,
  canCreate,
  onCreate,
  onSource,
  onGrants,
}: {
  items: MetricView[];
  scopes: ScopeView[];
  filters: Filters;
  change: (next: Filters) => void;
  canCreate: boolean;
  onCreate: () => void;
  onSource: () => void;
  onGrants?: () => void;
}) {
  return (
    <div className="semantic-toolbar">
      <div className="semantic-filters" role="search" aria-label="筛选资产">
        <div className="semantic-search">
          <Search size={16} aria-hidden="true" />
          <input
            type="search"
            aria-label="搜索资产"
            placeholder="搜索名称、代码或别名"
            value={filters.q}
            onChange={(e) => change({ ...filters, q: e.target.value })}
          />
        </div>
        <select
          aria-label="资产类型"
          value={filters.kind}
          onChange={(e) => change({ ...filters, kind: e.target.value })}
        >
          <option value="">全部类型</option>
          <option value="entity">实体</option>
          <option value="term">术语</option>
          <option value="metric">指标</option>
        </select>
        <select
          aria-label="业务域"
          value={filters.domain}
          onChange={(e) => change({ ...filters, domain: e.target.value })}
        >
          <option value="">全部业务域</option>
          {[...new Set(items.map((m) => m.domain))]
            .sort((a, b) => a.localeCompare(b, "zh"))
            .map((d) => (
              <option key={d} value={d}>
                {domainName(d)}
              </option>
            ))}
        </select>
        <select
          aria-label="维护归属"
          value={filters.owner}
          onChange={(e) => change({ ...filters, owner: e.target.value })}
        >
          <option value="">全部归属</option>
          {scopes.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        <select
          aria-label="发布状态"
          value={filters.state}
          onChange={(e) => change({ ...filters, state: e.target.value })}
        >
          <option value="">全部状态</option>
          {Object.entries({ published: "已生效", inactive: "未生效" }).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {(canCreate || onGrants) && (
        <div className="semantic-toolbar-actions">
          {canCreate && (
            <>
              <UiButton variant="secondary" aria-haspopup="dialog" onClick={onSource}>
                <FilePlus2 size={16} aria-hidden="true" />
                登记来源
              </UiButton>
              <UiButton aria-haspopup="dialog" onClick={onCreate}>
                <Plus size={16} aria-hidden="true" />
                新建资产
              </UiButton>
            </>
          )}
          {onGrants && (
            <UiButton variant="secondary" aria-haspopup="dialog" onClick={onGrants}>
              <UsersRound size={16} aria-hidden="true" />
              资产授权
            </UiButton>
          )}
        </div>
      )}
    </div>
  );
}

export function MetricStatus({ state }: { state: string }) {
  return (
    <span className="semantic-status" data-state={state}>
      {state === "published" ? "已生效" : "未生效"}
    </span>
  );
}

function kindInfo(kind: string) {
  if (kind === "entity") return { Icon: Box, label: "实体" };
  if (kind === "term") return { Icon: BookOpen, label: "术语" };
  return { Icon: ChartColumn, label: "指标" };
}

export function KindBadge({ kind }: { kind: string }) {
  const { Icon, label } = kindInfo(kind);
  return (
    <span className="semantic-kind" data-kind={kind}>
      <Icon size={15} aria-hidden="true" />
      {label}
    </span>
  );
}
export function MetricDirectory({
  items,
  activeId,
  onSelect,
  activeRow,
  scopes,
  filtered,
  clear,
  loading,
  failed,
}: {
  items: MetricView[];
  activeId: string;
  onSelect: (id: string, showDetail: boolean) => void;
  scopes: ScopeView[];
  activeRow: RefObject<HTMLButtonElement | null>;
  filtered: boolean;
  clear: () => void;
  loading: boolean;
  failed: boolean;
}) {
  const activeIndex = items.findIndex((m) => m.asset_id === activeId);
  useEffect(() => {
    const row = activeRow.current;
    const list = row?.closest(".semantic-table-scroll");
    if (!row || !list) return;
    const item = row.getBoundingClientRect();
    const bounds = list.getBoundingClientRect();
    if (item.top < bounds.top) list.scrollTop += item.top - bounds.top;
    else if (item.bottom > bounds.bottom) list.scrollTop += item.bottom - bounds.bottom;
  }, [activeId, activeIndex, activeRow]);
  return (
    <section className="ui-panel semantic-directory" aria-label="资产列表">
      <div className="semantic-directory-head">
        <h2>资产列表</h2>
        {!loading && !failed && (
          <span className="semantic-count" role="status">
            {items.length} 项
          </span>
        )}
        {filtered && (
          <UiButton variant="quiet" onClick={clear}>
            清除筛选
          </UiButton>
        )}
      </div>
      {items.length ? (
        <div className="semantic-table-scroll semantic-directory-scroll">
          <table className="semantic-catalog" aria-label="语义资产目录">
            <thead>
              <tr>
                {["名称 / 代码", "类型", "业务域", "维护归属", "状态", "版本"].map((t) => (
                  <th key={t} scope="col">
                    {t}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((m, index) => (
                <tr
                  key={m.asset_id}
                  data-selected={activeId === m.asset_id || undefined}
                  onClick={() => onSelect(m.asset_id, true)}
                >
                  <td>
                    <button
                      type="button"
                      data-asset-id={m.asset_id}
                      aria-haspopup="dialog"
                      className="semantic-asset-name"
                      ref={activeId === m.asset_id ? activeRow : undefined}
                      aria-pressed={activeId === m.asset_id}
                      tabIndex={index === Math.max(0, activeIndex) ? 0 : -1}
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelect(m.asset_id, true);
                      }}
                      onKeyDown={(e) => {
                        const next = {
                          ArrowUp: Math.max(0, index - 1),
                          ArrowDown: Math.min(items.length - 1, index + 1),
                          Home: 0,
                          End: items.length - 1,
                        }[e.key];
                        if (next === undefined) return;
                        e.preventDefault();
                        e.currentTarget.closest("tbody")?.querySelectorAll("button")[next]?.focus();
                        onSelect(items[next].asset_id, false);
                      }}
                    >
                      <strong>{m.name}</strong>
                      <small>{m.code}</small>
                    </button>
                  </td>
                  <td>
                    <KindBadge kind={m.kind} />
                  </td>
                  <td>{domainName(m.domain)}</td>
                  <td>{scopes.find((s) => s.id === m.owner_scope_id)?.label ?? "受限范围"}</td>
                  <td>
                    <MetricStatus state={m.state} />
                  </td>
                  <td>v{m.version_no}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="semantic-list-empty">
          <ListFilter size={22} aria-hidden="true" />
          <p>
            {loading
              ? "正在加载资产…"
              : failed
                ? "资产列表加载失败"
                : filtered
                  ? "没有符合筛选条件的资产"
                  : "当前范围暂无资产"}
          </p>
        </div>
      )}
    </section>
  );
}
