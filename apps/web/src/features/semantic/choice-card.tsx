"use client";

import {
  ArrowRight,
  BookmarkPlus,
  Clock3,
  ListFilter,
  PencilLine,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { UiButton } from "@/components/ui/button";
import type { components } from "@/generated/api";
import "./chat-cards.css";

export const MAX_CARD_ITEMS = 3;
type Wait = components["schemas"]["WaitView"];
type Choice = NonNullable<Wait["options"]>[number];
type Answer = { choiceId: string } | { customDefinition: string };

export function SemanticCardHeader({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children?: ReactNode;
}) {
  return (
    <header className="semantic-chat-header">
      <span className="semantic-chat-icon">
        <Icon size={19} strokeWidth={1.7} aria-hidden="true" />
      </span>
      <h3>{title}</h3>
      {children}
    </header>
  );
}

export function SemanticChoiceCard({
  kind,
  options,
  waitId,
  expiresAt,
  busy,
  canApprove,
  canReject,
  onApprove,
  onReject,
}: {
  kind: "choice" | "result";
  options: Choice[];
  waitId: string;
  expiresAt?: string | null;
  busy: boolean;
  canApprove: boolean;
  canReject: boolean;
  onApprove: (answer: Answer) => void;
  onReject: () => void;
}) {
  const [selected, select] = useState("");
  const [definition, setDefinition] = useState("");
  const metric = kind === "choice";
  const custom = metric && selected === "custom";
  const visible = options.slice(0, MAX_CARD_ITEMS);
  const ready = custom ? Boolean(definition.trim()) : visible.some((o) => o.id === selected);
  const deadline = expiresAt && new Date(expiresAt);
  return (
    <section className="semantic-chat-card" aria-label="执行确认">
      <SemanticCardHeader
        icon={metric ? ListFilter : BookmarkPlus}
        title={metric ? "选择本次查询口径" : "选择要保存的查询结果"}
      >
        {deadline && (
          <time
            className="semantic-card-time"
            dateTime={deadline.toISOString()}
            title={deadline.toLocaleString("zh-CN")}
          >
            <Clock3 size={14} aria-hidden="true" />
            {deadline.toLocaleString("zh-CN", {
              month: "numeric",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              hour12: false,
            })}{" "}
            前确认
          </time>
        )}
      </SemanticCardHeader>
      <fieldset className="semantic-choices" disabled={busy || !canApprove}>
        <legend className="sr-only">{metric ? "本次查询口径" : "要保存的查询结果"}</legend>
        {visible.map((o) => (
          <label className="semantic-option" key={o.id}>
            <input
              type="radio"
              name={waitId}
              value={o.id}
              checked={selected === o.id}
              onChange={() => select(o.id)}
            />
            <span className="semantic-option-copy">
              <span className="semantic-option-heading">
                <strong>{o.name}</strong>
                {metric && (
                  <small>
                    v{o.version}
                    {o.unit && " · " + (o.unit === "CNY_fen" ? "人民币分" : o.unit)}
                  </small>
                )}
              </span>
              {o.description && (
                <span className="semantic-option-description">{o.description}</span>
              )}
            </span>
          </label>
        ))}
      </fieldset>
      {metric && canApprove && (
        <div className="semantic-custom">
          <button
            type="button"
            className="semantic-custom-toggle"
            disabled={busy}
            aria-expanded={custom}
            aria-controls={`custom-${waitId}`}
            onClick={() => select(custom ? "" : "custom")}
          >
            <PencilLine size={15} aria-hidden="true" /> 都不合适，我来补充算法
          </button>
          {custom && (
            <label className="semantic-custom-field" id={`custom-${waitId}`}>
              本次计算方法
              <textarea
                rows={3}
                maxLength={1600}
                required
                disabled={busy}
                value={definition}
                onChange={(e) => setDefinition(e.target.value)}
              />
            </label>
          )}
        </div>
      )}
      <footer className="semantic-card-footer">
        {canReject && (
          <UiButton variant="quiet" disabled={busy} onClick={onReject}>
            取消本次查询
          </UiButton>
        )}
        {canApprove && (
          <UiButton
            busy={busy}
            disabled={!ready}
            onClick={() =>
              onApprove(custom ? { customDefinition: definition.trim() } : { choiceId: selected })
            }
          >
            选择并继续 <ArrowRight size={15} aria-hidden="true" />
          </UiButton>
        )}
      </footer>
    </section>
  );
}
