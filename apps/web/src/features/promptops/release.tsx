"use client";
import { useEffect, useRef, useState } from "react";
import { Modal } from "@/components/ui/dialog";
import { AnalysisMarkdown } from "@/components/analysis-markdown";
import { UiCodeBlock } from "@/components/ui/code-block";
import {
  errorText,
  snapshotLabel,
  versionLabel,
  type Ops,
  type Target,
  type Change,
  type FileView,
  type ReleasePreview,
} from "./client";

function Changes({ items }: { items: Change[] }) {
  return items.length ? (
    <ul className="prompt-changes">
      {items.map((row) => (
        <li key={row.path}>
          <code>{row.path}</code> · {versionLabel(row.old_version)} →{" "}
          {versionLabel(row.new_version)}
          {row.usages.length > 0 && <p>{row.usages.join("、")}</p>}
        </li>
      ))}
    </ul>
  ) : (
    <p className="muted">文件版本清单相同。</p>
  );
}
export function ReleaseDialog({
  ops,
  target,
  onClose,
  onSaved,
}: {
  ops: Ops;
  target: Target;
  onClose: () => void;
  onSaved: (msg: string) => void;
}) {
  const [preview, setPreview] = useState<ReleasePreview | null>(null);
  const [file, setFile] = useState<FileView | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    const fetchPreview = async () => {
      const result = await ops.preview("release-preview", target, controller.signal);
      const candidate =
        target.action === "file" && target.path
          ? await ops.get("file", { path: target.path }, controller.signal)
          : null;
      if (!controller.signal.aborted) {
        setPreview(result);
        setFile(candidate);
        setError("");
      }
    };
    void fetchPreview().catch((e) => {
      if (!controller.signal.aborted) setError(errorText(e));
    });
    return () => controller.abort();
  }, [ops, target, attempt]);
  async function submit() {
    if (!preview || !note.trim() || preview.errors.length) return;
    setBusy(true);
    setError("");
    try {
      const receipt = await ops.write("releases", {
        ...target,
        confirmation_hash: preview.confirmation_hash,
        note: note.trim(),
      });
      onSaved(
        receipt.snapshot_no
          ? "已提交整库快照 " + snapshotLabel(receipt.snapshot_no) + "。"
          : "发布完成，文件版本 " + versionLabel(receipt.version) + "。",
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const title =
    target.action === "file"
      ? "发布此文件"
      : target.action === "all"
        ? "整体发布"
        : "恢复快照 " + snapshotLabel(target.snapshot_no!);
  return (
    <Modal
      title={title}
      open
      wide
      initialFocus={cancel}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {!preview && !error && <p role="status">读取发布清单…</p>}
      {target.path && (
        <p>
          <code>{target.path}</code>
        </p>
      )}
      {preview && (
        <>
          <h3>本次生效变更</h3>
          <Changes items={preview.changes} />
          {target.action !== "file" && (
            <details>
              <summary>相对最近快照的变化</summary>
              <Changes items={preview.since_snapshot} />
            </details>
          )}
          {file && (
            <details open>
              <summary>当前版本与待发布正文</summary>
              <div className="prompt-compare">
                <div>
                  <h4>当前生效</h4>
                  {file.path.endsWith(".json") ? (
                    <UiCodeBlock code={file.body ?? ""} language="json" title="当前 JSON 资产" />
                  ) : (
                    <AnalysisMarkdown source={file.body ?? ""} embedded />
                  )}
                </div>
                <div>
                  <h4>候选正文</h4>
                  {file.path.endsWith(".json") ? (
                    <UiCodeBlock
                      code={file.draft_body ?? file.body ?? ""}
                      language="json"
                      title="候选 JSON 资产"
                    />
                  ) : (
                    <AnalysisMarkdown source={file.draft_body ?? file.body ?? ""} embedded />
                  )}
                </div>
              </div>
            </details>
          )}
          {preview.errors.length > 0 && (
            <ul role="alert">
              {preview.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          <label>
            发布说明
            <textarea
              rows={2}
              maxLength={500}
              disabled={busy}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
        </>
      )}
      {error && (
        <p role="alert">
          {error}{" "}
          <button
            className="secondary"
            onClick={() => {
              setPreview(null);
              setAttempt((n) => n + 1);
            }}
          >
            重新预览
          </button>
        </p>
      )}
      <div className="actions">
        <button ref={cancel} className="secondary" disabled={busy} onClick={onClose}>
          取消
        </button>
        <button
          disabled={busy || !preview || !!preview.errors.length || !note.trim()}
          onClick={() => void submit()}
        >
          {busy ? "正在提交…" : "确认" + title}
        </button>
      </div>
    </Modal>
  );
}
