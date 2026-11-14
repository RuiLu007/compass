"use client";

import { Check, Circle, CircleAlert, CircleDot, CircleMinus } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { RunAutonomyAction } from "@/contracts/analysis-run";

const labels = new Map([
  ["channel_summary", "比较渠道实付金额"],
  ["product_breakdown", "分析商品贡献"],
]);
const statusLabels = {
  pending: "待处理",
  failed: "查询未通过",
  verified: "已完成",
  skipped: "已跳过",
  active: "进行中",
};
const statusIcons = {
  pending: Circle,
  failed: CircleAlert,
  verified: Check,
  skipped: CircleMinus,
  active: CircleDot,
};

function todoLabel(item: RunAutonomyAction["items"][number], showScope: boolean) {
  const label = labels.get(item.goal) ?? item.goal;
  const { period, compareTo, channelCode } = item;
  if (!showScope || !period || !compareTo) return label;
  return `${label}${channelCode ? ` · ${channelCode}` : ""} · ${period.start} / ${compareTo.start}`;
}

export function planResolved(plan: RunAutonomyAction) {
  return (
    plan.items.length > 0 &&
    plan.items.every((item) => item.status === "verified" || item.status === "skipped")
  );
}

export function AnalysisTodoPanel({
  plan,
  onFocusReturn,
}: {
  plan: RunAutonomyAction;
  onFocusReturn: () => void;
}) {
  const [visibility, setVisibility] = useState<"closed" | "preview" | "pinned">("closed");
  const [hiddenVersion, setHiddenVersion] = useState<number>();
  const panel = useRef<HTMLElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const resolved = planResolved(plan);
  const total = plan.items.length;
  const selectedIndex = plan.items.findIndex((item) => item.id === plan.selectedTodoId);
  const selected = plan.items[selectedIndex];
  const currentIndex = selected
    ? selectedIndex
    : plan.items.findIndex((item) => item.status !== "verified" && item.status !== "skipped");
  const current = resolved ? total : currentIndex + 1;
  const complete = plan.items.filter(
    (item) => item.status === "verified" || item.status === "skipped",
  ).length;
  const headline = resolved
    ? "计划已完成"
    : selected
      ? todoLabel(selected, plan.items.filter((item) => item.goal === selected.goal).length > 1)
      : "准备执行计划";
  const hidden = total === 0 || (resolved && hiddenVersion === plan.planVersion);
  const expanded = !hidden && visibility !== "closed";

  useEffect(() => {
    if (!expanded) return;
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !panel.current?.contains(event.target)) {
        setVisibility("closed");
      }
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (panel.current?.contains(document.activeElement)) toggle.current?.focus();
      setVisibility("closed");
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [expanded]);

  useEffect(() => {
    if (!resolved) return;
    const timeout = window.setTimeout(() => {
      if (panel.current?.contains(document.activeElement)) onFocusReturn();
      setHiddenVersion(plan.planVersion);
    }, 1100);
    return () => window.clearTimeout(timeout);
  }, [resolved, plan.planVersion, onFocusReturn]);

  if (hidden) return null;

  return (
    <section
      ref={panel}
      className="analysis-todo"
      aria-label="分析计划"
      data-complete={resolved}
      onPointerEnter={(event) => {
        if (event.pointerType === "mouse") {
          setVisibility((previous) => (previous === "closed" ? "preview" : previous));
        }
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === "mouse") {
          setVisibility((previous) => (previous === "preview" ? "closed" : previous));
        }
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          setVisibility((previous) => (previous === "pinned" ? "closed" : previous));
        }
      }}
    >
      <button
        ref={toggle}
        type="button"
        className="analysis-todo-toggle"
        onClick={() => setVisibility((previous) => (previous === "pinned" ? "closed" : "pinned"))}
        aria-expanded={expanded}
        aria-controls={listId}
        aria-label={`分析计划，第 ${current} / ${total} 步，${headline}`}
      >
        <svg
          className="analysis-todo-ring"
          width="18"
          height="18"
          viewBox="0 0 20 20"
          aria-hidden="true"
        >
          <circle className="analysis-todo-ring-track" cx="10" cy="10" r="7.5" />
          <circle
            className="analysis-todo-ring-progress"
            cx="10"
            cy="10"
            r="7.5"
            pathLength={1}
            strokeDasharray={1}
            strokeDashoffset={1 - complete / total}
            transform="rotate(-90 10 10)"
          />
          {resolved && <path d="m7 10 2 2 4-4" />}
        </svg>
        <span className="analysis-todo-count">
          第 {current} / {total} 步
        </span>
        <span className="analysis-todo-separator" aria-hidden="true">
          ·
        </span>
        <span className="analysis-todo-headline" data-active={Boolean(selected) && !resolved}>
          {headline}
        </span>
      </button>
      <span className="analysis-todo-sr-only" role="status">
        第 {current} / {total} 步，{headline}
      </span>
      <div className="analysis-todo-popover" hidden={!expanded}>
        <ul
          className="analysis-todo-items"
          id={listId}
          role="list"
          aria-label="计划事项"
          tabIndex={0}
        >
          {plan.items.map((item) => {
            const status = item.id === plan.selectedTodoId ? "active" : item.status;
            const Icon = statusIcons[status];
            return (
              <li
                key={item.id}
                data-status={status}
                aria-current={status === "active" ? "step" : undefined}
              >
                <Icon size={15} aria-hidden="true" />
                <span className="analysis-todo-label">{todoLabel(item, true)}</span>
                <span className="analysis-todo-status">{statusLabels[status]}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
