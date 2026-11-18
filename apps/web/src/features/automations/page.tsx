"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import type { AnalysisRunScope } from "@/client/analysis-run";
import { ApiError } from "@/client/request-scope";
import type { PagePayload } from "@/contracts/page";
import { AppLink } from "@/components/shell/app-link";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { UiButton } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { ScheduleForm } from "./schedule-form";
import {
  deliveryLabel,
  describeRule,
  planTime,
  runLabel,
  schedulePath,
  statusLabel,
  triggerReason,
  type Plan,
  type Plans,
  type PlanReceipt,
  type Trigger,
  type Triggers,
} from "./client";

const retain = { retainOnNotFound: true };
const errorMessage = (failure: unknown) =>
  failure instanceof Error ? failure.message : "暂时无法加载，请重试。";

function useScheduleData<T>(path?: string, refreshOnFocus = true) {
  const api = useDocumentSession();
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    const load = () => {
      void api
        .request<T>(path, "GET", undefined, { ...retain, signal: controller.signal })
        .then((value) => {
          if (!controller.signal.aborted) {
            setData(value);
            setError("");
          }
        })
        .catch((failure) => {
          if (!controller.signal.aborted) setError(errorMessage(failure));
        });
    };
    load();
    const focus = () => {
      if (refreshOnFocus && !document.hidden) load();
    };
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    return () => {
      controller.abort();
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", focus);
    };
  }, [api, path, version, refreshOnFocus]);
  return { data, setData, error, reload };
}

function Loading({ error, reload }: { error: string; reload: () => void }) {
  return error ? (
    <div className="ui-panel">
      <p className="ui-notice" data-tone="error" role="alert">
        {error}
      </p>
      <UiButton variant="secondary" onClick={reload}>
        重试
      </UiButton>
    </div>
  ) : (
    <p className="ui-notice" role="status">
      正在加载…
    </p>
  );
}

function PlanList({
  scope,
  createHref,
  historyHref,
}: {
  scope: AnalysisRunScope;
  createHref: string;
  historyHref: string;
}) {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  function search(event: FormEvent) {
    event.preventDefault();
    setQuery(input.trim());
  }
  return (
    <section className="schedule-page">
      <div className="schedule-bar">
        <form className="schedule-filters" onSubmit={search}>
          <label className="ui-field">
            搜索任务名称
            <input
              className="ui-input"
              type="search"
              maxLength={120}
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
          </label>
          <label className="ui-field">
            状态
            <select
              className="ui-input"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="">全部状态</option>
              <option value="enabled">已启用</option>
              <option value="disabled">已停用</option>
            </select>
          </label>
          <UiButton type="submit" variant="secondary">
            搜索
          </UiButton>
        </form>
        <AppLink className="ui-link-button" href={createHref}>
          新建定时任务
        </AppLink>
      </div>
      <PlanRows
        key={JSON.stringify([query, status])}
        {...{ scope, createHref, historyHref }}
        filters={{ ...(query ? { q: query } : {}), ...(status ? { status } : {}) }}
      />
    </section>
  );
}

