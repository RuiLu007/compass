import { useState } from "react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import type { ScopeView } from "@/contracts/semantic";

export type Suggestion = {
  source_ref: string;
  position: string;
  name: string;
  summary: string;
  formula: string;
  confidence: number;
  missing: string[];
  suggestions: string[];
};
export function Suggestions({
  target,
  sources,
  scopes,
  version,
  onChoose,
}: {
  target: string;
  sources: { source_id: string; locator: string }[];
  scopes: ScopeView[];
  version?: string;
  onChoose: (s: Suggestion) => void;
}) {
  const session = useDocumentSession();
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [items, setItems] = useState<Suggestion[]>([]);
  return (
    <details className="ui-panel">
      <summary>{version ? "AI 审查草稿" : "从来源生成候选"}</summary>
      <form
        className="semantic-form"
        onSubmit={async (e) => {
          e.preventDefault();
          const data = new FormData(e.currentTarget);
          setBusy(true);
          setMessage("");
          setItems([]);
          try {
            const result = await session.request<{ candidates: Suggestion[] }>(
              "semantic/suggestions?" + target,
              "POST",
              version
                ? { version_id: version }
                : { source_refs: [data.get("source")], owner_scope_id: data.get("owner") },
              { retainOnNotFound: true },
            );
            setItems(result.candidates);
            if (!result.candidates.length) setMessage("当前素材没有足够信息生成建议。");
          } catch (e) {
            setMessage(e instanceof Error ? e.message : "生成建议失败，请重试。");
          } finally {
            setBusy(false);
          }
        }}
      >
        {!version && (
          <>
            <label>
              候选来源
              <select name="source" required>
                {sources.map((s) => (
                  <option key={s.source_id} value={s.source_id}>
                    {s.locator}
                  </option>
                ))}
              </select>
            </label>
            <label>
              候选归属
              <select name="owner" required>
                {scopes
                  .filter((s) => s.manage)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
              </select>
            </label>
          </>
        )}
        <div>
          <button className="ui-button" disabled={busy}>
            {busy ? "正在分析素材…" : version ? "审查草稿" : "生成候选"}
          </button>
        </div>
      </form>
      {message && <p role="status">{message}</p>}
      {items.map((s, i) => (
        <article key={i}>
          <h3>{s.name}</h3>
          <p>{s.summary}</p>
          <p>{s.formula}</p>
          <small>
            {s.position} · 置信度 {Math.round(s.confidence * 100)}%
          </small>
          {!!s.missing.length && <p>待补充　{s.missing.join("、")}</p>}
          <ul>
            {s.suggestions.map((v, j) => (
              <li key={j}>{v}</li>
            ))}
          </ul>
          <button className="ui-button" data-variant="secondary" onClick={() => onChoose(s)}>
            采用并编辑
          </button>
        </article>
      ))}
    </details>
  );
}
