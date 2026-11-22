"use client";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type PointerEvent,
} from "react";
import { Ellipsis, MessageSquare, Pencil, Trash2, Workflow } from "lucide-react";
import { Dialog, DropdownMenu } from "radix-ui";
import { ApiError } from "@/client/request-scope";
import { sessionsChanged, type SessionListItem } from "@/client/analysis-sessions";
import {
  groupByTime,
  mergeItems,
  refreshFirstPage,
  type SessionList,
} from "./conversation-history";
import type { PagePayload } from "@/contracts/page";
import { useDocumentSession } from "./session-boundary";
import { AppLink } from "./app-link";
import { UiButton } from "../ui/button";

type SessionAction = {
  kind: "rename" | "delete";
  item: SessionListItem;
};

function scrollTitle(event: PointerEvent<HTMLDivElement>) {
  if (event.pointerType !== "mouse" || matchMedia("(prefers-reduced-motion: reduce)").matches)
    return;
  const row = event.currentTarget;
  const title = row.querySelector<HTMLElement>(".conversation-title");
  const text = title?.firstElementChild;
  const menu = row.querySelector<HTMLElement>(".conversation-menu-trigger");
  if (!title || !text || !menu) return;
  const bounds = title.getBoundingClientRect();
  const textWidth = text.getBoundingClientRect().width;
  const visibleWidth = Math.min(bounds.width, menu.getBoundingClientRect().left - bounds.left);
  if (textWidth <= visibleWidth) return;

  const distance = Math.ceil(textWidth - visibleWidth + 4);
  title.style.setProperty("--title-scroll-distance", `-${distance}px`);
  title.style.setProperty("--title-scroll-duration", `${Math.min(9, distance / 50)}s`);
  title.removeAttribute("title");
  title.setAttribute("data-scrolling", "");
}

function resetTitle(event: PointerEvent<HTMLDivElement>) {
  const title = event.currentTarget.querySelector<HTMLElement>(
    ".conversation-title[data-scrolling]",
  );
  if (!title) return;
  title.removeAttribute("data-scrolling");
  title.title = title.textContent ?? "";
}

