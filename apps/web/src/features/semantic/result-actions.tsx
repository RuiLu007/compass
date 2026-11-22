"use client";

import { useEffect, useRef, useState } from "react";
import { BookmarkPlus, Check, ExternalLink } from "lucide-react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { UiButton } from "@/components/ui/button";
import { parseModelContract } from "@/contracts/model-contract";
import type { components } from "@/generated/api";
import { MAX_CARD_ITEMS, SemanticCardHeader } from "./choice-card";

type Result = components["schemas"]["SemanticOpenResult"];
type Props = { runId: string; tenantId: string; workspaceId: string };
export type MetricSuggestion = {
  id: string;
  name: string;
  summary: string;
  formula: string;
  reason: string;
  unit: string;
  draftId?: string | null;
};

function suggestion(row: Result): MetricSuggestion[] {
  const value = row.proposal?.fields;
  const fields =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  const reason = row.proposal?.reuse_reason;
  if (row.status === "saved" && row.draft_id) {
    const title = row.proposal?.saved_name ?? (reason ? fields?.name : null);
    return [
      {
        id: row.result_ref,
        name: typeof title === "string" ? title : "查看已保存的指标草稿",
        summary: "",
        formula: "",
        reason: "",
        unit: "",
        draftId: row.draft_id,
      },
    ];
  }
  if (
    !row.reusable ||
    row.status === "dismissed" ||
    !fields ||
    typeof reason !== "string" ||
    typeof fields.name !== "string" ||
    typeof fields.summary !== "string" ||
    typeof fields.formula !== "string"
  )
    return [];
  return [
    {
      id: row.result_ref,
      name: fields.name,
      summary: fields.summary,
      formula: fields.formula,
      reason,
      unit: typeof fields.unit === "string" ? fields.unit : "",
      draftId: row.draft_id,
    },
  ];
}

export function MetricSuggestionsCard({
  items,
  busy,
  error,
  onDecide,
  draftUrl,
}: {
  items: MetricSuggestion[];
  busy: boolean;
  error: string;
  onDecide: (ids: string[], action: "save" | "dismiss") => void;
  draftUrl: (id: string) => string;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const visible = [
    ...items.filter((m) => !m.draftId).slice(0, MAX_CARD_ITEMS),
    ...items.filter((m) => m.draftId),
  ];
  const pending = visible.filter((s) => !s.draftId).map((s) => s.id);
  const chosen = pending.filter((id) => selected.includes(id));
  if (!visible.length) return null;
  return (
    <section className="semantic-chat-card semantic-suggestions" aria-label="指标保存建议">
      <SemanticCardHeader
        icon={BookmarkPlus}
        title={pending.length ? "值得留存的指标" : "已保存的指标草稿"}
      />
      <fieldset className="semantic-choices" disabled={busy}>
        <legend className="sr-only">选择要保存的指标</legend>
        {visible.map((m) =>
          m.draftId ? (
            <div className="semantic-saved" key={m.id}>
              <Check size={16} aria-hidden="true" />
              <a href={draftUrl(m.draftId)}>
                {m.name}
                <ExternalLink size={13} aria-hidden="true" />
              </a>
            </div>
          ) : (
            <label className="semantic-option" key={m.id}>
              <input
                type="checkbox"
                checked={selected.includes(m.id)}
                aria-label={m.name}
                onChange={(e) =>
                  setSelected((prev) =>
                    e.target.checked ? [...prev, m.id] : prev.filter((id) => id !== m.id),
                  )
                }
              />
              <span className="semantic-option-copy">
                <span className="semantic-option-heading">
                  <strong>{m.name}</strong>
                  {m.unit && <small>{m.unit}</small>}
                </span>
                <span className="semantic-result-detail">
                  <b>统计口径</b>
                  <span>{m.summary}</span>
                </span>
                <span className="semantic-result-detail">
                  <b>计算公式</b>
                  <span className="semantic-formula">{m.formula}</span>
                </span>
                <span className="semantic-result-detail">
                  <b>复用场景</b>
                  <span>{m.reason}</span>
                </span>
              </span>
            </label>
          ),
        )}
      </fieldset>
      {error && (
        <p role="alert" className="semantic-card-error">
          {error}
        </p>
      )}
      {!!pending.length && (
        <footer className="semantic-card-footer">
          <span className="semantic-card-caption">保存到个人草稿</span>
          <UiButton variant="quiet" disabled={busy} onClick={() => onDecide(pending, "dismiss")}>
            暂不保存
          </UiButton>
          <UiButton busy={busy} disabled={!chosen.length} onClick={() => onDecide(chosen, "save")}>
            保存所选{chosen.length ? `（${chosen.length}）` : ""}
          </UiButton>
        </footer>
      )}
    </section>
  );
}

export function SemanticResultActions(props: Props) {
  return (
    <RunSuggestions key={[props.runId, props.tenantId, props.workspaceId].join("/")} {...props} />
  );
}

function RunSuggestions({ runId, tenantId, workspaceId }: Props) {
  const api = useDocumentSession();
  const path = "analysis/runs/" + runId + "/semantic-results";
  const target = new URLSearchParams({ tenantId, workspaceId }).toString();
  const [rows, setRows] = useState<Result[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(false);
  const submitting = useRef(false);
  useEffect(() => {
    const ctl = new AbortController();
    active.current = true;
    void api
      .request(path + "?" + target, "GET", undefined, {
        signal: ctl.signal,
        retainOnNotFound: true,
      })
      .then((raw) => {
        if (!ctl.signal.aborted)
          setRows(parseModelContract<{ items: Result[] }>("SemanticResults", raw).items);
      })
      .catch(() => {
        if (!ctl.signal.aborted) setRows([]);
      });
    const clear = api.onClear(() => {
      active.current = false;
      ctl.abort();
      setRows([]);
    });
    return () => {
      active.current = false;
      ctl.abort();
      clear();
    };
  }, [api, path, target]);

  async function decide(ids: string[], action: "save" | "dismiss") {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      for (const id of ids) {
        if (!active.current) break;
        const raw = await api.request(path, "POST", {
          tenantId,
          workspaceId,
          result_ref: id,
          action,
        });
        const result = parseModelContract<Result>("SemanticOpenResult", raw);
        if (active.current)
          setRows((prev) => prev.map((row) => (row.result_ref === id ? result : row)));
      }
    } catch (e) {
      if (active.current) setError(e instanceof Error ? e.message : "操作未完成，请重试。");
    } finally {
      submitting.current = false;
      if (active.current) setBusy(false);
    }
  }
  return (
    <MetricSuggestionsCard
      items={rows.flatMap(suggestion)}
      busy={busy}
      error={error}
      onDecide={decide}
      draftUrl={(id) =>
        location.pathname.replace(/\/analysis(?:\/.*)?$/, "/admin/data/metrics") +
        "?asset=" +
        encodeURIComponent(id)
      }
    />
  );
}
