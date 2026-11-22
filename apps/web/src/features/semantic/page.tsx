"use client";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import type { SessionView } from "@/contracts/page";
import type { MetricView, ScopeView, AssetRelation } from "@/contracts/semantic";
import { AssetDetail } from "./asset-detail";
import { AssetGrantDrawer } from "./organization";
import { MetricForm, formTitles } from "./form";
import { SourceForm } from "./source-form";
import {
  MetricDirectory,
  MetricStatus,
  MetricToolbar,
  emptyFilters,
  matchesFilters,
  KindBadge,
} from "./directory";
import { Modal } from "@/components/ui/dialog";
import { UiButton } from "@/components/ui/button";

import { ArrowLeft, CheckCircle2, CircleAlert, Pencil, Trash2 } from "lucide-react";
import "./semantic.css";

type Source = { source_id: string; locator: string; source_version: string; source_kind: string };
type Detail = MetricView & {
  relations?: AssetRelation[];
};
type Drawer =
  { mode: "new" | "source" | "grants" } | { mode: "preview" | "edit" | "delete"; path: string[] };
export function SemanticPage({ session }: { session: SessionView }) {
  const api = useDocumentSession();
  const target = new URLSearchParams({
    tenantId: session.scope.tenantId!,
    workspaceId: session.scope.workspaceId!,
  }).toString();
  const [items, setItems] = useState<MetricView[]>([]),
    [scopes, setScopes] = useState<ScopeView[]>([]),
    [sources, setSources] = useState<Source[]>([]),
    [personal, setPersonal] = useState(false),
    [configure, setConfigure] = useState(false);
  const formId = useId();
  const firstField = useRef<HTMLInputElement>(null);
  const [filters, setFilters] = useState(emptyFilters);
  const [listError, setListError] = useState("");
  const [readError, setReadError] = useState<{ asset: string; message: string }>();
  const [drawer, setDrawer] = useState<Drawer | null>(() => {
    const asset =
      typeof window !== "undefined" && new URLSearchParams(window.location.search).get("asset");
    return asset ? { mode: "preview", path: [asset] } : null;
  });
  const mode = drawer?.mode;
  const path = drawer && "path" in drawer ? drawer.path : undefined;
  const activeId = path?.at(-1);
  const [rowId, selectRow] = useState("");
  const selectedRow = useRef<HTMLButtonElement>(null);
  const origin = useRef<HTMLElement | null>(null);
  const workspace = useRef<HTMLElement>(null);
  const panelTop = useRef<HTMLDivElement>(null);
  const [editBase, setEditBase] = useState<Detail>();
  const [loaded, show] = useState<Detail>();
  const [notice, notify] = useState("");
  const [error, fail] = useState("");
  const [busy, working] = useState(false);
  const [revision, refresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const detail = loaded?.asset_id === activeId ? loaded : undefined;
  const detailError = readError?.asset === activeId ? readError?.message : "";
  const filtered = items.filter((m) => matchesFilters(m, filters));
  const keyRef = useRef({ body: "", key: "" });
  const read = useCallback(
    <T,>(path: string, signal: AbortSignal) =>
      api.request<T>(
        "semantic/" + path + (path.includes("?") ? "&" : "?") + target,
        "GET",
        undefined,
        { signal, retainOnNotFound: true },
      ),
    [api, target],
  );
  useEffect(() => {
    const ctl = new AbortController();
    let live = true;
    Promise.all([
      read<{ items: MetricView[] }>("assets", ctl.signal),
      read<{ items: ScopeView[]; can_create_personal: boolean; can_configure: boolean }>(
        "scopes",
        ctl.signal,
      ),
      read<{ items: Source[] }>("sources", ctl.signal),
    ])
      .then(([m, s, src]) => {
        if (live) {
          setListError("");
          setItems(m.items);
          setScopes(s.items);
          setPersonal(s.can_create_personal);
          setConfigure(s.can_configure);
          setSources(src.items);

          setLoading(false);
        }
      })
      .catch((e) => {
        if (live) {
          setListError(String(e.message));
          setLoading(false);
        }
      });
    const clear = api.onClear(() => {
      setItems([]);
      setScopes([]);
      setSources([]);
      show(undefined);

      setDrawer(null);
      setEditBase(undefined);
      setPersonal(false);
      setConfigure(false);
      selectRow("");
    });
    return () => {
      live = false;
      ctl.abort();
      clear();
    };
  }, [api, read, revision]);
  useEffect(() => {
    if (!activeId) return;
    const ctl = new AbortController();
    let live = true;
    read<Detail>("assets/" + activeId, ctl.signal)
      .then((data) => {
        if (live) {
          show(data);
          setReadError(undefined);
        }
      })
      .catch((e) => {
        if (live) {
          show(undefined);
          setReadError({ asset: activeId, message: String(e.message) });
        }
      });
    return () => {
      live = false;
      ctl.abort();
    };
  }, [activeId, read, revision]);
  async function write<T>(path: string, data: Record<string, unknown>) {
    const serialized = JSON.stringify([path, data]);
    if (keyRef.current.body !== serialized)
      keyRef.current = { body: serialized, key: crypto.randomUUID() };
    return api.request<T>(
      "semantic/" + path + "?" + target,
      "POST",
      { ...data, request_key: keyRef.current.key },
      { retainOnNotFound: true },
    );
  }
  const perform = async (work: () => Promise<void>) => {
    if (busy) return;
    working(true);
    fail("");
    notify("");
    try {
      await work();
      keyRef.current = { body: "", key: "" };
      refresh((i) => i + 1);
    } catch (e) {
      fail(e instanceof Error ? e.message : "提交失败，请重试。");
    } finally {
      working(false);
    }
  };
  function closeDrawer() {
    setDrawer(null);
    setEditBase(undefined);
    fail("");
    show(undefined);
    setReadError(undefined);
  }
  function preview(ids: string[]) {
    setDrawer({ mode: "preview", path: ids });
    setEditBase(undefined);
    show(undefined);
    setReadError(undefined);
    fail("");
  }
  function start(mode: "new" | "source" | "grants") {
    origin.current = document.activeElement as HTMLElement;
    setDrawer({ mode });
    setEditBase(undefined);
    fail("");
    notify("");
  }
  function setView(view: "preview" | "edit" | "delete") {
    if (!path) return;
    if (view === "edit" && !editBase) setEditBase(detail);
    setDrawer({ mode: view, path });
    fail("");
    notify("");
  }
  useEffect(() => {
    const body = panelTop.current?.closest(".dialog-body");
    body?.scrollTo(0, 0);
    if (mode === "edit") firstField.current?.focus();
  }, [mode, activeId]);
  const returnFocus = () =>
    origin.current?.isConnected
      ? origin.current
      : (selectedRow.current ??
        workspace.current?.querySelector<HTMLElement>(".semantic-search input") ??
        null);
  const title =
    mode === "new" || mode === "source"
      ? formTitles[mode]
      : mode === "delete"
        ? "删除资产"
        : (detail?.name ?? items.find((i) => i.asset_id === activeId)?.name ?? "资产详情");
  const editable = !!(detail?.editable && path);
  return (
    <section ref={workspace} className="semantic-workspace" aria-label="指标口径">
      <MetricToolbar
        items={items}
        scopes={scopes}
        filters={filters}
        change={(next) => {
          setFilters(next);
          selectRow("");
          notify("");
        }}
        canCreate={personal || scopes.some((s) => s.manage)}
        onGrants={configure ? () => start("grants") : undefined}
        onCreate={() => start("new")}
        onSource={() => start("source")}
      />
      {listError && (
        <div className="ui-notice" role="alert" data-tone="error">
          <CircleAlert size={16} aria-hidden="true" />
          {listError}
          <UiButton variant="quiet" onClick={() => refresh((i) => i + 1)}>
            重新加载
          </UiButton>
        </div>
      )}
      {!mode && error && (
        <p className="ui-notice" data-tone="error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          {error}
        </p>
      )}
      {!mode && (notice || busy) && (
        <p className="ui-notice" data-tone={busy ? undefined : "success"} role="status">
          {!busy && <CheckCircle2 size={16} aria-hidden="true" />}
          {busy ? "正在提交…" : notice}
        </p>
      )}
      <MetricDirectory
        scopes={scopes}
        items={filtered}
        loading={loading}
        failed={!!listError}
        activeId={rowId}
        activeRow={selectedRow}
        onSelect={(id, open) => {
          selectRow(id);
          if (open) {
            origin.current = document.querySelector<HTMLButtonElement>(
              `button[data-asset-id="${id}"]`,
            );
            notify("");
            preview([id]);
          }
        }}
        filtered={Object.values(filters).some(Boolean)}
        clear={() => {
          setFilters(emptyFilters);
          selectRow("");
        }}
      />
      {drawer && mode !== "grants" && (
        <Modal
          open
          placement="right"
          title={title}
          initialFocus={mode === "new" || mode === "source" ? firstField : undefined}
          returnFocus={returnFocus}
          busy={busy}
          onClose={closeDrawer}
          footer={
            <>
              {error && (
                <p className="ui-notice semantic-save-error" role="alert" data-tone="error">
                  <CircleAlert size={16} />
                  {error}
                </p>
              )}
              {notice && mode === "preview" && (
                <p className="semantic-receipt" role="status">
                  <CheckCircle2 size={16} />
                  {notice}
                </p>
              )}
              {mode === "delete" && detail ? (
                <>
                  <UiButton variant="secondary" disabled={busy} onClick={() => setView("preview")}>
                    取消
                  </UiButton>
                  <UiButton
                    variant="danger"
                    busy={busy}
                    onClick={() =>
                      void perform(async () => {
                        await write("assets/" + detail.asset_id + "/remove", {
                          expected_digest: detail.digest,
                        });
                        setItems((current) =>
                          current.filter((i) => i.asset_id !== detail.asset_id),
                        );
                        closeDrawer();
                        selectRow("");
                        notify("资产已删除");
                      })
                    }
                  >
                    确认删除
                  </UiButton>
                </>
              ) : mode === "preview" ? (
                <>
                  {editable && (
                    <UiButton
                      variant="quiet"
                      className="semantic-delete"
                      onClick={() => setView("delete")}
                    >
                      <Trash2 size={16} />
                      删除
                    </UiButton>
                  )}
                  <UiButton variant="secondary" onClick={closeDrawer}>
                    完成
                  </UiButton>
                </>
              ) : (
                <>
                  <UiButton
                    variant="secondary"
                    disabled={busy}
                    onClick={() => {
                      if (mode === "edit") {
                        setEditBase(undefined);
                        setView("preview");
                      } else closeDrawer();
                    }}
                  >
                    {mode === "edit" ? "取消编辑" : "取消"}
                  </UiButton>
                  <UiButton form={formId} type="submit" busy={busy}>
                    {busy ? "正在保存…" : mode === "source" ? "保存来源" : "保存并生效"}
                  </UiButton>
                </>
              )}
            </>
          }
        >
          <div ref={panelTop} className="semantic-drawer-content">
            {path && mode !== "delete" && (
              <>
                {path.length > 1 && (
                  <UiButton
                    className="semantic-parent"
                    variant="quiet"
                    onClick={() => preview(path.slice(0, -1))}
                  >
                    <ArrowLeft size={16} />
                    返回{items.find((i) => i.asset_id === path.at(-2))?.name ?? "上一项"}
                  </UiButton>
                )}
                {detail && (
                  <>
                    <div className="semantic-detail-meta">
                      <KindBadge kind={detail.kind} />
                      <MetricStatus state={detail.state} />
                      <code>{detail.code}</code>
                      <span>v{detail.version_no}</span>
                    </div>
                    <div className="semantic-views" role="group" aria-label="详情视图">
                      <button
                        type="button"
                        aria-pressed={mode === "preview"}
                        onClick={() => setView("preview")}
                      >
                        预览
                      </button>
                      {editable && (
                        <button
                          type="button"
                          aria-pressed={mode === "edit"}
                          onClick={() => setView("edit")}
                        >
                          <Pencil size={14} />
                          编辑
                        </button>
                      )}
                    </div>
                  </>
                )}
              </>
            )}
            {path &&
              !detail &&
              (detailError ? (
                <div role="alert">
                  {detailError}
                  <UiButton variant="secondary" onClick={() => refresh((i) => i + 1)}>
                    重新读取
                  </UiButton>
                </div>
              ) : (
                <p role="status">正在读取资产详情…</p>
              ))}
            {mode === "preview" && detail && (
              <article className="semantic-detail" aria-label="资产预览">
                <AssetDetail
                  item={detail}
                  scopes={scopes}
                  open={(id) => {
                    const previous = path!.indexOf(id);
                    if (id !== activeId)
                      preview(previous >= 0 ? path!.slice(0, previous + 1) : [...path!, id]);
                  }}
                />
              </article>
            )}
            {mode === "delete" && detail && (
              <p>确认删除「{detail.name}」？删除后该资产将从目录和经营查询中移除，历史版本保留。</p>
            )}
            {mode === "source" && (
              <SourceForm
                busy={busy}
                id={formId}
                initialFocus={firstField}
                scopes={scopes}
                personal={personal}
                save={(fields) =>
                  void perform(async () => {
                    await write("sources", fields);
                    closeDrawer();
                    notify("来源已登记");
                  })
                }
              />
            )}
            {(mode === "new" || editBase) && (
              <div hidden={mode !== "new" && mode !== "edit"}>
                <MetricForm
                  key={editBase?.version_id ?? "new"}
                  id={formId}
                  initialFocus={firstField}
                  mode={editBase ? "edit" : "new"}
                  defaultKind={filters.kind || "metric"}
                  items={items}
                  detail={editBase}
                  scopes={scopes}
                  personal={personal}
                  sources={sources}
                  busy={busy}
                  save={(fields) =>
                    perform(async () => {
                      const result = await write<{ asset_id: string }>("assets", fields);
                      if (!path) selectRow(result.asset_id);
                      preview(path ?? [result.asset_id]);
                      notify("资产已保存并生效");
                    })
                  }
                />
              </div>
            )}
          </div>
        </Modal>
      )}
      {configure && mode === "grants" && (
        <AssetGrantDrawer
          key={target}
          target={target}
          busy={busy}
          error={error}
          close={closeDrawer}
          save={(mode, target_ids, asset_ids, active) =>
            void perform(async () => {
              await write("asset-grants", { mode, target_ids, asset_ids, active });
              closeDrawer();
              notify(
                `已为 ${target_ids.length} 个${mode === "department" ? "部门" : "成员"}${active ? "授予" : "撤销"} ${asset_ids.length} 项资产的使用权`,
              );
            })
          }
        />
      )}
    </section>
  );
}
