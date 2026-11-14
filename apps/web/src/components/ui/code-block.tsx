"use client";

import { Check, Copy, WrapText } from "lucide-react";
import { useState } from "react";
import { UiButton } from "./button";

export function UiCodeBlock({
  code,
  language,
  title,
}: {
  code: string;
  language?: string;
  title?: string;
}) {
  const [wrap, setWrap] = useState(false);
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const label = title ?? language ?? "代码";

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code);
      setCopy("copied");
    } catch {
      setCopy("failed");
    }
  }

  return (
    <section className="ui-code" aria-label={label}>
      <div className="ui-code-toolbar">
        <div className="ui-code-title">
          <strong>{label}</strong>
          {title && language && <span>{language}</span>}
        </div>
        <div className="ui-code-actions">
          <UiButton variant="quiet" aria-pressed={wrap} onClick={() => setWrap(!wrap)}>
            <WrapText size={15} aria-hidden="true" />
            自动换行
          </UiButton>
          <UiButton variant="quiet" onClick={() => void copyCode()} aria-label="复制代码">
            {copy === "copied" ? (
              <Check size={15} aria-hidden="true" />
            ) : (
              <Copy size={15} aria-hidden="true" />
            )}
            {copy === "copied" ? "已复制" : "复制"}
          </UiButton>
        </div>
      </div>
      <pre className="ui-code-scroll" tabIndex={0} data-wrap={wrap} aria-label={`${label}内容`}>
        <code>{code}</code>
      </pre>
      <span role="status" className={copy === "failed" ? "ui-code-feedback" : "sr-only"}>
        {copy === "copied"
          ? "代码已复制到剪贴板。"
          : copy === "failed"
            ? "复制失败，请选中代码手动复制。"
            : ""}
      </span>
    </section>
  );
}
