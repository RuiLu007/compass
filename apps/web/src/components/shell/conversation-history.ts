import type { SessionListItem } from "@/client/analysis-sessions";
import type { components } from "@/generated/api";

export type SessionList = components["schemas"]["SessionList"];

// PostgreSQL timestamps can differ within the same JavaScript millisecond.
function fractionalSecond(stamp: string) {
  return Number(stamp.match(/\.\d+/)?.[0] ?? 0);
}

function newestFirst(a: SessionListItem, b: SessionListItem) {
  return (
    Date.parse(b.updatedAt) - Date.parse(a.updatedAt) ||
    fractionalSecond(b.updatedAt) - fractionalSecond(a.updatedAt) ||
    b.sessionId.localeCompare(a.sessionId)
  );
}

export function mergeItems(current: SessionListItem[], incoming: SessionListItem[]) {
  const items = new Map(current.map((item) => [item.sessionId, item]));
  for (const item of incoming) {
    if ((items.get(item.sessionId)?.version ?? 0) <= item.version) items.set(item.sessionId, item);
  }
  return [...items.values()].sort(newestFirst);
}

export function refreshFirstPage(
  previous: SessionList | null,
  page: SessionList,
  deleted: Set<string>,
) {
  const first = page.items.filter((item) => !deleted.has(item.sessionId));
  const ids = new Set(first.map((item) => item.sessionId));
  const last = page.items.at(-1);
  const retained = (previous?.items ?? []).filter(
    (item) =>
      !deleted.has(item.sessionId) &&
      (ids.has(item.sessionId) || (page.nextCursor && last && newestFirst(item, last) > 0)),
  );
  // Keep the loaded tail's cursor only when the refreshed and cached ranges overlap.
  const overlaps = retained.some((item) => ids.has(item.sessionId));
  const hasOlder = retained.some((item) => !ids.has(item.sessionId));
  return {
    items: mergeItems(retained, first),
    nextCursor: previous && overlaps && hasOlder ? previous.nextCursor : page.nextCursor,
  };
}

export function groupByTime(items: SessionListItem[], now: Date) {
  const boundary = (daysAgo: number) =>
    new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo).getTime();
  const daysSinceMonday = (now.getDay() + 6) % 7;
  const periods = [
    { label: "今天", start: boundary(0) },
    { label: "昨天", start: boundary(1) },
    { label: "本周", start: boundary(daysSinceMonday) },
    { label: "上周", start: boundary(daysSinceMonday + 7) },
  ];
  const groups = new Map<string, SessionListItem[]>();
  for (const item of items) {
    const updated = Date.parse(item.updatedAt);
    const label = periods.find((period) => updated >= period.start)?.label ?? "更早";
    const group = groups.get(label);
    if (group) group.push(item);
    else groups.set(label, [item]);
  }
  return groups;
}
