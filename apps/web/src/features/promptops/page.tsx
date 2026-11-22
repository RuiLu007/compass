"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  FilePlus2,
  FolderTree,
  History,
  LoaderCircle,
  Plus,
  Rocket,
  Upload,
  X,
} from "lucide-react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { Modal } from "@/components/ui/dialog";
import { promptClient, errorText, type Entry, type FileView, type Target } from "./client";
import { FileTree } from "./tree";
import { FileEditor } from "./editor";
import { ReleaseDialog } from "./release";
import { ImportDialog } from "./import-dialog";
import { HistoryDialog } from "./history";
import "./promptops.css";

type Overlay =
  | { type: "release"; target: Target }
  | { type: "new"; directory: string }
  | { type: "import" | "snapshots" | "history" };
export function PromptOpsPage() {
  const session = useDocumentSession();
  const ops = useMemo(() => promptClient(session), [session]);
  const [tree, setTree] = useState<Entry[]>([]),
    [file, setFile] = useState<FileView | null>(null);
  const [selected, setSelected] = useState(""),
    [search, setSearch] = useState(""),
    [loadedQuery, setLoadedQuery] = useState("");
  const [refresh, setRefresh] = useState(0),
    [treeLoading, setTreeLoading] = useState(true);
  const [fileBusy, setFileBusy] = useState(false),
    [creating, setCreating] = useState(false);
  const [overlay, setOverlay] = useState<Overlay | null>(null),
    [newName, setNewName] = useState(""),
    [newBody, setNewBody] = useState("");
  const [treeError, setTreeError] = useState(""),
    [fileError, setFileError] = useState(""),
    [newError, setNewError] = useState(""),
    [notice, setNotice] = useState("");
  const [showTree, setShowTree] = useState(true);
  const dirty = useRef(false),
    newNameInput = useRef<HTMLInputElement>(null);
  const newPath = (overlay?.type === "new" ? overlay.directory : "") + newName.trim();
  function onDirty(value: boolean) {
    dirty.current = value;
    session.setDirty(value);
  }
  function mayLeave() {
    return !dirty.current || window.confirm("放弃本文件尚未保存的修改？");
  }
  function choose(path: string) {
    if (path !== selected && !mayLeave()) return;
    if (path !== selected) {
      onDirty(false);
      setFile(null);
      setFileError("");
      setFileBusy(true);
      setSelected(path);
    }
    setShowTree(false);
  }
  function open(next: Overlay) {
    if (dirty.current) {
      setNotice("请先保存当前正文，或切换文件后放弃修改。");
      setShowTree(false);
      return;
    }
    if (next.type === "new") {
      setNewName("");
      setNewBody("");
      setNewError("");
    }
    setOverlay(next);
  }
  function saved(msg: string) {
    onDirty(false);
    setNotice(msg);
    setOverlay(null);
    setRefresh((x) => x + 1);
  }
  useEffect(() => {
    const c = new AbortController();
    const load = async () => {
      setTreeLoading(true);
      setTreeError("");
      const first = await ops.get("tree", { q: search, limit: "100" }, c.signal);
      const rows = [...first.items];
      let cursor = first.next_cursor;
      while (cursor) {
        const page = await ops.get("tree", { q: search, limit: "100", cursor }, c.signal);
        if (page.generation !== first.generation || cursor === page.next_cursor)
          throw Error("文件库已变化，请重新读取。");
        rows.push(...page.items);
        cursor = page.next_cursor;
      }
      if (!c.signal.aborted) {
        setTree(rows);
        setLoadedQuery(search);
      }
    };
    const timer = setTimeout(
      () =>
        void load()
          .catch((e) => {
            if (!c.signal.aborted) setTreeError(errorText(e));
          })
          .finally(() => {
            if (!c.signal.aborted) setTreeLoading(false);
          }),
      search ? 150 : 0,
    );
    return () => {
      c.abort();
      clearTimeout(timer);
    };
  }, [ops, search, refresh]);
  useEffect(() => {
    if (!selected) return;
    const c = new AbortController();
    void ops
      .get("file", { path: selected }, c.signal)
      .then((result) => {
        if (!c.signal.aborted) {
          setFile(result);
          setFileError("");
        }
      })
      .catch((e) => {
        if (!c.signal.aborted) {
          setFile(null);
          setFileError(errorText(e));
        }
      })
      .finally(() => {
        if (!c.signal.aborted) setFileBusy(false);
      });
    return () => c.abort();
  }, [ops, selected, refresh]);
  async function create() {
    if (overlay?.type !== "new" || !newName.trim()) return;
    if (/[\\/]/.test(newName) || !newName.trim().endsWith(".md")) {
      setNewError("请填写以 .md 结尾的文件名，目录由所选位置确定。");
      return;
    }
    const path = newPath;
    setCreating(true);
    setNewError("");
    try {
      await ops.write("file-draft", { path, body: newBody, expected_edit_rev: 0, base_epoch: 0 });
      setSelected(path);
      setShowTree(false);
      setFile(null);
      setSearch("");
      saved("已创建线上草稿。");
    } catch (e) {
      setNewError(errorText(e));
    } finally {
      setCreating(false);
    }
  }
  return (
    <div className="prompt-ops">
      <button className="secondary prompt-mobile-toggle" onClick={() => setShowTree(!showTree)}>
        <FolderTree size={16} aria-hidden="true" />
        {showTree ? "查看编辑区" : "查看文件树"}
      </button>
      <div className="prompt-layout" data-mobile-tree={showTree}>
        <aside className="prompt-tree" aria-label="提示词文件树">
          <div className="prompt-directory-row prompt-tree-root">
            <h3>prompts/</h3>
            <button
              className="secondary prompt-directory-add"
              aria-label="在根目录新建提示词"
              title="新建提示词"
              onClick={() => open({ type: "new", directory: "" })}
            >
              <Plus size={16} aria-hidden="true" />
            </button>
          </div>
          <label>
            搜索文件
            <input
              type="search"
              placeholder="路径、文件名或标题"
              maxLength={512}
              value={search}
              onChange={(e) => {
                setTreeLoading(true);
                setSearch(e.target.value);
              }}
            />
          </label>
          <div className="prompt-tree-scroll">
            {treeLoading && <p role="status">正在读取文件目录…</p>}
            {treeError && (
              <p role="alert">
                {treeError}{" "}
                <button className="secondary" onClick={() => setRefresh((x) => x + 1)}>
                  重新加载
                </button>
              </p>
            )}
            {!treeLoading && !treeError && !tree.length && (
              <p className="muted">
                {search ? "未找到匹配文件。" : "请从随包目录导入，或新建第一份文件。"}
              </p>
            )}
            <FileTree
              items={loadedQuery === search ? tree : []}
              selected={selected}
              onSelect={choose}
              onCreate={(directory) => open({ type: "new", directory })}
              searching={!!search}
            />
          </div>
        </aside>
        <div className="prompt-workspace">
          <header className="prompt-toolbar" aria-label="提示词工具栏">
            <div className="prompt-actions">
              <button className="secondary" onClick={() => open({ type: "import" })}>
                <Upload size={16} aria-hidden="true" /> 导入本地目录
              </button>
              <button className="secondary" onClick={() => open({ type: "snapshots" })}>
                <History size={16} aria-hidden="true" /> 整库快照
              </button>
              <button onClick={() => open({ type: "release", target: { action: "all" } })}>
                <Rocket size={16} aria-hidden="true" /> 整体发布
              </button>
            </div>
          </header>
          {notice && (
            <p role="status" className="ui-notice">
              {notice}
            </p>
          )}
          <div className="prompt-detail">
            {file && file.path === selected && !fileBusy ? (
              <FileEditor
                key={file.path + ":" + file.edit_rev + ":" + file.active_epoch}
                ops={ops}
                file={file}
                onDirty={onDirty}
                onSaved={saved}
                onHistory={() => open({ type: "history" })}
                onPublish={() =>
                  open({ type: "release", target: { action: "file", path: file.path } })
                }
              />
            ) : (
              <div className="ui-panel">
                <p role="status">
                  {fileBusy ? "正在读取文件…" : fileError || "从文件树中选择要查看或编辑的提示词。"}
                </p>
                {fileError && (
                  <button className="secondary" onClick={() => setRefresh((x) => x + 1)}>
                    重读文件
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
      {overlay?.type === "release" && (
        <ReleaseDialog
          ops={ops}
          target={overlay.target}
          onClose={() => setOverlay(null)}
          onSaved={saved}
        />
      )}
      {overlay?.type === "import" && (
        <ImportDialog ops={ops} onClose={() => setOverlay(null)} onSaved={saved} />
      )}
      {(overlay?.type === "snapshots" || overlay?.type === "history") && (
        <HistoryDialog
          ops={ops}
          file={overlay.type === "history" ? file : null}
          onClose={() => setOverlay(null)}
          onSaved={saved}
          onRestore={(n) =>
            setOverlay({ type: "release", target: { action: "restore", snapshot_no: n } })
          }
        />
      )}
      {overlay?.type === "new" && (
        <Modal
          open
          placement="right"
          initialFocus={newNameInput}
          busy={creating}
          footer={
            <div className="prompt-actions">
              <button className="secondary" disabled={creating} onClick={() => setOverlay(null)}>
                <X size={16} aria-hidden="true" /> 取消
              </button>
              <button disabled={creating || !newName.trim()} onClick={() => void create()}>
                {creating ? (
                  <LoaderCircle size={16} className="ui-spinner" aria-hidden="true" />
                ) : (
                  <FilePlus2 size={16} aria-hidden="true" />
                )}
                {creating ? "正在创建…" : "创建草稿"}
              </button>
            </div>
          }
          title="新建提示词"
          onClose={() => {
            if (!creating) setOverlay(null);
          }}
        >
          <div className="prompt-form">
            <label>
              相对路径
              <input value={overlay.directory || "./"} readOnly />
            </label>
            <label>
              文件名
              <input
                ref={newNameInput}
                value={newName}
                maxLength={512 - overlay.directory.length}
                placeholder="example.md"
                disabled={creating}
                onChange={(e) => {
                  setNewName(e.target.value);
                  setNewError("");
                }}
              />
            </label>
            <label>
              Markdown 正文
              <textarea
                rows={10}
                disabled={creating}
                value={newBody}
                onChange={(e) => setNewBody(e.target.value)}
              />
            </label>
            {newError && (
              <p role="alert">
                {newError}{" "}
                {tree.some((f) => f.path === newPath) && (
                  <button
                    className="secondary"
                    onClick={() => {
                      setOverlay(null);
                      choose(newPath);
                    }}
                  >
                    打开已有文件
                  </button>
                )}
              </p>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