export function ConversationList({ payload, active }: { payload: PagePayload; active: boolean }) {
  const api = useDocumentSession();
  const [list, setList] = useState<SessionList | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [refreshAttempt, setRefreshAttempt] = useState(0);
  const [today, setToday] = useState(() => new Date());
  const [action, setAction] = useState<SessionAction | null>(null);
  const [title, setTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [failedCursor, setFailedCursor] = useState<string | null>(null);
  const moreRequest = useRef<AbortController | null>(null);
  const moreButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const errorId = useId();
  const deleted = useRef(new Set<string>());
  const operation = useRef<{ key: string; id: string } | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const dialogOpener = useRef<HTMLButtonElement | null>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const scope = new URLSearchParams(payload.session.scope).toString();
  const allowed = payload.session.capabilities.includes("models.invoke");
  const target = payload.session.navigation.find((entry) => entry.id === "analysis-new")?.href;
  useEffect(() => {
    if (!active || !target || !allowed) return;
    const controller = new AbortController();
    let latestRead = 0;
    async function load() {
      const read = ++latestRead;
      try {
        const value = await api.request<SessionList>(
          `analysis/sessions?${scope}`,
          "GET",
          undefined,
          { signal: controller.signal },
        );
        if (!controller.signal.aborted && latestRead === read) {
          setList((previous) => refreshFirstPage(previous, value, deleted.current));
          setRefreshFailed(false);
        }
      } catch {
        if (!controller.signal.aborted && latestRead === read) setRefreshFailed(true);
      }
    }
    void load();
    function refreshWhenVisible() {
      if (document.visibilityState === "visible") void load();
    }

    function changed(event: Event) {
      const item = (event as CustomEvent<SessionListItem | null | undefined>).detail;
      if (item && !deleted.current.has(item.sessionId))
        setList((current) => ({
          items: mergeItems(current?.items ?? [], [item]),
          nextCursor: current?.nextCursor ?? null,
        }));
      void load();
    }
    window.addEventListener(sessionsChanged, changed);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      controller.abort();
      window.removeEventListener(sessionsChanged, changed);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [active, api, scope, target, allowed, refreshAttempt]);
  const pendingIds = JSON.stringify(
    list?.items.filter((item) => item.titleStatus === "pending").map((item) => item.sessionId) ??
      [],
  );
  useEffect(() => {
    const ids: string[] = JSON.parse(pendingIds);
    if (!active || !ids.length) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refreshTitles() {
      // Bound requests even when several pages of sessions have been loaded.
      for (const id of ids) {
        if (controller.signal.aborted) return;
        try {
          const item = await api.request<SessionListItem>(
            `analysis/sessions/${id}?${scope}`,
            "GET",
            undefined,
            { signal: controller.signal, retainOnNotFound: true },
          );
          if (!controller.signal.aborted)
            setList((current) =>
              current?.items.some((entry) => entry.sessionId === id)
                ? { ...current, items: mergeItems(current.items, [item]) }
                : current,
            );
        } catch (failure) {
          if (!controller.signal.aborted && failure instanceof ApiError && failure.status === 404) {
            deleted.current.add(id);
            setList((current) =>
              current
                ? { ...current, items: current.items.filter((item) => item.sessionId !== id) }
                : current,
            );
          }
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(refreshTitles, 2000);
    }
    timer = setTimeout(refreshTitles, 1000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, api, pendingIds, scope]);

  useEffect(() => {
    const midnight = new Date(today);
    midnight.setHours(24, 0, 0, 0);
    const timer = setTimeout(() => setToday(new Date()), midnight.getTime() - Date.now());
    return () => clearTimeout(timer);
  }, [today]);

  const cursor = list?.nextCursor;
  const loadMore = useCallback(async () => {
    if (!active || !allowed || !cursor || moreRequest.current) return;
    const controller = new AbortController();
    moreRequest.current = controller;
    setLoadingMore(true);
    setFailedCursor(null);
    try {
      const page = await api.request<SessionList>(
        `analysis/sessions?${scope}&cursor=${encodeURIComponent(cursor)}`,
        "GET",
        undefined,
        { signal: controller.signal },
      );
      if (!controller.signal.aborted)
        setList((current) =>
          current?.nextCursor === cursor
            ? {
                items: mergeItems(
                  current.items,
                  page.items.filter((item) => !deleted.current.has(item.sessionId)),
                ),
                nextCursor: page.nextCursor,
              }
            : current,
        );
    } catch {
      if (!controller.signal.aborted) setFailedCursor(cursor);
    } finally {
      if (moreRequest.current === controller) {
        moreRequest.current = null;
        setLoadingMore(false);
      }
    }
  }, [active, allowed, api, scope, cursor]);

  useEffect(() => () => moreRequest.current?.abort(), [api, scope, active]);
  useEffect(() => {
    if (
      !active ||
      !cursor ||
      loadingMore ||
      failedCursor === cursor ||
      !moreButton.current ||
      !listRef.current ||
      typeof IntersectionObserver === "undefined"
    )
      return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) void loadMore();
      },
      { root: listRef.current, rootMargin: "0px 0px 96px" },
    );
    observer.observe(moreButton.current);
    return () => observer.disconnect();
  }, [active, cursor, loadingMore, failedCursor, loadMore]);
  if (!target) return null;
  if (!allowed)
    return (
      <div className="conversation-list">
        <p className="conversation-empty">获得模型调用权限后可查看分析会话。</p>
      </div>
    );
  const selected = new URL(payload.viewKey, "https://compass.local").searchParams.get("session");
  const groups = groupByTime(list?.items ?? [], today);
  function openAction(kind: SessionAction["kind"], item: SessionListItem) {
    dialogOpener.current =
      listRef.current?.querySelector(`[data-session-menu="${item.sessionId}"]`) ?? null;
    setTitle(item.title);
    setActionError("");
    operation.current = null;
    setAction({ kind, item });
  }
  async function submitAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!action || saving) return;
    const { item, kind } = action;
    const name = title.trim();
    if (kind === "rename" && !name) return;
    const key = kind === "rename" ? name : kind;
    if (operation.current?.key !== key) operation.current = { key, id: crypto.randomUUID() };
    const body = { ...payload.session.scope, requestId: operation.current.id };
    const path = `analysis/sessions/${item.sessionId}`;
    setSaving(true);
    setActionError("");
    try {
      if (kind === "rename") {
        const updated = await api.request<SessionListItem>(
          path,
          "PATCH",
          { ...body, title: name },
          { retainOnNotFound: true },
        );
        setList((current) =>
          current ? { ...current, items: mergeItems(current.items, [updated]) } : current,
        );
      } else {
        await api.request(path, "DELETE", body, { retainOnNotFound: true });
        deleted.current.add(item.sessionId);
        setList((current) =>
          current
            ? {
                ...current,
                items: current.items.filter((entry) => entry.sessionId !== item.sessionId),
              }
            : current,
        );
        if (selected === item.sessionId && target) {
          api.setDirty(false);
          api.navigate(target);
        }
      }
      setAction(null);
      window.dispatchEvent(new Event(sessionsChanged));
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : "操作失败，请重试。");
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="conversation-list" ref={listRef} tabIndex={-1} aria-label="分析会话">
      {!list && !refreshFailed && (
        <p className="conversation-empty" role="status">
          正在读取会话…
        </p>
      )}
      {list && !groups.size && !refreshFailed && <p className="conversation-empty">暂无分析会话</p>}
      {Array.from(groups, ([label, items]) => (
        <section className="conversation-group" key={label} aria-label={label}>
          <h3>{label}</h3>
          {items.map((item) => (
            <div
              className="conversation-row"
              key={item.sessionId}
              onPointerEnter={scrollTitle}
              onPointerLeave={resetTitle}
            >
              <AppLink
                className="conversation-item"
                href={`${target}?session=${item.sessionId}`}
                aria-current={selected === item.sessionId ? "page" : undefined}
              >
                {item.iconKey === "workflow" ? (
                  <Workflow size={16} aria-hidden="true" />
                ) : (
                  <MessageSquare size={16} aria-hidden="true" />
                )}
                <span className="conversation-title" key={item.title} title={item.title}>
                  <span>{item.title}</span>
                </span>
              </AppLink>
              <DropdownMenu.Root modal={false}>
                <DropdownMenu.Trigger
                  className="conversation-menu-trigger"
                  aria-label={`会话操作 · ${item.title}`}
                  data-session-menu={item.sessionId}
                >
                  <Ellipsis size={16} aria-hidden="true" />
                </DropdownMenu.Trigger>
                <DropdownMenu.Portal>
                  <DropdownMenu.Content
                    className="dropdown conversation-menu"
                    align="end"
                    sideOffset={4}
                    data-sensitive-portal
                    onCloseAutoFocus={(event) => {
                      if (dialogOpener.current?.dataset.sessionMenu === item.sessionId)
                        event.preventDefault();
                    }}
                  >
                    <DropdownMenu.Item onSelect={() => openAction("rename", item)}>
                      <Pencil size={14} aria-hidden="true" /> 改名
                    </DropdownMenu.Item>
                    <DropdownMenu.Item
                      className="conversation-delete"
                      onSelect={() => openAction("delete", item)}
                    >
                      <Trash2 size={14} aria-hidden="true" /> 删除
                    </DropdownMenu.Item>
                  </DropdownMenu.Content>
                </DropdownMenu.Portal>
              </DropdownMenu.Root>
            </div>
          ))}
        </section>
      ))}
      {active && refreshFailed && (
        <div className="conversation-feedback">
          <p role="alert">暂时无法刷新会话。</p>
          <button
            className="secondary compact"
            onClick={() => {
              setRefreshFailed(false);
              setRefreshAttempt((attempt) => attempt + 1);
            }}
          >
            重试
          </button>
        </div>
      )}
      {cursor && (
        <button
          ref={moreButton}
          className="secondary compact conversation-more"
          disabled={!active || loadingMore}
          aria-busy={loadingMore}
          onClick={() => void loadMore()}
        >
          {loadingMore
            ? "正在加载…"
            : failedCursor === cursor
              ? "重试加载更早会话"
              : "加载更多会话"}
        </button>
      )}
      <Dialog.Root
        open={!!action}
        onOpenChange={(open) => {
          if (!open && !saving) setAction(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" data-sensitive-portal />
          <Dialog.Content
            className="dialog"
            data-sensitive-portal
            onOpenAutoFocus={(event) => {
              if (action?.kind === "delete") {
                event.preventDefault();
                cancel.current?.focus();
              }
            }}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              const opener = dialogOpener.current;
              dialogOpener.current = null;
              if (opener?.isConnected) opener.focus();
              else listRef.current?.focus();
            }}
          >
            <div className="dialog-head">
              <div>
                <Dialog.Title>{action?.kind === "rename" ? "改名" : "删除会话"}</Dialog.Title>
                <Dialog.Description>
                  {action?.kind === "rename"
                    ? "修改这条会话在侧栏显示的标题。"
                    : `删除「${action?.item.title}」及其会话记录？`}
                </Dialog.Description>
              </div>
            </div>
            <div className="dialog-body">
              <form onSubmit={submitAction}>
                {action?.kind === "rename" && (
                  <div className="ui-field">
                    <label htmlFor={titleId}>会话标题</label>
                    <input
                      className="ui-input"
                      id={titleId}
                      value={title}
                      onChange={(event) => setTitle(event.target.value)}
                      onFocus={(event) => event.target.select()}
                      disabled={saving}
                      maxLength={80}
                      required
                      aria-describedby={actionError ? errorId : undefined}
                    />
                  </div>
                )}
                {actionError && (
                  <p id={errorId} role="alert">
                    {actionError}
                  </p>
                )}
                <div className="ui-actions conversation-dialog-actions">
                  <Dialog.Close asChild>
                    <button
                      type="button"
                      ref={cancel}
                      className="ui-button"
                      data-variant="secondary"
                      disabled={saving}
                    >
                      取消
                    </button>
                  </Dialog.Close>
                  <UiButton
                    type="submit"
                    variant={action?.kind === "delete" ? "danger" : "primary"}
                    busy={saving}
                    disabled={action?.kind === "rename" && !title.trim()}
                  >
                    {action?.kind === "rename"
                      ? saving
                        ? "正在保存…"
                        : "保存"
                      : saving
                        ? "正在删除…"
                        : "删除"}
                  </UiButton>
                </div>
              </form>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
