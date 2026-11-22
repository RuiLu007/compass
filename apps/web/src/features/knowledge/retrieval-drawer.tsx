"use client";
import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Search } from "lucide-react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { UiButton } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import type { Base } from "./types";

type Match = {
  baseId: string;
  baseName: string;
  tenantName: string;
  workspaceName: string;
  fileId: string;
  fileName: string;
  chunkId: string;
  ordinal: number;
  heading: string;
  body: string;
  similarity: number;
};
type Result = { query: string; matches: Match[]; elapsedMs: number; model: string };
const suggestions = ["华东 GMV 下降", "优惠券叠加", "库存缺货", "退款口径"];

export function RetrievalDrawer({
  bases,
  scope,
  platform,
  onClose,
}: {
  bases: Base[];
  scope: string;
  platform: boolean;
  onClose: () => void;
}) {
  const api = useDocumentSession();
  const input = useRef<HTMLInputElement>(null);
  const pending = useRef<AbortController | null>(null);
  const [query, setQuery] = useState(suggestions[0]);
  const [baseId, setBaseId] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => () => pending.current?.abort(), []);

  async function search() {
    if (busy || !query.trim()) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const data = await api.request<Result>(
        "knowledge/" + (platform ? "catalog" : "bases") + "/search?" + scope,
        "POST",
        { query: query.trim(), baseId: baseId || null },
        { signal: controller.signal, timeoutMs: 45000, retainOnNotFound: true },
      );
      if (!controller.signal.aborted) setResult(data);
    } catch (e) {
      if (!controller.signal.aborted)
        setError(e instanceof Error ? e.message : "检索失败，请重试。");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  function changeQuery(value: string) {
    setQuery(value);
    setResult(null);
    setError("");
  }

  return (
    <Modal open placement="right" wide title="检索测试" onClose={onClose} initialFocus={input}>
      <div className="kb-retrieval">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void search();
          }}
        >
          <label>
            知识库
            <select
              value={baseId}
              disabled={busy}
              onChange={(event) => {
                setBaseId(event.target.value);
                setResult(null);
                setError("");
              }}
            >
              <option value="">全部可见知识库</option>
              {bases.map((base) => (
                <option value={base.id} key={base.id}>
                  {[base.name, base.workspaceName].filter(Boolean).join(" · ")}
                </option>
              ))}
            </select>
          </label>
          <label>
            关键词或问题
            <input
              ref={input}
              value={query}
              disabled={busy}
              maxLength={1000}
              placeholder="输入要查找的政策、活动或经营问题"
              onChange={(event) => changeQuery(event.target.value)}
            />
          </label>
          <div className="kb-suggestions" aria-label="示例关键词">
            {suggestions.map((text) => (
              <UiButton
                key={text}
                variant="secondary"
                disabled={busy}
                onClick={() => {
                  changeQuery(text);
                  input.current?.focus();
                }}
              >
                {text}
              </UiButton>
            ))}
          </div>
          <UiButton type="submit" busy={busy} disabled={!query.trim()}>
            <Search size={16} aria-hidden="true" />
            {busy ? "正在检索…" : "检索"}
          </UiButton>
        </form>
        <p className="kb-retrieval-help">
          按余弦相似度从高到低返回前 10 个已启用片段，百分比表示语义相似程度。
        </p>
        {error && (
          <p role="alert" className="kb-error">
            {error}
          </p>
        )}
        {busy && (
          <p role="status" className="kb-retrieval-help">
            正在生成查询向量并匹配知识片段…
          </p>
        )}
        {result && (
          <section aria-label="检索结果">
            <p role="status" className="kb-result-summary">
              “{result.query}” · 命中 {result.matches.length} 个片段 ·{" "}
              {(result.elapsedMs / 1000).toFixed(2)} 秒
            </p>
            {!result.matches.length && (
              <p className="kb-empty">当前范围没有可检索的片段，请检查文档入库和分段启用状态。</p>
            )}
            <div className="kb-results">
              {result.matches.map((match) => (
                <article className="kb-result" key={match.chunkId}>
                  <div className="kb-result-heading">
                    <strong>{match.heading || "分段 " + match.ordinal}</strong>
                    <span className="kb-score" title={"余弦相似度 " + match.similarity.toFixed(6)}>
                      相关度 {(match.similarity * 100).toFixed(2)}%
                    </span>
                  </div>
                  <p className="kb-result-source">
                    {match.baseName} · {match.fileName} · 分段 {match.ordinal}
                  </p>
                  {platform && (
                    <p className="kb-result-source">
                      {match.tenantName} / {match.workspaceName}
                    </p>
                  )}
                  <p className="kb-result-preview">{match.body}</p>
                  <details>
                    <summary>片段全文与 ID</summary>
                    <p className="kb-result-body">{match.body}</p>
                    <p className="kb-result-id">片段 ID · {match.chunkId}</p>
                  </details>
                  <a
                    className="kb-result-link"
                    href={"#" + new URLSearchParams({ kb: match.baseId, file: match.fileId })}
                  >
                    查看文件分段 <ArrowUpRight size={14} aria-hidden="true" />
                  </a>
                </article>
              ))}
            </div>
            <p className="kb-retrieval-help">{result.model} · 1024 维 · pgvector</p>
          </section>
        )}
      </div>
    </Modal>
  );
}
