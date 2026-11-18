"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "@/client/request-scope";
import type { AnalysisRunScope } from "@/client/analysis-run";
import { AppLink } from "@/components/shell/app-link";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { UiButton } from "@/components/ui/button";
import type { AvailableModels } from "@/features/models/types";
import { type Plan, type PlanReceipt, type Preview, planTime, schedulePath } from "./client";

function initial(plan?: Plan) {
  return {
    name: plan?.name ?? "",
    question: plan?.question ?? "",
    provider: plan?.selection.provider_id ?? "auto",
    cron: plan?.cron ?? "0 9 * * 1",
    timezone: plan?.timezone ?? "Asia/Shanghai",
    enabled: plan?.status !== "disabled",
  };
}

export function ScheduleForm({
  scope,
  plan,
  home,
  onSaved,
  onReload,
}: {
  scope: AnalysisRunScope;
  plan?: Plan;
  home: string;
  onSaved: (receipt: PlanReceipt) => void;
  onReload?: () => void;
}) {
  const api = useDocumentSession();
  const [form, setForm] = useState(() => initial(plan));
  const [models, setModels] = useState<AvailableModels>();
  const [modelError, setModelError] = useState("");
  const [modelReload, setModelReload] = useState(0);
  const [preview, setPreview] = useState<Preview>();
  const [busy, setBusy] = useState<"preview" | "save">();
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const request = useRef<{ fingerprint: string; key: string } | null>(null);
  const { tenantId, workspaceId } = scope;
  const selection =
    form.provider === "auto"
      ? { mode: "auto" as const }
      : { mode: "fixed" as const, provider_id: form.provider };
  const chosen = models?.items.find((model) => model.id === form.provider);

  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams({ tenantId, workspaceId, view: "chat" });
    void api
      .request<AvailableModels>(`models/available?${query}`, "GET", undefined, {
        signal: controller.signal,
      })
      .then((value) => {
        if (!controller.signal.aborted) {
          setModels(value);
          setModelError("");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setModelError("暂时无法加载可用模型。");
      });
    return () => controller.abort();
  }, [api, tenantId, workspaceId, modelReload]);

  function change<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    api.setDirty(true);
    setForm((old) => ({ ...old, [key]: value }));
    setPreview(undefined);
  }

  async function previewTimes() {
    if (busy) return;
    setBusy("preview");
    setError("");
    setPreview(undefined);
    try {
      setPreview(
        await api.request<Preview>(schedulePath(scope, "/preview"), "POST", {
          cron: form.cron,
          timezone: form.timezone,
          selection,
        }),
      );
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "无法预览触发时间。");
    } finally {
      setBusy(undefined);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || conflict) return;
    const fields = {
      name: form.name,
      question: form.question,
      selection,
      cron: form.cron,
      timezone: form.timezone,
      enabled: form.enabled,
    };
    const body = plan
      ? {
          expectedRevision: plan.revision,
          patch: {
            ...(form.name !== plan.name ? { name: form.name } : {}),
            ...(form.question !== plan.question ? { question: form.question } : {}),
            ...(form.provider !== initial(plan).provider ? { selection } : {}),
            ...(form.cron !== plan.cron ? { cron: form.cron } : {}),
            ...(form.timezone !== plan.timezone ? { timezone: form.timezone } : {}),
            ...(form.enabled !== initial(plan).enabled ? { enabled: form.enabled } : {}),
          },
        }
      : fields;
    if ("patch" in body && Object.keys(body.patch ?? {}).length === 0) {
      setError("修改字段后即可保存。");
      return;
    }
    const fingerprint = JSON.stringify(body);
    if (request.current?.fingerprint !== fingerprint)
      request.current = { fingerprint, key: crypto.randomUUID() };
    setBusy("save");
    setError("");
    try {
      const receipt = await api.request<PlanReceipt>(
        schedulePath(scope, plan ? `/${plan.id}` : ""),
        plan ? "PATCH" : "POST",
        { ...body, requestId: request.current.key },
        { retainOnNotFound: true },
      );
      api.setDirty(false);
      onSaved(receipt);
    } catch (failure) {
      setConflict(failure instanceof ApiError && failure.status === 409);
      setError(failure instanceof Error ? failure.message : "保存失败，请重试。");
    } finally {
      setBusy(undefined);
    }
  }

  return (
    <form className="ui-panel schedule-form" data-editor onSubmit={save}>
      <div className="ui-field">
        <label htmlFor="schedule-name">任务名称</label>
        <input
          id="schedule-name"
          className="ui-input"
          required
          maxLength={120}
          disabled={Boolean(busy)}
          value={form.name}
          onChange={(e) => change("name", e.target.value)}
        />
      </div>
      <div className="ui-field">
        <label htmlFor="schedule-question">分析内容</label>
        <textarea
          id="schedule-question"
          className="ui-input"
          rows={5}
          required
          maxLength={8000}
          disabled={Boolean(busy)}
          value={form.question}
          placeholder="例如，汇总上周各渠道实付金额并说明主要变化。"
          onChange={(e) => change("question", e.target.value)}
        />
      </div>
      <div className="ui-field">
        <label htmlFor="schedule-model">模型</label>
        <select
          id="schedule-model"
          className="ui-input"
          value={form.provider}
          disabled={Boolean(busy)}
          onChange={(e) => change("provider", e.target.value)}
        >
          <option value="auto">自动选模</option>
          {form.provider !== "auto" && !chosen && (
            <option value={form.provider}>原模型 · 当前列表不可用</option>
          )}
          {models?.items.map((model) => (
            <option key={model.id} value={model.id}>
              {model.alias}
            </option>
          ))}
        </select>
        {modelError && (
          <div className="ui-actions">
            <span className="ui-field-hint">{modelError}</span>
            <UiButton variant="quiet" onClick={() => setModelReload((n) => n + 1)}>
              重试
            </UiButton>
          </div>
        )}
        {chosen && chosen.capabilities.tools.state !== "supported" && (
          <p className="ui-field-hint">
            该模型不支持工具调用。需要查询数据时请选择支持工具的模型。
          </p>
        )}
      </div>
      <div className="schedule-rule-fields">
        <div className="ui-field">
          <label htmlFor="schedule-cron">定时规则（Cron）</label>
          <input
            id="schedule-cron"
            className="ui-input"
            required
            maxLength={120}
            aria-describedby="schedule-cron-hint"
            disabled={Boolean(busy)}
            value={form.cron}
            onChange={(e) => change("cron", e.target.value)}
          />
          <small id="schedule-cron-hint" className="ui-field-hint">
            分钟、小时、日、月、星期。每天 9 点填 0 9 * * *，每周一 9 点填 0 9 * * 1。
          </small>
        </div>
        <div className="ui-field">
          <label htmlFor="schedule-timezone">时区</label>
          <input
            id="schedule-timezone"
            className="ui-input"
            required
            maxLength={128}
            list="schedule-zones"
            disabled={Boolean(busy)}
            value={form.timezone}
            onChange={(e) => change("timezone", e.target.value)}
          />
          <datalist id="schedule-zones">
            {["Asia/Shanghai", "UTC", "Asia/Tokyo", "America/New_York", "Europe/London"].map(
              (zone) => (
                <option key={zone} value={zone} />
              ),
            )}
          </datalist>
        </div>
      </div>
      <label className="schedule-enabled">
        <input
          type="checkbox"
          checked={form.enabled}
          disabled={Boolean(busy)}
          onChange={(e) => change("enabled", e.target.checked)}
        />{" "}
        启用任务
      </label>
      {plan?.status === "enabled" && !form.enabled && (
        <p className="ui-field-hint">
          停用后不再生成新触发。已开始交付或运行的记录仍可在执行记录中查看。
        </p>
      )}
      {preview && (
        <div aria-live="polite" className="schedule-preview">
          <strong>预计触发时间 · {form.timezone}</strong>
          <ul>
            {preview.times.map((value) => (
              <li key={value}>{planTime(value, form.timezone)}</li>
            ))}
          </ul>
          {preview.warningCode && <p>当前模型不支持工具调用。</p>}
          {preview.autoRouting && (
            <p>
              自动选模在运行时选择路由。Flash
              {preview.autoRouting.flash ? " 已配置" : " 缺少可用绑定"}，Pro
              {preview.autoRouting.pro ? " 已配置" : " 缺少可用绑定"}。
              {(!preview.autoRouting.flash || !preview.autoRouting.pro) &&
                "请联系管理员检查模型路由。"}
            </p>
          )}
        </div>
      )}
      {error && (
        <p className="ui-notice" data-tone="error" role="alert">
          {error}
        </p>
      )}
      {conflict && onReload && (
        <UiButton variant="secondary" onClick={onReload}>
          加载最新版本
        </UiButton>
      )}
      <div className="ui-actions">
        <UiButton type="submit" disabled={busy === "preview" || conflict} busy={busy === "save"}>
          {busy === "save" ? "正在保存…" : "保存任务"}
        </UiButton>
        <UiButton
          variant="secondary"
          disabled={busy === "save"}
          busy={busy === "preview"}
          onClick={() => void previewTimes()}
        >
          {busy === "preview" ? "正在计算…" : "预览触发时间"}
        </UiButton>
        <AppLink className="ui-link-button" data-variant="quiet" href={home}>
          返回全部任务
        </AppLink>
      </div>
    </form>
  );
}
