"use client";

import { useEffect, useState } from "react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { parseModelContract } from "@/contracts/model-contract";
import type { components } from "@/generated/api";
import type { ScopeView } from "@/contracts/semantic";
import { MetricForm } from "./form";

type Result = components["schemas"]["SemanticOpenResult"];
export function SemanticResultActions({
  runId,
  tenantId,
  workspaceId,
}: {
  runId: string;
  tenantId: string;
  workspaceId: string;
}) {
  const api = useDocumentSession();
  const path = "analysis/runs/" + runId + "/semantic-results";
  const target = new URLSearchParams({ tenantId, workspaceId }).toString();
  const [items, setItems] = useState<Result[]>([]);
  const [scopes, setScopes] = useState<ScopeView[]>([]);
  const [personal, setPersonal] = useState(false);
  const [selected, select] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    const ctl = new AbortController();
    void api
      .request(path + "?" + target, "GET", undefined, {
        signal: ctl.signal,
        retainOnNotFound: true,
      })
      .then((raw) => {
        if (!ctl.signal.aborted)
          setItems(parseModelContract<{ items: Result[] }>("SemanticResults", raw).items);
      })
      .catch(() => {
        if (!ctl.signal.aborted) setItems([]);
      });
    const clear = api.onClear(() => {
      setItems([]);
      select("");
      setScopes([]);
    });
    return () => {
      ctl.abort();
      clear();
    };
  }, [api, path, target]);
  async function preview(ref: string) {
    try {
      const s = await api.request<{ items: ScopeView[]; can_create_personal: boolean }>(
        "semantic/scopes?" + target,
        "GET",
      );
      setScopes(s.items);
      setPersonal(s.can_create_personal);
      select(ref);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法读取归属范围");
    }
  }
  async function decide(
    row: Result,
    action: "confirm" | "dismiss" | "save",
    fields?: Record<string, unknown>,
  ) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = parseModelContract<Result>(
        "SemanticOpenResult",
        await api.request(path, "POST", {
          tenantId,
          workspaceId,
          result_ref: row.result_ref,
          action,
          ...(fields ? { fields } : {}),
        }),
      );
      setItems((all) => all.map((x) => (x.result_ref === result.result_ref ? result : x)));
      select("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败，请重试。");
    } finally {
      setBusy(false);
    }
  }
  if (!items.length) return null;
  return (
    <section className="durable-approval" aria-label="临时查询结果">
      {items.map((row) => (
        <div key={row.result_ref}>
          <p>
            临时口径 · {row.columns.join("、")} · {new Date(row.created_at).toLocaleString("zh-CN")}
          </p>
          {row.status === "active" && (
            <button
              className="ui-button"
              disabled={busy}
              onClick={() => void decide(row, "confirm")}
            >
              确认查询结果
            </button>
          )}
          {row.status === "confirmed" && row.reusable && (
            <p>这项汇总可在其他期间复用。需要保存这套计算口径吗？</p>
          )}
          {row.status === "saved" ? (
            <a
              href={
                location.pathname.replace(/\/analysis(?:\/.*)?$/, "/admin/data/metrics") +
                "?asset=" +
                encodeURIComponent(row.draft_id ?? "")
              }
            >
              查看指标草稿
            </a>
          ) : (
            <>
              <button
                className="ui-button"
                data-variant="secondary"
                onClick={() => void preview(row.result_ref)}
              >
                保存为指标草稿
              </button>
              {row.status === "confirmed" && row.reusable && (
                <button
                  className="ui-button"
                  data-variant="secondary"
                  disabled={busy}
                  onClick={() => void decide(row, "dismiss")}
                >
                  暂不保存
                </button>
              )}
            </>
          )}
          {selected === row.result_ref &&
            Array.isArray(row.proposal?.parameter_examples) &&
            !!row.proposal.parameter_examples.length && (
              <p>原查询参数示例 · {row.proposal.parameter_examples.join("、")}</p>
            )}
          {selected === row.result_ref && (
            <MetricForm
              mode="receipt"
              initial={row.proposal?.fields as Record<string, unknown> | undefined}
              scopes={scopes}
              personal={personal}
              sources={[]}
              busy={busy}
              cancel={() => select("")}
              save={(f) => decide(row, "save", f)}
            />
          )}
        </div>
      ))}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
