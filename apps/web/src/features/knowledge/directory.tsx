import { useRef } from "react";
import { DropdownMenu } from "radix-ui";
import { FileText, HardDrive, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { size, status, type Base, type Doc, type Chunk, type Dialog } from "./types";
type Show = (dialog: Dialog, trigger?: HTMLElement | null) => void;

function BaseCard({
  base,
  select,
  open,
}: {
  base: Base;
  select: (id: string) => void;
  open: Show;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const openingDialog = useRef(false);
  function show(dialog: Dialog) {
    openingDialog.current = true;
    open(dialog, trigger.current);
  }
  return (
    <article className="kb-card">
      <button
        className="kb-open"
        onClick={() => select(base.id)}
        title={[base.tenantName, base.workspaceName].filter(Boolean).join(" / ")}
      >
        <strong>{base.name}</strong>
        {base.description && <span className="kb-desc">{base.description}</span>}
        <small>
          <span>
            <FileText size={15} aria-hidden="true" />
            {base.files} 份文档
          </span>
          <span>
            <HardDrive size={15} aria-hidden="true" />
            {size(base.bytes)}
          </span>
        </small>
      </button>
      {base.canManage !== false && (
        <DropdownMenu.Root
          modal={false}
          onOpenChange={(value) => {
            if (value) openingDialog.current = false;
          }}
        >
          <DropdownMenu.Trigger
            ref={trigger}
            aria-label={base.name + " 更多操作"}
            className="kb-more"
          >
            <MoreHorizontal size={18} aria-hidden="true" />
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              className="dropdown kb-menu"
              align="end"
              sideOffset={4}
              collisionPadding={12}
              data-sensitive-portal
              onCloseAutoFocus={(event) => {
                if (openingDialog.current) event.preventDefault();
              }}
            >
              <DropdownMenu.Item onSelect={() => show({ type: "base", item: base })}>
                <Pencil size={16} aria-hidden="true" />
                编辑知识库
              </DropdownMenu.Item>
              <DropdownMenu.Separator />
              <DropdownMenu.Item
                className="kb-delete"
                onSelect={() => show({ type: "delete", item: base })}
              >
                <Trash2 size={16} aria-hidden="true" />
                删除知识库
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      )}
    </article>
  );
}

export function BaseCards({
  bases,
  select,
  open,
}: {
  bases: Base[];
  select: (id: string) => void;
  open: Show;
}) {
  return (
    <div className="kb-grid">
      {bases.map((base) => (
        <BaseCard key={base.id} base={base} select={select} open={open} />
      ))}
    </div>
  );
}

export function FileTable({
  docs,
  busy,
  select,
  show,
  retry,
  canManage,
}: {
  docs: Doc[];
  busy: boolean;
  select: (id: string) => void;
  show: Show;
  retry: (id: string) => void;
  canManage: boolean;
}) {
  return (
    <div className="kb-scroll">
      <table>
        <thead>
          <tr>
            {["文件名称", "文件状态", "格式", "文档大小", "字符数", "上传时间", "操作"].map((t) => (
              <th key={t}>{t}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.id}>
              <td>
                <button className="kb-link" onClick={() => select(d.id)}>
                  {d.name}
                </button>
              </td>
              <td>
                <span className="kb-status" data-state={d.status}>
                  {status[d.status]}
                </span>
                {d.error && <small>{d.error}</small>}
              </td>
              <td>{d.format.toUpperCase()}</td>
              <td>{size(d.bytes)}</td>
              <td>{d.chars.toLocaleString()}</td>
              <td>{new Date(d.uploaded).toLocaleString("zh-CN", { hour12: false })}</td>
              <td>
                <div className="kb-row-actions">
                  <button onClick={() => select(d.id)}>查看</button>
                  {canManage && (
                    <>
                      <button onClick={() => show({ type: "rename", item: d })}>编辑</button>
                      {d.status !== "ready" && (
                        <button disabled={busy} onClick={() => retry(d.id)}>
                          重试索引
                        </button>
                      )}
                      <button
                        className="kb-delete"
                        onClick={() => show({ type: "delete", item: d })}
                      >
                        删除
                      </button>
                    </>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ChunkCards({
  chunks,
  busy,
  show,
  toggle,
}: {
  chunks: Chunk[];
  busy: boolean;
  show: (c: Chunk) => void;
  toggle: (c: Chunk) => void;
}) {
  return (
    <div className="kb-grid">
      {chunks.map((c) => (
        <article className="kb-chunk" key={c.id} data-enabled={c.enabled}>
          <button className="kb-preview" onClick={() => show(c)}>
            <strong>分段 {c.ordinal}</strong>
            <span>{c.body}</span>
          </button>
          <button
            role="switch"
            aria-checked={c.enabled}
            title={c.enabled ? "已纳入检索" : "已停用"}
            aria-label={"分段 " + c.ordinal + " 参与检索"}
            className="kb-switch"
            disabled={busy}
            onClick={() => toggle(c)}
          >
            <i />
          </button>
        </article>
      ))}
    </div>
  );
}
