"use client";

import { ArrowRight, CheckCircle2, ChevronRight, CircleAlert, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Dialog } from "radix-ui";
import { ApiError } from "@/client/request-scope";
import type { AnalysisRunScope } from "@/client/analysis-run";
import type { components } from "@/generated/api";
import { AppLink } from "./shell/app-link";
import { NavigationIcon } from "./shell/navigation-icons";
import { useDocumentSession } from "./shell/session-boundary";
import { UiButton } from "./ui/button";
import { UiCodeBlock } from "./ui/code-block";
import { UiDisclosure } from "./ui/disclosure";

type Asset = components["schemas"]["WorkflowAssetView"];
type Assets = components["schemas"]["WorkflowAssetList"];
type Operation =
  { type: "open"; assetId: string } | { type: "rename" | "status" | "more" | "delete" };

function statusLabel(status: Asset["status"]) {
  return status === "active" ? "已启用" : "已停用";
}

export function AnalysisWorkflows({
  scope,
  analysisHref,
}: {
  scope: AnalysisRunScope;
  analysisHref?: string;
}) {
  const api = useDocumentSession();
  const [items, setItems] = useState<Asset[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<Asset | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [operation, setOperation] = useState<Operation | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [retry, setRetry] = useState(0);
  const { tenantId, workspaceId } = scope;
  const detailTitle = useRef<HTMLHeadingElement>(null);
  const listTitle = useRef<HTMLHeadingElement>(null);
  const deleteButton = useRef<HTMLButtonElement>(null);
  const cancelDelete = useRef<HTMLButtonElement>(null);
  const id = useId();
  const selectedId = selected?.assetId;

  useEffect(() => {
    if (selectedId) detailTitle.current?.focus();
  }, [selectedId]);

  const path = useCallback(
    (id?: string, cursor?: string) => {
      const query = new URLSearchParams({ tenantId, workspaceId });
      if (cursor) query.set("cursor", cursor);
      return `analysis/workflows${id ? `/${id}` : ""}?${query}`;
    },
    [tenantId, workspaceId],
  );

  const readPage = useCallback(
    (signal?: AbortSignal, cursor?: string) =>
      api.request<Assets>(path(undefined, cursor), "GET", undefined, {
        retainOnNotFound: true,
        signal,
      }),
    [api, path],
  );
  const showPage = useCallback((response: Assets, append = false) => {
    setItems((old) => {
      if (!append || !old) return response.items;
      const existing = new Set(old.map((item) => item.assetId));
      return [...old, ...response.items.filter((item) => !existing.has(item.assetId))];
    });
    setNextCursor(response.nextCursor ?? null);
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    void readPage(abort.signal)
      .then((response) => {
        if (!abort.signal.aborted) showPage(response);
      })
      .catch((failure: unknown) => {
        if (!abort.signal.aborted)
          setError(
            failure instanceof ApiError && failure.status === 404
              ? "当前环境尚未开放工作流。"
              : "工作流暂时无法加载。",
          );
      });
    return () => abort.abort();
  }, [readPage, showPage, retry]);

  useEffect(
    () =>
      api.onClear(() => {
        setItems(null);
        setNextCursor(null);
        setSelected(null);
        setName("");
        setError("");
        setFeedback("");
        setOperation(null);
        setConfirmDelete(false);
        setDeleteError("");
      }),
    [api],
  );

  if (items === null) {
    return (
      <div className="ui-panel workflow-library-state">
        {error ? (
          <>
            <p className="ui-notice" data-tone="error" role="alert">
              <CircleAlert size={17} aria-hidden="true" /> {error}
            </p>
            <div className="ui-actions">
              <UiButton
                variant="secondary"
                onClick={() => {
                  setError("");
                  setRetry((value) => value + 1);
                }}
              >
                重新加载
              </UiButton>
            </div>
          </>
        ) : (
          <p className="ui-notice" role="status">
            <LoaderCircle className="ui-spinner" size={17} aria-hidden="true" /> 正在加载工作流…
          </p>
        )}
      </div>
    );
  }

  async function open(id: string) {
    setOperation({ type: "open", assetId: id });
    setError("");
    setFeedback("");
    try {
      const asset = await api.request<Asset>(path(id), "GET", undefined, {
        retainOnNotFound: true,
      });
      setSelected(asset);
      setName(asset.name);
    } catch {
      setError("无法打开此工作流，请重试。");
    } finally {
      setOperation(null);
    }
  }

  async function patch(action: "rename" | "status") {
    if (!selected) return;
    const nextName = action === "rename" ? name.trim() : selected.name;
    const status =
      action === "rename" ? selected.status : selected.status === "active" ? "disabled" : "active";
    setOperation({ type: action });
    setError("");
    setFeedback("");
    try {
      const asset = await api.request<Asset>(
        path(selected.assetId),
        "PATCH",
        {
          status,
          name: nextName,
          metadataRevision: selected.metadataRevision,
        },
        { retainOnNotFound: true },
      );
      setSelected({ ...asset, scriptSource: selected.scriptSource });
      if (action === "rename") setName(asset.name);
      setFeedback(
        action === "rename" ? "工作流名称已更新。" : `工作流${statusLabel(asset.status)}。`,
      );
      setItems((old) =>
        old ? [asset, ...old.filter((item) => item.assetId !== asset.assetId)] : old,
      );
      try {
        showPage(await readPage());
      } catch {
        setError("列表暂时无法刷新。");
      }
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "工作流修改失败。");
    } finally {
      setOperation(null);
    }
  }

  async function loadMore(cursor: string) {
    setOperation({ type: "more" });
    setError("");
    try {
      showPage(await readPage(undefined, cursor), true);
    } catch {
      setError("无法加载更多工作流，请重试。");
    } finally {
      setOperation(null);
    }
  }

  async function remove() {
    if (!selected || operation) return;
    setOperation({ type: "delete" });
    setDeleteError("");
    setError("");
    setFeedback("");
    try {
      await api.request<void>(
        path(selected.assetId),
        "DELETE",
        { metadataRevision: selected.metadataRevision },
        { retainOnNotFound: true },
      );
      setItems((old) => old?.filter((item) => item.assetId !== selected.assetId) ?? null);
      setSelected(null);
      setName("");
      setConfirmDelete(false);
      setFeedback("工作流已删除。");
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "version_conflict") {
        try {
          const asset = await api.request<Asset>(path(selected.assetId), "GET", undefined, {
            retainOnNotFound: true,
          });
          setSelected(asset);
          setName(asset.name);
          setItems(
            (old) => old?.map((item) => (item.assetId === asset.assetId ? asset : item)) ?? null,
          );
          setDeleteError("工作流已更新，请核对后重新确认删除。");
        } catch {
          setDeleteError("无法读取最新工作流，请关闭弹窗后重新打开工作流。");
        }
      } else {
        setDeleteError(failure instanceof ApiError ? failure.message : "工作流删除失败，请重试。");
      }
    } finally {
      setOperation(null);
    }
  }

  const deleting = operation?.type === "delete";
  return (
    <div className="workflow-library-content">
      {error && (
        <p className="ui-notice" data-tone="error" role="alert">
          <CircleAlert size={17} aria-hidden="true" /> {error}
        </p>
      )}
      {feedback && (
        <p className="ui-notice" data-tone="success" role="status">
          <CheckCircle2 size={17} aria-hidden="true" /> {feedback}
        </p>
      )}
      <div className="workflow-library-layout" data-has-items={items.length > 0}>
        <section className="ui-panel workflow-library-list" aria-labelledby={`${id}-list`}>
          <h2 ref={listTitle} id={`${id}-list`} tabIndex={-1}>
            已保存工作流
          </h2>
          {items.length === 0 && !nextCursor ? (
            <div className="workflow-library-empty">
              <span className="workflow-library-icon">
                <NavigationIcon name="workflow" size={20} />
              </span>
              <div>
                <p>还没有保存的工作流。</p>
                <p className="ui-panel-description">在分析对话中确认保存后，工作流会出现在这里。</p>
              </div>
              {analysisHref && (
                <AppLink className="ui-link-button" href={analysisHref}>
                  前往分析 <ArrowRight size={16} aria-hidden="true" />
                </AppLink>
              )}
            </div>
          ) : (
            <ul aria-labelledby={`${id}-list`}>
              {items.map((item) => {
                const opening = operation?.type === "open" && operation.assetId === item.assetId;
                const label = statusLabel(item.status);
                return (
                  <li key={item.assetId}>
                    <UiButton
                      className="workflow-library-row"
                      variant="quiet"
                      aria-label={`${item.name} · v${item.definitionVersion} · ${label}`}
                      aria-current={selectedId === item.assetId ? "true" : undefined}
                      busy={opening}
                      disabled={Boolean(operation)}
                      onClick={() => void open(item.assetId)}
                    >
                      {!opening && <NavigationIcon name="workflow" size={17} />}
                      <span className="workflow-library-row-title">
                        <strong>{item.name}</strong>
                        <span className="workflow-library-version">v{item.definitionVersion}</span>
                      </span>
                      <span className="workflow-library-status" data-status={item.status}>
                        {label}
                      </span>
                      <ChevronRight
                        size={15}
                        className="workflow-library-chevron"
                        aria-hidden="true"
                      />
                    </UiButton>
                  </li>
                );
              })}
            </ul>
          )}
          {nextCursor && (
            <UiButton
              variant="secondary"
              busy={operation?.type === "more"}
              disabled={Boolean(operation)}
              onClick={() => void loadMore(nextCursor)}
            >
              加载更多
            </UiButton>
          )}
        </section>
        {selected ? (
          <section className="ui-panel analysis-workflow-detail" aria-labelledby={`${id}-detail`}>
            <header className="workflow-library-detail-header">
              <h2 ref={detailTitle} id={`${id}-detail`} tabIndex={-1}>
                {selected.name}
              </h2>
              <span className="workflow-library-status" data-status={selected.status}>
                {statusLabel(selected.status)}
              </span>
            </header>
            <dl className="workflow-library-metadata">
              <div>
                <dt>脚本版本</dt>
                <dd>v{selected.definitionVersion}</dd>
              </div>
              <div>
                <dt>入口契约</dt>
                <dd>
                  <code>{selected.entryContract}</code>
                </dd>
              </div>
            </dl>
            {selected.description && <p className="ui-panel-description">{selected.description}</p>}
            {selected.parameters && (
              <UiDisclosure label="查看工具参数">
                <UiCodeBlock
                  title="函数参数契约"
                  language="JSON"
                  code={JSON.stringify(selected.parameters, null, 2)}
                />
              </UiDisclosure>
            )}
            {selected.scriptSource && (
              <UiDisclosure key={selected.assetId} label="查看脚本">
                <UiCodeBlock title="编排脚本" language="JavaScript" code={selected.scriptSource} />
              </UiDisclosure>
            )}
            <form
              className="workflow-library-manage"
              onSubmit={(event) => {
                event.preventDefault();
                if (!operation && name.trim() && name.trim() !== selected.name)
                  void patch("rename");
              }}
            >
              <div className="ui-field">
                <label htmlFor={`${id}-name`}>工作流名称</label>
                <input
                  className="ui-input"
                  id={`${id}-name`}
                  value={name}
                  maxLength={80}
                  readOnly={Boolean(operation)}
                  required
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div className="ui-actions">
                <UiButton
                  type="submit"
                  variant="secondary"
                  busy={operation?.type === "rename"}
                  disabled={Boolean(operation) || !name.trim() || name.trim() === selected.name}
                >
                  改名
                </UiButton>
                <UiButton
                  variant="quiet"
                  busy={operation?.type === "status"}
                  disabled={Boolean(operation)}
                  onClick={() => void patch("status")}
                >
                  {selected.status === "active" ? "停用" : "重新启用"}
                </UiButton>
                <button
                  type="button"
                  ref={deleteButton}
                  className="ui-button"
                  data-variant="danger"
                  disabled={Boolean(operation)}
                  onClick={() => {
                    setDeleteError("");
                    setConfirmDelete(true);
                  }}
                >
                  删除
                </button>
              </div>
            </form>
            <div className="analysis-workflow-reuse">
              <p className="ui-notice">
                {selected.entryContract === "agent_workflow@1"
                  ? selected.status === "active"
                    ? "已启用。可以在 Chat 中直接提出相关分析问题。"
                    : "重新启用后将加入个人工具清单。"
                  : "此资产使用旧版零售执行契约，当前保留查看和导出。"}
              </p>
              <UiButton
                variant="secondary"
                onClick={() => {
                  const url = URL.createObjectURL(
                    new Blob([selected.scriptSource ?? ""], {
                      type: "text/javascript;charset=utf-8",
                    }),
                  );
                  const link = document.createElement("a");
                  link.href = url;
                  link.download = `workflow-${selected.assetId}.js`;
                  link.click();
                  URL.revokeObjectURL(url);
                }}
              >
                导出脚本
              </UiButton>
            </div>
          </section>
        ) : items.length > 0 ? (
          <div className="workflow-library-placeholder">
            <span className="workflow-library-icon">
              <NavigationIcon name="workflow" size={20} />
            </span>
            <p>选择一条工作流查看详情</p>
          </div>
        ) : null}
      </div>
      <Dialog.Root
        open={confirmDelete}
        onOpenChange={(open) => !deleting && setConfirmDelete(open)}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content
            className="dialog"
            data-sensitive-portal
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              cancelDelete.current?.focus();
            }}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              (deleteButton.current ?? listTitle.current)?.focus();
            }}
          >
            <div className="dialog-head">
              <div>
                <Dialog.Title>删除工作流</Dialog.Title>
                <Dialog.Description>
                  删除「{selected?.name}」后，模型将无法再调用此工作流。历史执行记录会保留。
                </Dialog.Description>
              </div>
            </div>
            <div className="dialog-body">
              {deleteError && (
                <p className="ui-notice" data-tone="error" role="alert">
                  {deleteError}
                </p>
              )}
              <div className="ui-actions">
                <Dialog.Close asChild>
                  <button
                    type="button"
                    ref={cancelDelete}
                    className="ui-button"
                    data-variant="secondary"
                    disabled={deleting}
                  >
                    取消
                  </button>
                </Dialog.Close>
                <UiButton busy={deleting} variant="danger" onClick={() => void remove()}>
                  {deleting ? "正在删除…" : "确认删除"}
                </UiButton>
              </div>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
