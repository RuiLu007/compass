import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import test from "node:test";
import { descendants, terminateProcesses } from "./process-tree.mjs";

test("process ownership includes grandchildren but excludes unrelated processes", () => {
  const table = [
    { pid: 1, parent: 0 },
    { pid: 2, parent: 1 },
    { pid: 3, parent: 2 },
    { pid: 4, parent: 0 },
  ];
  assert.deepEqual(
    descendants(table, [1]).map((row) => row.pid),
    [1, 2, 3],
  );
});
test("stop waits for descendants even when they use their own process group", async () => {
  const child = spawn(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      `
    import { spawn } from 'node:child_process';
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { detached: true, stdio: 'ignore' });
    console.log(child.pid);
    setInterval(() => {}, 1000);
  `,
    ],
    { stdio: ["ignore", "pipe", "inherit"] },
  );
  const [chunk] = await once(child.stdout, "data");
  const descendant = Number(chunk.toString().trim());
  try {
    await terminateProcesses([child.pid]);
    assert.throws(() => process.kill(descendant, 0), { code: "ESRCH" });
  } finally {
    for (const pid of [child.pid, descendant]) {
      try {
        process.kill(pid, "SIGKILL");
      } catch {}
    }
  }
});
