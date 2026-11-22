"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import {
  ArrowLeft,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Pencil,
  Plus,
  Search,
  Upload,
} from "lucide-react";
import type { SessionView } from "@/contracts/page";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { UiButton } from "@/components/ui/button";
import { UiPageHeading } from "@/components/ui/page-heading";
import { Modal } from "@/components/ui/dialog";
import { RetrievalDrawer } from "./retrieval-drawer";
import { BaseCards, FileTable, ChunkCards } from "./directory";
import { size, type Base, type Doc, type Chunk, type Dialog, type LibraryScope } from "./types";
import "./knowledge.css";

type Api = <T>(
  path: string,
  method?: string,
  input?: unknown,
  signal?: AbortSignal,
  owner?: LibraryScope,
) => Promise<T>;
function selection(): [string, string] {
  const h = new URLSearchParams(window.location.hash.slice(1));
  return [h.get("kb") ?? "", h.get("file") ?? ""];
}
function go(base = "", file = "") {
  const h = new URLSearchParams();
  if (base) h.set("kb", base);
  if (file) h.set("file", file);
  window.location.hash = h.toString();
}
function readFile(file: File) {
  return new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(new Error("无法读取文件。"));
    r.onload = () => resolve(String(r.result).split(",")[1]);
    r.readAsDataURL(file);
  });
}
function errorText(e: unknown) {
  return e instanceof Error ? e.message : "操作失败，请重试。";
}