function PlanRows({
  scope,
  createHref,
  historyHref,
  filters,
}: {
  scope: AnalysisRunScope;
  createHref: string;
  historyHref: string;
  filters: Record<string, string>;
}) {
  const api = useDocumentSession();
  const { data, setData, error, reload } = useScheduleData<Plans>(schedulePath(scope, "", filters));
  const [remove, setRemove] = useState<Plan>();
  const [message, setMessage] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState<string>();
  const retry = useRef<{ target: string; requestId: string } | null>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const deleted = useRef(false);

  async function act(plan: Plan, deleting: boolean) {
    if (busy) return;
    setBusy(plan.id);
    setFailure("");
    setMessage("");
    const target = `${plan.id}/${plan.revision}/${deleting}`;
    if (retry.current?.target !== target)
      retry.current = { target, requestId: crypto.randomUUID() };
    try {
      const receipt = await api.request<PlanReceipt>(
        schedulePath(scope, `/${plan.id}`),
        deleting ? "DELETE" : "PATCH",
        {
          requestId: retry.current.requestId,
          expectedRevision: plan.revision,
          ...(deleting ? {} : { patch: { enabled: plan.status !== "enabled" } }),
        },
        retain,
      );
      setMessage(`「${plan.name}」${statusLabel[receipt.status]}。`);
      if (deleting) {
        deleted.current = true;
        setRemove(undefined);
      }
      reload();
    } catch (cause) {
      setFailure(errorMessage(cause));
      if (cause instanceof ApiError && cause.status === 409) {
        setRemove(undefined);
        reload();
      }
    } finally {
      setBusy(undefined);
    }
  }

  async function more(cursor: string) {
    setBusy("more");
    setFailure("");
    try {
      const page = await api.request<Plans>(schedulePath(scope, "", { ...filters, cursor }));
      setData((old) => ({
        items: [
          ...(old?.items ?? []),
          ...page.items.filter((p) => !old?.items.some((v) => v.id === p.id)),
        ],
        nextCursor: page.nextCursor ?? null,
      }));
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(undefined);
    }
  }

  if (!data) return <Loading error={error} reload={reload} />;
  return (
    <div className="ui-panel schedule-results">
      <h3 ref={title} tabIndex={-1}>
        全部任务
      </h3>
      {message && (
        <p className="ui-notice" data-tone="success" role="status">
          {message}
        </p>
      )}
      {(error || failure) && !remove && (
        <p className="ui-notice" data-tone="error" role="alert">
          {failure || error}
        </p>
      )}
      {data.items.length === 0 ? (
        <p>{Object.keys(filters).length ? "没有符合条件的定时任务。" : "暂无定时任务。"}</p>
      ) : (
        <div className="schedule-table-scroll" tabIndex={0} aria-label="定时任务列表">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>计划与时区</th>
                <th>状态</th>
                <th>下次触发</th>
                <th>最近执行</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((plan) => (
                <tr key={plan.id}>
                  <td>
                    <AppLink href={`${createHref}?schedule=${plan.id}`}>{plan.name}</AppLink>
                  </td>
                  <td>
                    {describeRule(plan.cron)}
                    <small>{plan.timezone}</small>
                  </td>
                  <td>
                    <span className={`status ${plan.status === "enabled" ? "" : "status-muted"}`}>
                      {statusLabel[plan.status]}
                    </span>
                  </td>
                  <td>{planTime(plan.nextFireAt, plan.timezone)}</td>
                  <td>
                    {plan.latestTrigger
                      ? plan.latestTrigger.runStatus
                        ? runLabel[plan.latestTrigger.runStatus]
                        : deliveryLabel[plan.latestTrigger.deliveryStatus]
                      : "暂无执行"}
                  </td>
                  <td>
                    <div className="ui-actions schedule-row-actions">
                      <AppLink
                        className="ui-link-button"
                        data-variant="quiet"
                        href={`${historyHref}?schedule=${plan.id}`}
                      >
                        执行记录
                      </AppLink>
                      <UiButton
                        variant="quiet"
                        disabled={Boolean(busy) && busy !== plan.id}
                        busy={busy === plan.id && !remove}
                        onClick={() => void act(plan, false)}
                      >
                        {plan.status === "enabled" ? "停用" : "启用"}
                      </UiButton>
                      <UiButton
                        variant="quiet"
                        disabled={Boolean(busy)}
                        onClick={() => {
                          setFailure("");
                          setRemove(plan);
                        }}
                      >
                        删除
                      </UiButton>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.nextCursor && (
        <UiButton
          variant="secondary"
          busy={busy === "more"}
          disabled={Boolean(busy) && busy !== "more"}
          onClick={() => void more(data.nextCursor!)}
        >
          加载更多
        </UiButton>
      )}
      {remove && (
        <Modal
          open
          title={`删除「${remove.name}」？`}
          initialFocus={cancel}
          returnFocus={() => {
            if (!deleted.current) return null;
            deleted.current = false;
            return title.current;
          }}
          description="删除后停止未来触发并保留执行记录。已开始交付或受理的运行会继续，可进入原会话取消。"
          onClose={() => {
            if (!busy) setRemove(undefined);
          }}
        >
          {failure && (
            <p className="ui-notice" data-tone="error" role="alert">
              {failure}
            </p>
          )}
          <div className="ui-actions">
            <UiButton
              ref={cancel}
              variant="secondary"
              disabled={Boolean(busy)}
              onClick={() => setRemove(undefined)}
            >
              取消
            </UiButton>
            <UiButton variant="danger" busy={Boolean(busy)} onClick={() => void act(remove, true)}>
              {busy ? "正在删除…" : "确认删除"}
            </UiButton>
          </div>
        </Modal>
      )}
    </div>
  );
}

function EditPlan({ scope, id, home }: { scope: AnalysisRunScope; id?: string; home: string }) {
  const api = useDocumentSession();
  const entry = useScheduleData<Plan>(id ? schedulePath(scope, `/${id}`) : undefined, false);
  const [receipt, setReceipt] = useState<PlanReceipt>();
  const [refreshKey, setRefreshKey] = useState(0);
  const [loadedRevision, setLoadedRevision] = useState<Plan>();
  const plan = loadedRevision ?? entry.data;
  if (receipt)
    return (
      <div className="ui-panel schedule-results">
        <p role="status" className="ui-notice" data-tone="success">
          定时任务已保存。
          {receipt.nextFireAt
            ? `下次预计触发 ${planTime(receipt.nextFireAt, plan?.timezone)}。`
            : "任务当前已停用。"}
        </p>
        <div className="ui-actions">
          <AppLink className="ui-link-button" href={home}>
            返回全部任务
          </AppLink>
        </div>
      </div>
    );
  if (id && !plan) return <Loading error={entry.error} reload={entry.reload} />;
  if (plan?.status === "deleted") return <p className="ui-notice">此任务已删除。</p>;
  async function reload() {
    api.setDirty(false);
    try {
      const latest = await api.request<Plan>(
        schedulePath(scope, `/${id}`),
        "GET",
        undefined,
        retain,
      );
      setLoadedRevision(latest);
      setRefreshKey((n) => n + 1);
    } catch {
      entry.reload();
    }
  }
  return (
    <ScheduleForm
      key={refreshKey}
      {...{ scope, plan, home }}
      onSaved={setReceipt}
      onReload={id ? () => void reload() : undefined}
    />
  );
}

function HistoryRows({
  scope,
  id,
  analysisHref,
  zone,
}: {
  scope: AnalysisRunScope;
  id: string;
  analysisHref?: string;
  zone?: string;
}) {
  const api = useDocumentSession();
  const history = useScheduleData<Triggers>(schedulePath(scope, `/${id}/triggers`));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function more(cursor: string) {
    setBusy(true);
    setError("");
    try {
      const page = await api.request<Triggers>(schedulePath(scope, `/${id}/triggers`, { cursor }));
      history.setData((old) => ({
        items: [
          ...(old?.items ?? []),
          ...page.items.filter((p) => !old?.items.some((v) => v.id === p.id)),
        ],
        nextCursor: page.nextCursor ?? null,
      }));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  if (!history.data) return <Loading error={history.error} reload={history.reload} />;
  return (
    <div className="ui-panel schedule-results">
      {(error || history.error) && (
        <p className="ui-notice" data-tone="error" role="alert">
          {error || history.error}
        </p>
      )}
      {history.data.items.length === 0 ? (
        <p>此任务暂无执行记录。</p>
      ) : (
        <div className="schedule-table-scroll" tabIndex={0} aria-label="定时任务执行记录">
          <table>
            <thead>
              <tr>
                <th>计划触发时间</th>
                <th>交付状态</th>
                <th>运行状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {history.data.items.map((trigger: Trigger) => (
                <tr key={trigger.id}>
                  <td>
                    {planTime(trigger.scheduledAt, zone)}
                    {trigger.coalescedFromAt && (
                      <small>合并自 {planTime(trigger.coalescedFromAt, zone)}</small>
                    )}
                  </td>
                  <td>
                    {deliveryLabel[trigger.deliveryStatus]}
                    {trigger.nextAttemptAt &&
                      ["retry_wait", "reconcile_pending"].includes(trigger.deliveryStatus) && (
                        <small>下次尝试 {planTime(trigger.nextAttemptAt, zone)}</small>
                      )}
                    {trigger.deliveryAttempts > 0 && (
                      <small>交付尝试 {trigger.deliveryAttempts} 次</small>
                    )}
                  </td>
                  <td>
                    {trigger.runStatus ? runLabel[trigger.runStatus] : "—"}
                    {trigger.observedAt && (
                      <small>同步于 {planTime(trigger.observedAt, zone)}</small>
                    )}
                    {triggerReason(trigger) && <small>{triggerReason(trigger)}</small>}
                  </td>
                  <td>
                    {analysisHref && trigger.sessionId && (
                      <AppLink href={`${analysisHref}?session=${trigger.sessionId}`}>
                        打开分析会话
                      </AppLink>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {history.data.nextCursor && (
        <UiButton
          variant="secondary"
          busy={busy}
          onClick={() => void more(history.data!.nextCursor!)}
        >
          加载更多
        </UiButton>
      )}
    </div>
  );
}

function History({
  scope,
  id,
  analysisHref,
}: {
  scope: AnalysisRunScope;
  id?: string;
  analysisHref?: string;
}) {
  const api = useDocumentSession();
  const [deleted, setDeleted] = useState(false);
  const [selected, setSelected] = useState(id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const filters: Record<string, string> = deleted ? { status: "deleted" } : {};
  const options = useScheduleData<Plans>(schedulePath(scope, "", filters));
  const detail = useScheduleData<Plan>(selected ? schedulePath(scope, `/${selected}`) : undefined);
  const items = options.data?.items ?? [];
  const selectedPlan =
    detail.data?.id === selected ? detail.data : items.find((plan) => plan.id === selected);
  async function more(cursor: string) {
    setBusy(true);
    setError("");
    try {
      const page = await api.request<Plans>(schedulePath(scope, "", { ...filters, cursor }));
      options.setData((old) => ({
        items: [...(old?.items ?? []), ...page.items],
        nextCursor: page.nextCursor ?? null,
      }));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="schedule-page">
      <div className="ui-panel schedule-filters">
        <label className="ui-field">
          选择定时任务
          <select
            className="ui-input"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
          >
            <option value="">请选择</option>
            {selected && !items.some((plan) => plan.id === selected) && (
              <option value={selected}>{selectedPlan?.name ?? "当前任务"}</option>
            )}
            {items.map((plan) => (
              <option key={plan.id} value={plan.id}>
                {plan.name}
              </option>
            ))}
          </select>
        </label>
        <label className="schedule-enabled">
          <input
            type="checkbox"
            checked={deleted}
            onChange={(event) => {
              options.setData(undefined);
              setDeleted(event.target.checked);
              setSelected("");
            }}
          />{" "}
          查看已删除任务
        </label>
        {options.data?.nextCursor && (
          <UiButton
            variant="secondary"
            busy={busy}
            onClick={() => void more(options.data!.nextCursor!)}
          >
            加载更多任务
          </UiButton>
        )}
      </div>
      {(options.error || error) && (
        <Loading error={options.error || error} reload={options.reload} />
      )}
      {!selected && options.data && !items.length && <p>当前没有可选任务。</p>}
      {selected && !selectedPlan && <Loading error={detail.error} reload={detail.reload} />}
      {selected && selectedPlan && (
        <HistoryRows
          key={selected}
          id={selected}
          {...{ scope, analysisHref }}
          zone={selectedPlan.timezone}
        />
      )}
    </section>
  );
}

export function AutomationsPage({
  payload,
  scope,
}: {
  payload: PagePayload;
  scope: AnalysisRunScope;
}) {
  const nav = (id: string, fallback: string) =>
    payload.session.navigation.find((entry) => entry.id === id)?.href ?? fallback;
  const home = nav("automations-all", "/automations");
  const createHref = nav("automations-new", "/automations/new");
  const historyHref = nav("automations-history", "/automations/history");
  const analysisHref = payload.session.navigation.find(
    (entry) => entry.id === "analysis-new",
  )?.href;
  const id =
    new URL(payload.viewKey, "https://compass.local").searchParams.get("schedule") ?? undefined;
  if (payload.page.id === "automations-new") return <EditPlan key={id} {...{ scope, id, home }} />;
  if (payload.page.id === "automations-history")
    return <History key={id} {...{ scope, id, analysisHref }} />;
  return <PlanList {...{ scope, createHref, historyHref }} />;
}
