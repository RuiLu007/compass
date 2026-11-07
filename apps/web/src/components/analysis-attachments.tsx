"use client";

import { useLayoutEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  File as FileIcon,
  FileArchive,
  FileImage,
  FileSpreadsheet,
  FileText,
  Presentation,
  X,
} from "lucide-react";

export type ComposerAttachment = { id: string; file: File };

function fileDetails(file: File) {
  const extension = file.name.includes(".") ? file.name.split(".").at(-1)!.toLowerCase() : "";
  const type = extension ? extension.toUpperCase() : "文件";
  const size =
    file.size < 1024
      ? `${file.size} B`
      : file.size < 1024 * 1024
        ? `${Number((file.size / 1024).toFixed(1))} KB`
        : `${Number((file.size / (1024 * 1024)).toFixed(1))} MB`;
  if (["ppt", "pptx", "key"].includes(extension))
    return { type, size, Icon: Presentation, tone: "presentation" };
  if (["xls", "xlsx", "csv", "numbers"].includes(extension))
    return { type, size, Icon: FileSpreadsheet, tone: "spreadsheet" };
  if (file.type.startsWith("image/")) return { type, size, Icon: FileImage, tone: "image" };
  if (["zip", "rar", "gz", "7z"].includes(extension))
    return { type, size, Icon: FileArchive, tone: "archive" };
  if (["doc", "docx", "pdf", "txt", "md"].includes(extension))
    return { type, size, Icon: FileText, tone: "document" };
  return { type, size, Icon: FileIcon, tone: "file" };
}

export function AnalysisAttachments({
  items,
  disabled,
  onRemove,
}: {
  items: ComposerAttachment[];
  disabled: boolean;
  onRemove: (id: string) => void;
}) {
  const list = useRef<HTMLUListElement>(null);
  const [edges, setEdges] = useState({ overflow: false, left: false, right: false });

  function measure() {
    const node = list.current;
    if (!node) return;
    const overflow = node.scrollWidth - node.clientWidth > 1;
    setEdges((current) => {
      const next = {
        overflow,
        left: node.scrollLeft > 1,
        right: node.scrollLeft + node.clientWidth < node.scrollWidth - 1,
      };
      return Object.keys(next).every(
        (key) => next[key as keyof typeof next] === current[key as keyof typeof current],
      )
        ? current
        : next;
    });
  }

  useLayoutEffect(() => {
    const node = list.current;
    if (!node) return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    for (const item of node.children) observer.observe(item);
    measure();
    return () => observer.disconnect();
  }, [items]);

  function scroll(direction: -1 | 1) {
    const node = list.current;
    if (!node) return;
    node.scrollBy({ left: direction * Math.max(200, node.clientWidth * 0.75), behavior: "auto" });
  }

  if (items.length === 0) return null;
  return (
    <div className="composer-attachments" data-overflow={edges.overflow}>
      {edges.overflow && (
        <button
          type="button"
          className="composer-attachment-scroll composer-attachment-scroll-left"
          aria-label="向左查看附件"
          disabled={!edges.left}
          onClick={() => scroll(-1)}
        >
          <ChevronLeft size={18} aria-hidden="true" />
        </button>
      )}
      <ul
        ref={list}
        className="composer-attachment-list"
        aria-label={`已选择的附件（${items.length}/10）`}
        onScroll={measure}
      >
        {items.map(({ id, file }) => {
          const { type, size, Icon, tone } = fileDetails(file);
          return (
            <li key={id} className="composer-attachment">
              <span className="composer-attachment-icon" data-kind={tone}>
                <Icon size={24} aria-hidden="true" />
              </span>
              <div className="composer-attachment-details">
                <strong title={file.name}>{file.name}</strong>
                <span>
                  {type} · {size}
                </span>
              </div>
              <button
                type="button"
                className="composer-attachment-remove"
                aria-label={`移除附件 ${file.name}`}
                disabled={disabled}
                onClick={() => onRemove(id)}
              >
                <X size={14} aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ul>
      {edges.overflow && (
        <button
          type="button"
          className="composer-attachment-scroll composer-attachment-scroll-right"
          aria-label="向右查看附件"
          disabled={!edges.right}
          onClick={() => scroll(1)}
        >
          <ChevronRight size={18} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
