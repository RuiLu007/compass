"use client";
import { useEffect, useRef, useState } from "react";
import { Modal } from "@/components/ui/dialog";
import { errorText, type Ops, type ImportChoice, type ImportRow } from "./client";

const states = {
  new: "新增文件",
  unchanged: "已对齐",
  local: "本地更新",
  online: "在线有修改",
  conflict: "两端都有修改",
};
export function ImportDialog({
  ops,
  onClose,
  onSaved,
}: {
  ops: Ops;
  onClose: () => void;
  onSaved: (msg: string) => void;
}) {
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [baseline, setBaseline] = useState<{
    confirmation_hash: string;
    package_digest: string;
  } | null>(null);
  const [choices, setChoices] = useState<Record<string, ImportChoice>>({});
  const [detail, setDetail] = useState<ImportRow | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const c = new AbortController();
    async function load() {
      const values: ImportRow[] = [];
      let cursor: string | null = null;
      const first = await ops.preview("import-preview", { limit: 100 }, c.signal);
      values.push(...first.items);
      cursor = first.next_cursor;
      while (cursor) {
        const next = await ops.preview("import-preview", { limit: 100, cursor }, c.signal);
        if (next.confirmation_hash !== first.confirmation_hash || next.next_cursor === cursor)
          throw Error("文件库已变化，请重新核对导入。");
        values.push(...next.items);
        cursor = next.next_cursor;
      }
      if (!c.signal.aborted) {
        setRows(values);
        setBaseline({
          confirmation_hash: first.confirmation_hash,
          package_digest: first.package_digest,
        });
        setError("");
      }
    }
    void load().catch((e) => {
      if (!c.signal.aborted) setError(errorText(e));
    });
    return () => c.abort();
  }, [ops, attempt]);
  async function compare(row: ImportRow) {
    setBusy(true);
    setError("");
    try {
      const preview = await ops.preview("import-preview", { path: row.path });
      const current = preview.items.find((r) => r.path === row.path);
      if (!current || preview.confirmation_hash !== baseline?.confirmation_hash)
        throw Error("导入基线已变化，请重新核对。");
      setDetail(current);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!baseline) return;
    setBusy(true);
    try {
      const r = await ops.write("import", { ...baseline, choices: Object.values(choices) });
      onSaved(
        "导入已完成，采纳 " +
          r.adopted +
          " 项，保留 " +
          r.retained +
          " 项，跳过 " +
          r.skipped +
          " 项。",
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="导入随包本地目录"
      description="按相对路径比较随包文件和线上正文，确认后写入草稿。"
      open
      wide
      initialFocus={cancel}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {!baseline && !error && <p role="status">正在读取本地清单…</p>}
      {baseline && (
        <>
          <p>
            随包文件 {rows.length} 个，需处理的冲突{" "}
            {rows.filter((r) => r.state === "conflict" && !choices[r.path]).length} 个。
          </p>
          <div className="prompt-list">
            {rows.map((row) => (
              <div key={row.path}>
                <code>{row.path}</code>
                <span>{states[row.state]}</span>
                <button
                  className="secondary compact"
                  disabled={busy}
                  onClick={() => void compare(row)}
                >
                  核对差异
                </button>
              </div>
            ))}
          </div>
        </>
      )}
      {detail && (
        <section>
          <h3>{detail.path}</h3>
          <div className="prompt-compare">
            {(
              [
                ["最近导入", detail.base_body],
                ["随包正文", detail.local_body],
                ["线上正文", detail.online_body],
              ] as const
            ).map(([name, body]) => (
              <details key={name} open>
                <summary>{name}</summary>
                <pre tabIndex={0}>{body ?? "（无正文）"}</pre>
              </details>
            ))}
          </div>
          <label>
            采用方式
            <select
              value={choices[detail.path]?.choice ?? ""}
              onChange={(e) => {
                const choice = e.target.value as ImportChoice["choice"];
                setChoices((current) => {
                  const next = { ...current };
                  if (choice)
                    next[detail.path] = {
                      path: detail.path,
                      choice,
                      ...(choice === "merge"
                        ? { body: detail.online_body ?? detail.local_body ?? "" }
                        : {}),
                    };
                  else delete next[detail.path];
                  return next;
                });
              }}
            >
              <option value="">按默认规则处理</option>
              <option value="online">保留线上</option>
              <option value="local">采用本地</option>
              <option value="merge">手工合并</option>
            </select>
          </label>
          {choices[detail.path]?.choice === "merge" && (
            <label>
              合并正文
              <textarea
                rows={6}
                value={choices[detail.path].body ?? ""}
                onChange={(e) =>
                  setChoices({
                    ...choices,
                    [detail.path]: { path: detail.path, choice: "merge", body: e.target.value },
                  })
                }
              />
            </label>
          )}
        </section>
      )}
      {error && (
        <p role="alert">
          {error}{" "}
          <button
            className="secondary"
            onClick={() => {
              setBaseline(null);
              setChoices({});
              setDetail(null);
              setAttempt((n) => n + 1);
            }}
          >
            重新核对
          </button>
        </p>
      )}
      <div className="actions">
        <button className="secondary" ref={cancel} disabled={busy} onClick={onClose}>
          取消
        </button>
        <button
          disabled={
            busy || !baseline || rows.some((r) => r.state === "conflict" && !choices[r.path])
          }
          onClick={() => void apply()}
        >
          {busy ? "正在处理…" : "确认导入草稿"}
        </button>
      </div>
    </Modal>
  );
}