export function KnowledgePage({ session }: { session: SessionView }) {
  const api = useDocumentSession();
  const platform = session.isPlatformAdmin;
  const scope = new URLSearchParams({
    tenantId: session.scope.tenantId!,
    workspaceId: session.scope.workspaceId!,
  }).toString();
  const call: Api = useCallback(
    <T,>(
      path: string,
      method = "GET",
      input?: unknown,
      signal?: AbortSignal,
      owner?: LibraryScope,
    ) =>
      api.request<T>(
        (platform && method === "GET" ? "knowledge/catalog" : "knowledge/bases") +
          path +
          "?" +
          (owner ? new URLSearchParams(owner).toString() : scope),
        method,
        input,
        {
          retainOnNotFound: true,
          timeoutMs: 300000,
          signal,
        },
      ),
    [api, scope, platform],
  );
  const [[base, file], setSelection] = useState(["", ""]);
  useEffect(() => {
    const update = () => setSelection(selection());
    update();
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return (
    <Library
      key={[scope, base, file].join("/")}
      base={base}
      file={file}
      call={call}
      scope={scope}
      platform={platform}
      workspaceName={session.workspace?.name ?? "当前工作区"}
    />
  );
}

function Library({
  base,
  file,
  call: request,
  scope,
  platform,
  workspaceName,
}: {
  base: string;
  file: string;
  call: Api;
  scope: string;
  platform: boolean;
  workspaceName: string;
}) {
  const [bases, setBases] = useState<Base[]>([]),
    [docs, setDocs] = useState<Doc[]>([]),
    [chunks, setChunks] = useState<Chunk[]>([]);
  const [canCreate, setCanCreate] = useState(false);
  const [testing, setTesting] = useState(false);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [refresh, setRefresh] = useState(0);
  const [dialog, setDialog] = useState<Dialog | null>(null),
    [busy, setBusy] = useState(false),
    [dialogError, setDialogError] = useState("");
  const [value, setValue] = useState(""),
    [description, setDescription] = useState(""),
    [editing, setEditing] = useState(false);
  const [files, setFiles] = useState<File[]>([]),
    [progress, setProgress] = useState(""),
    [drag, setDrag] = useState(false);
  const [query, setQuery] = useState(""),
    [filter, setFilter] = useState(""),
    [page, setPage] = useState(1);
  const [actionError, setActionError] = useState("");
  const dialogTrigger = useRef<HTMLElement | null>(null);
  const first = useRef<HTMLInputElement>(null),
    cancel = useRef<HTMLButtonElement>(null);
  const basePath = "/" + base + "/files",
    filePath = basePath + "/" + file;
  const kb = bases.find((b) => b.id === base),
    doc = docs.find((d) => d.id === file);
  const canManage = base ? !!kb && kb.canManage !== false : canCreate;
  function call<T>(path: string, method: string, input: unknown, source: Base | undefined = kb) {
    const owner =
      source?.tenantId && source.workspaceId
        ? { tenantId: source.tenantId, workspaceId: source.workspaceId }
        : undefined;
    return request<T>(path, method, input, undefined, owner);
  }
  useEffect(() => {
    const ctrl = new AbortController();
    Promise.all([
      platform
        ? request<{ bases: Base[]; canCreate: boolean }>("", "GET", undefined, ctrl.signal)
        : request<Base[]>("", "GET", undefined, ctrl.signal).then((bases) => ({
            bases,
            canCreate: true,
          })),
      base ? request<Doc[]>(basePath, "GET", undefined, ctrl.signal) : [],
      file ? request<Chunk[]>(filePath + "/chunks", "GET", undefined, ctrl.signal) : [],
    ])
      .then(([b, d, c]) => {
        if (!ctrl.signal.aborted) {
          setBases(b.bases);
          setCanCreate(b.canCreate);
          setDocs(d);
          setChunks(c);
          setError("");
        }
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) setError(errorText(e));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [request, platform, base, file, basePath, filePath, refresh]);
  const matches = (s: string) => s.toLocaleLowerCase().includes(filter.toLocaleLowerCase());
  const filteredDocs = docs.filter((d) => matches(d.name)),
    filteredChunks = chunks.filter((c) => matches(c.body + " " + c.heading));
  const count = file ? filteredChunks.length : filteredDocs.length,
    limit = file ? 9 : 10,
    last = Math.max(1, Math.ceil(count / limit)),
    current = Math.min(page, last);
  function open(d: Dialog, trigger?: HTMLElement | null) {
    dialogTrigger.current = trigger ?? (document.activeElement as HTMLElement | null);
    setDialogError("");
    setDialog(d);
    setProgress("");
    setEditing(false);
    setFiles([]);
    setValue(d.type === "chunk" ? d.item.body : "item" in d && d.item ? d.item.name : "");
    setDescription(d.type === "base" ? (d.item?.description ?? "") : "");
  }
  async function action(work: () => Promise<unknown>, message: string, modal = false) {
    setBusy(true);
    setActionError("");
    setError("");
    setDialogError("");
    setNotice("");
    try {
      await work();
      if (modal) setDialog(null);
      setNotice(message);
    } catch (e) {
      (modal ? setDialogError : setActionError)(errorText(e));
    } finally {
      setBusy(false);
      setRefresh((v) => v + 1);
    }
  }
  function selectFiles(selected: File[]) {
    setDialogError("");
    const valid = selected.every(
      (f) => /\.(pdf|md|txt)$/i.test(f.name) && f.size > 0 && f.size <= 5 * 1024 * 1024,
    );
    if (!valid) {
      setDialogError("请选择非空的 PDF、Markdown 或 TXT 文件，每个文件最多 5 MB。");
      return;
    }
    setFiles((f) => [...f, ...selected]);
  }
  async function save() {
    if (!dialog) return;
    const d = dialog;
    if (d.type === "upload") {
      const failed: string[] = [];
      await action(
        async () => {
          for (const [i, f] of files.entries()) {
            setProgress(`正在处理 ${i + 1}/${files.length} · ${f.name}`);
            try {
              const { id } = await call<{ id: string }>(basePath, "POST", {
                id: crypto.randomUUID(),
                name: f.name,
                content: await readFile(f),
              });
              await call(basePath + "/" + id + "/index", "POST", {});
            } catch (e) {
              failed.push(f.name + " · " + errorText(e));
            }
          }
          setProgress("");
          setFiles([]);
          if (failed.length) throw new Error(failed.join("\n"));
        },
        "所选文档已入库。",
        true,
      );
      return;
    }
    if (d.type === "chunk" && !editing) {
      setDialog(null);
      return;
    }
    await action(
      async () => {
        if (d.type === "base")
          return call(
            d.item ? "/" + d.item.id : "",
            d.item ? "PATCH" : "POST",
            {
              name: value.trim(),
              description,
              ...(d.item ? { revision: d.item.revision } : {}),
            },
            d.item,
          );
        if (d.type === "rename")
          return call(basePath + "/" + d.item.id, "PATCH", {
            name: value.trim(),
            revision: d.item.revision,
          });
        if (d.type === "delete")
          return call(
            ("format" in d.item ? basePath : "") + "/" + d.item.id,
            "DELETE",
            { revision: d.item.revision },
            "format" in d.item ? kb : d.item,
          );
        if (d.type === "chunk")
          return call(filePath + "/chunks/" + d.item.id, "PATCH", {
            revision: d.item.revision,
            body: value,
          });
      },
      d.type === "chunk" ? "分段正文与向量已更新。" : d.type === "delete" ? "已删除。" : "已保存。",
      true,
    );
  }
  return (
    <section className="knowledge" aria-label="知识库管理">
      {base ? (
        <UiPageHeading title={file ? (doc?.name ?? "文档分段") : (kb?.name ?? "知识库")}>
          <button className="kb-back" onClick={() => go(file ? base : "")}>
            <ArrowLeft size={16} />
            {file ? "返回文档列表" : "返回知识库"}
          </button>
          {file && doc && (
            <a
              className="ui-link-button"
              href={
                (platform ? "/api/web/v1/knowledge/catalog" : "/api/web/v1/knowledge/bases") +
                filePath +
                "/original?" +
                scope
              }
              target="_blank"
              rel="noreferrer"
            >
              查看原文
            </a>
          )}
          {!file && canManage && (
            <UiButton onClick={() => open({ type: "upload" })}>
              <Upload size={16} /> 导入文档
            </UiButton>
          )}
        </UiPageHeading>
      ) : (
        <div className="kb-toolbar" role="toolbar" aria-label="知识库操作">
          <UiButton
            variant="secondary"
            onClick={() => setTesting(true)}
            disabled={loading || !!error}
          >
            <Search size={16} aria-hidden="true" /> 检索测试
          </UiButton>
          {canCreate && (
            <UiButton onClick={() => open({ type: "base" })}>
              <Plus size={16} /> 新建知识库
            </UiButton>
          )}
        </div>
      )}
      {base && (
        <form
          className="kb-filters"
          onSubmit={(e) => {
            e.preventDefault();
            setFilter(query.trim());
            setPage(1);
          }}
        >
          <label className="kb-search">
            <Search size={16} />
            <input
              aria-label={file ? "分段搜索" : "按文件名搜索"}
              placeholder={file ? "搜索分段内容" : "输入文件名称"}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <UiButton type="submit" variant="secondary">
            搜索
          </UiButton>
        </form>
      )}
      {(error || actionError) && (
        <p role="alert" className="kb-error">
          {error || actionError}{" "}
          <button
            onClick={() => {
              setActionError("");
              setRefresh((x) => x + 1);
            }}
          >
            重新加载
          </button>
        </p>
      )}
      {notice && (
        <p role="status" className="kb-notice">
          {notice}
        </p>
      )}
      {loading ? (
        <p role="status">正在读取知识库…</p>
      ) : !base ? (
        <BaseCards bases={bases} select={(id) => go(id)} open={open} />
      ) : !file ? (
        <FileTable
          canManage={canManage}
          docs={filteredDocs.slice((current - 1) * limit, current * limit)}
          busy={busy}
          select={(id) => go(base, id)}
          show={open}
          retry={(id) =>
            action(() => call(basePath + "/" + id + "/index", "POST", {}), "文档已入库。")
          }
        />
      ) : (
        <ChunkCards
          chunks={filteredChunks.slice((current - 1) * limit, current * limit)}
          busy={busy || !canManage}
          show={(item) => open({ type: "chunk", item })}
          toggle={(c) =>
            action(
              () =>
                call(filePath + "/chunks/" + c.id, "PATCH", {
                  revision: c.revision,
                  enabled: !c.enabled,
                }),
              c.enabled ? "分段已退出检索。" : "分段已启用。",
            )
          }
        />
      )}
      {!loading && (!base ? !bases.length : !count) && !error && (
        <div className="kb-empty">
          <BookOpen size={30} />
          <p>
            {!base
              ? platform
                ? "平台暂无知识库"
                : workspaceName + "暂无知识库"
              : filter
                ? "没有匹配的结果"
                : file
                  ? doc?.error || "这个文件暂无分段。"
                  : "这个知识库还没有文档。"}
          </p>
        </div>
      )}
      {base && !loading && (
        <nav className="kb-pages" aria-label="分页">
          <span>共 {count} 条</span>
          <button aria-label="上一页" disabled={current <= 1} onClick={() => setPage(current - 1)}>
            <ChevronLeft size={16} />
          </button>
          {Array.from({ length: last }, (_, i) => i + 1)
            .filter((n) => n === 1 || n === last || Math.abs(n - current) <= 2)
            .map((n) => (
              <button
                key={n}
                aria-current={n === current ? "page" : undefined}
                onClick={() => setPage(n)}
              >
                {n}
              </button>
            ))}
          <button
            aria-label="下一页"
            disabled={current >= last}
            onClick={() => setPage(current + 1)}
          >
            <ChevronRight size={16} />
          </button>
        </nav>
      )}
      {testing && (
        <RetrievalDrawer
          bases={bases}
          scope={scope}
          platform={platform}
          onClose={() => setTesting(false)}
        />
      )}
      {dialog && (
        <Modal
          open
          onClose={() => setDialog(null)}
          busy={busy}
          initialFocus={dialog.type === "delete" ? cancel : first}
          returnFocus={() => dialogTrigger.current}
          wide={dialog.type === "chunk" || dialog.type === "upload"}
          title={
            dialog.type === "chunk"
              ? "分段 " + dialog.item.ordinal
              : dialog.type === "upload"
                ? "上传知识库文档"
                : dialog.type === "delete"
                  ? "确认删除"
                  : dialog.type === "base"
                    ? dialog.item
                      ? "编辑知识库"
                      : "新建知识库"
                    : "编辑文件名称"
          }
          description={
            dialog.type === "base" && !dialog.item ? "所属工作区 · " + workspaceName : undefined
          }
          footer={
            <>
              <UiButton
                ref={cancel}
                variant="secondary"
                disabled={busy}
                onClick={() => setDialog(null)}
              >
                取消
              </UiButton>
              <UiButton
                busy={busy}
                variant={dialog.type === "delete" ? "danger" : "primary"}
                disabled={
                  dialog.type === "upload"
                    ? !files.length
                    : dialog.type === "delete"
                      ? false
                      : !value.trim()
                }
                onClick={save}
              >
                {busy
                  ? "处理中"
                  : dialog.type === "delete"
                    ? "删除"
                    : dialog.type === "upload"
                      ? "上传并入库"
                      : "确认"}
              </UiButton>
            </>
          }
        >
          {dialog.type === "upload" ? (
            <div className="kb-form">
              <label>上传文件</label>
              <label
                className="kb-drop"
                data-drag={drag}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDrag(true);
                }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDrag(false);
                  if (!busy) selectFiles(Array.from(e.dataTransfer.files));
                }}
              >
                <Upload size={34} />
                <strong>点击或拖入文件</strong>
                <span>文本型 PDF、Markdown、TXT · 每个最多 5 MB</span>
                <input
                  ref={first}
                  type="file"
                  multiple
                  disabled={busy}
                  accept=".md,.txt,.pdf"
                  aria-label="选择文档"
                  onChange={(e) => {
                    selectFiles(Array.from(e.target.files ?? []));
                    e.target.value = "";
                  }}
                />
              </label>
              {files.map((f, i) => (
                <div className="kb-picked" key={i}>
                  <span>
                    {f.name} · {size(f.size)}
                  </span>
                  <button disabled={busy} onClick={() => setFiles(files.filter((_, j) => i !== j))}>
                    移除
                  </button>
                </div>
              ))}
              {progress && <p role="status">{progress}</p>}
            </div>
          ) : dialog.type === "delete" ? (
            <p>删除「{dialog.item.name}」后，其中的分段将退出工作区检索。</p>
          ) : dialog.type === "chunk" ? (
            <div className="kb-content">
              <div>
                <strong>分段内容</strong>
                {!editing && canManage && (
                  <UiButton variant="quiet" onClick={() => setEditing(true)}>
                    <Pencil size={15} /> 编辑
                  </UiButton>
                )}
              </div>
              {editing ? (
                <textarea
                  aria-label="分段内容"
                  autoFocus
                  value={value}
                  maxLength={8192}
                  onChange={(e) => setValue(e.target.value)}
                />
              ) : (
                <pre>{value}</pre>
              )}
            </div>
          ) : (
            <div className="kb-form">
              <label>
                {dialog.type === "base" ? "知识库名称" : "文件名称"}
                <input
                  ref={first}
                  maxLength={dialog.type === "base" ? 100 : 180}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              </label>
              {dialog.type === "base" && (
                <label>
                  说明（选填）
                  <textarea
                    maxLength={500}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </label>
              )}
            </div>
          )}
          {dialogError && (
            <p role="alert" className="kb-error">
              {dialogError}
            </p>
          )}
        </Modal>
      )}
    </section>
  );
}
