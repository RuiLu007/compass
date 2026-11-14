import { spawnSync } from "node:child_process";

export function processTable() {
  const result = spawnSync("ps", ["-axo", "pid=,ppid=,stat=,command="], { encoding: "utf8" });
  if (result.status !== 0) throw new Error("Cannot inspect local application processes");
  return result.stdout.split("\n").flatMap((line) => {
    const match = line.trim().match(/^(\d+)\s+(\d+)\s+(\S+)\s+(.*)$/);
    return match
      ? [{ pid: Number(match[1]), parent: Number(match[2]), status: match[3], command: match[4] }]
      : [];
  });
}
export function descendants(table, roots) {
  const ids = new Set(roots);
  let size;
  do {
    size = ids.size;
    for (const row of table) if (ids.has(row.parent)) ids.add(row.pid);
  } while (ids.size !== size);
  return table.filter((row) => ids.has(row.pid));
}
export async function terminateProcesses(roots) {
  const owned = descendants(processTable(), roots);
  const live = () =>
    processTable().filter(
      (row) =>
        !row.status.startsWith("Z") &&
        owned.some((old) => old.pid === row.pid && old.command === row.command),
    );
  const signal = (rows, name) => {
    for (const row of rows.reverse()) {
      try {
        process.kill(row.pid, name);
      } catch (error) {
        if (error.code !== "ESRCH") throw error;
      }
    }
  };
  signal(live(), "SIGTERM");
  const deadline = Date.now() + 15000;
  while (live().length) {
    if (Date.now() > deadline) signal(live(), "SIGKILL");
    if (Date.now() > deadline + 5000) throw new Error("Application processes did not exit");
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}
