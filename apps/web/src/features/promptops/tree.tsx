import { ChevronRight, FileText, Folder } from "lucide-react";
import { useState } from "react";
import type { Entry } from "./client";
type Branch = { dirs: Map<string, Branch>; files: Entry[] };
const branch = (): Branch => ({ dirs: new Map(), files: [] });
export function FileTree({
  items,
  selected,
  onSelect,
  searching,
  onDirectory,
}: {
  items: Entry[];
  selected: string;
  onSelect: (p: string) => void;
  searching: boolean;
  onDirectory: (p: string) => void;
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
  function children(dir: Branch, prefix: string) {
    return (
      <ul>
        {[...dir.dirs].map(([name, group]) => {
          const folder = prefix + name + "/";
          return (
            <li key={folder}>
              <details
                open={searching || (expanded[folder] ?? selected.startsWith(folder))}
                onToggle={(event) => {
                  if (searching) return;
                  const open = event.currentTarget.open;
                  setExpanded((current) =>
                    current[folder] === open ? current : { ...current, [folder]: open },
                  );
                }}
              >
                <summary title={folder} onClick={() => onDirectory(folder)}>
                  <ChevronRight className="prompt-tree-chevron" size={15} aria-hidden="true" />
                  <Folder size={15} aria-hidden="true" />
                  <span>{name}</span>
                </summary>
                {children(group, folder)}
              </details>
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
