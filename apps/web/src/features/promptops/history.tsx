"use client";
import { useEffect, useRef, useState } from "react";
import { Modal } from "@/components/ui/dialog";
import { AnalysisMarkdown } from "@/components/analysis-markdown";
import { UiCodeBlock } from "@/components/ui/code-block";
import { errorText, snapshotLabel, type FileView, type Ops, type Snapshot } from "./client";
import type { components } from "@/generated/api";

type Version = components["schemas"]["PromptHistoricalVersion"];
type Manifest = components["schemas"]["PromptSnapshot"];
export function HistoryDialog({
  ops,
  file,
  onClose,
  onSaved,
  onRestore,
}: {
  ops: Ops;
  file: FileView | null;
  onClose: () => void;
  onSaved: (msg: string) => void;
  onRestore: (n: number) => void;
}) {
  const [versions, setVersions] = useState<Version[]>([]),
    [snaps, setSnaps] = useState<Snapshot[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null),
    [historical, setHistorical] = useState<FileView | null>(null);
  const [err, setErr] = useState(""),
    [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const c = new AbortController();
    async function read() {
      if (file) {
        const r = await ops.get("history", { path: file.path }, c.signal);
        if (c.signal.aborted) return;
        setVersions(r.items);
        setCursor(r.next_cursor);
      } else {
        const r = await ops.get("snapshots", {}, c.signal);
        if (c.signal.aborted) return;
        setSnaps(r.items);
        setCursor(r.next_cursor);
      }
      setErr("");
    }
    void read().catch((e) => {
      if (!c.signal.aborted) setErr(errorText(e));
    });
    return () => c.abort();
  }, [ops, file, attempt]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setErr("");
    try {
      await fn();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  function version(path: string, v: number) {
    return action(async () => {
      setHistorical(await ops.get("file", { path, version: String(v) }));
    });
  }
  function loadManifest(n: number) {
    return action(async () => {
      setHistorical(null);
      setManifest(await ops.get("snapshot", { snapshot_no: String(n) }));
    });
  }
  async function more() {
    if (!cursor) return;
    if (file) {
      const r = await ops.get("history", { path: file.path, cursor });
      setVersions([...versions, ...r.items]);
      setCursor(r.next_cursor);
    } else {
      const r = await ops.get("snapshots", { cursor });
      setSnaps([...snaps, ...r.items]);
      setCursor(r.next_cursor);
    }
  }
  return (
    <Modal
      title={file ? "文件版本历史" : "整库快照"}
      open
      wide
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {file && (
        <p>
          <code>{file.path}</code>
        </p>
      )}
      <div className="prompt-list">
        {versions.map((r) => (
          <div key={r.version}>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void version(r.path, r.version)}
            >
              查看 v{r.version}
            </button>
            <span>{r.note}</span>
            <small>
              {new Date(r.created_at).toLocaleString()} · {r.created_by}
            </small>
          </div>
        ))}
        {snaps.map((r) => (
          <div key={r.snapshot_no}>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void loadManifest(r.snapshot_no)}
            >
              {snapshotLabel(r.snapshot_no)}
            </button>
            <span>{r.note}</span>
            <small>
              {new Date(r.created_at).toLocaleString()} · {r.created_by}
            </small>
          </div>
        ))}
      </div>
      {!versions.length && !snaps.length && !err && <p role="status">尚无发布记录。</p>}
      {cursor && (
        <button disabled={busy} className="secondary" onClick={() => void action(more)}>
          更多历史记录
        </button>
      )}
      {manifest && (
        <section>
          <h3>{snapshotLabel(manifest.snapshot_no)} 生效文件</h3>
          <p>
            策略 {manifest.policy_version} · generation {manifest.generation}
          </p>
          <div className="prompt-list">
            {manifest.items.map((row) => (
              <div key={row.path}>
                <code>{row.path}</code>
                <button
                  disabled={busy}
                  className="secondary"
                  onClick={() => void version(row.path, row.version)}
                >
                  查看 v{row.version}
                </button>
              </div>
            ))}
          </div>
          {manifest.next_cursor && (
            <button
              disabled={busy}
              className="secondary"
              onClick={() =>
                void action(async () => {
                  const next = await ops.get("snapshot", {
                    snapshot_no: String(manifest.snapshot_no),
                    cursor: manifest.next_cursor!,
                  });
                  setManifest({ ...next, items: [...manifest.items, ...next.items] });
                })
              }
            >
              更多快照文件
            </button>
          )}
          <button disabled={busy} onClick={() => onRestore(manifest.snapshot_no)}>
            恢复此快照
          </button>
        </section>
      )}
      {historical && (
        <section>
          <h3>
            {historical.path} · v{historical.version}
          </h3>
          {historical.path.endsWith(".json") ? (
            <UiCodeBlock code={historical.body ?? ""} language="json" title="历史 JSON 资产" />
          ) : (
            <AnalysisMarkdown source={historical.body ?? ""} embedded />
          )}
          {historical.retired && (
            <p className="muted">此文件已合并到结构化资产；整库恢复会按当前结构迁移历史正文。</p>
          )}
          {file && !historical.retired && (
            <button
              disabled={busy}
              className="secondary"
              onClick={() =>
                void action(async () => {
                  if (!window.confirm("将这个历史版本恢复为文件草稿？")) return;
                  await ops.write("draft-from-version", {
                    path: file.path,
                    version: historical.version,
                    expected_edit_rev: file.edit_rev,
                    base_epoch: file.active_epoch,
                  });
                  onSaved("已恢复为草稿。");
                })
              }
            >
              将此版本恢复为草稿
            </button>
          )}
        </section>
      )}
      {err && (
        <p role="alert">
          {err}{" "}
          <button className="secondary" onClick={() => setAttempt((x) => x + 1)}>
            重读记录
          </button>
        </p>
      )}
      <div className="actions">
        <button className="secondary" ref={cancel} disabled={busy} onClick={onClose}>
          关闭
        </button>
      </div>
    </Modal>
  );
}
