import { ChevronRight, FileText, Folder, Plus } from "lucide-react";
import { useState } from "react";
import type { Entry } from "./client";
type Branch = { dirs: Map<string, Branch>; files: Entry[] };
const branch = (): Branch => ({ dirs: new Map(), files: [] });
export function FileTree({
  items,
  selected,
  onSelect,
  searching,
  onCreate,
}: {
  items: Entry[];
  selected: string;
  onSelect: (p: string) => void;
  searching: boolean;
  onCreate: (p: string) => void;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const root = branch();
  for (const item of items) {
    let dir = root;
    for (const segment of item.path.split("/").slice(0, -1)) {
      const child = dir.dirs.get(segment) ?? branch();
      dir.dirs.set(segment, child);
      dir = child;
    }
    dir.files.push(item);
  }
  function children(dir: Branch, prefix: string, hidden = false) {
    return (
      <ul hidden={hidden}>
        {[...dir.dirs].map(([name, group]) => {
          const folder = prefix + name + "/";
          const open = searching || (expanded[folder] ?? true);
          return (
            <li key={folder}>
              <div className="prompt-directory-row">
                <button
                  className="secondary prompt-directory-toggle"
                  title={folder}
                  aria-expanded={open}
                  onClick={() => setExpanded((current) => ({ ...current, [folder]: !open }))}
                >
                  <ChevronRight className="prompt-tree-chevron" size={15} aria-hidden="true" />
                  <Folder size={15} aria-hidden="true" />
                  <span>{name}</span>
                </button>
                <button
                  className="secondary prompt-directory-add"
                  aria-label={`在 ${folder} 中新建提示词`}
                  title="新建提示词"
                  onClick={() => onCreate(folder)}
                >
                  <Plus size={16} aria-hidden="true" />
                </button>
              </div>
              {children(group, folder, !open)}
            </li>
          );
        })}
        {dir.files.map((item) => (
          <li key={item.path}>
            <button
              className="secondary"
              title={item.path}
              aria-current={selected === item.path ? "page" : undefined}
              onClick={() => onSelect(item.path)}
            >
              <FileText size={15} aria-hidden="true" />
              <span>{item.path.split("/").at(-1)}</span>
              {item.has_draft && <small>草稿</small>}
            </button>
          </li>
        ))}
      </ul>
    );
  }
  return children(root, "");
}
