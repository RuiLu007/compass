import type { ExecutionEntry } from "./execution-timeline";

function readObject(source?: string | null): Record<string, unknown> | undefined {
  if (!source) return;
  try {
    const value = JSON.parse(source);
    if (value && typeof value === "object" && !Array.isArray(value)) return value;
  } catch {
    // Older execution previews can end in the middle of a JSON value.
  }
}

function snippet(value: unknown, limit: number, keepEnd = 0): string {
  if (typeof value !== "string") return "";
  const chars = Array.from(value.trim().replace(/\s+/g, " "));
  if (chars.length <= limit) return chars.join("");
  const end = keepEnd ? chars.slice(-keepEnd).join("") : "";
  return chars.slice(0, limit - 1 - keepEnd).join("") + "…" + end;
}

function operationTitle(input: Record<string, unknown>) {
  let action: string;
  switch (input.op) {
    case "query":
      action = `执行 ${snippet(input.sql, 80, 24) || "SQL 查询"}`;
      break;
    case "describe_table": {
      const table = [input.schema, input.table]
        .filter((part) => typeof part === "string")
        .join(".");
      action = `查看表结构 ${snippet(table, 64)}`.trimEnd();
      break;
    }
    case "list_tables":
      action = "列出数据表";
      break;
    case "list_connections":
      return "列出可用连接";
    default:
      return;
  }
  const offset = typeof input.offset === "number" && input.offset > 0 && `偏移 ${input.offset}`;
  const connection = snippet(input.connectionId, 12);
  return [action, offset, connection && `连接 ${connection}`].filter(Boolean).join(" · ");
}

function cellText(value: unknown) {
  if (value === null) return "NULL";
  if (typeof value === "string") return snippet(value, 22) || '""';
  if (typeof value === "number" && Number.isInteger(value) && !Number.isSafeInteger(value))
    return "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function resultText(output?: Record<string, unknown>, rowCount?: number) {
  if (Array.isArray(output?.connections)) {
    const names = output.connections
      .slice(0, 2)
      .map((item) => snippet(item?.name, 22))
      .filter(Boolean);
    return [`${output.connections.length} 个连接`, ...names].join(" · ");
  }
  const rows: unknown[] | undefined = Array.isArray(output?.rows) ? output.rows : undefined;
  const count = rowCount ?? rows?.length;
  if (count === undefined) return;
  const first = rows?.[0];
  const columns: unknown[] = Array.isArray(output?.columns) ? output.columns : [];
  const values = columns.slice(0, 2).flatMap((column, index) => {
    const name = snippet(column, 22);
    const value = Array.isArray(first) && cellText(first[index]);
    return name && value ? [`${name}=${value}`] : [];
  });
  return [`返回 ${count} 行${output?.truncated === true ? "（已截断）" : ""}`, ...values].join(
    " · ",
  );
}

export function withDatabaseQuerySummary(entry: ExecutionEntry, rowCount?: number): ExecutionEntry {
  const input = readObject(entry.input);
  return {
    ...entry,
    toolType: "database",
    title: (input && operationTitle(input)) ?? entry.title,
    queryResult:
      entry.status === "completed" || entry.status === "empty"
        ? resultText(readObject(entry.output), rowCount)
        : undefined,
  };
}
