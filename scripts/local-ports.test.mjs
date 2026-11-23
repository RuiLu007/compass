import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { cleanupRoots, clearAppPorts, listenerPids, publishedContainers } from "./local-ports.mjs";
import { terminateProcesses } from "./process-tree.mjs";

test("cleanup includes checkout reloaders and Scheduler, excluding the caller and other checkouts", () => {
  const rows = [
    { pid: 1, parent: 0, command: "/sbin/launchd" },
    { pid: 2, parent: 1, command: "node host" },
    { pid: 3, parent: 2, command: "node restart" },
    { pid: 10, parent: 2, command: "python supervisor.py" },
    { pid: 11, parent: 10, command: "python -m uvicorn compass_agent.main:app" },
    { pid: 12, parent: 11, command: "python -c spawn_main" },
    { pid: 13, parent: 10, command: "python -m compass_agent.scheduler.main" },
    { pid: 20, parent: 2, command: "python -m compass_agent.scheduler.main" },
    { pid: 21, parent: 2, command: "node unrelated" },
    { pid: 40, parent: 2, command: "node /opt/editor/extension-host.js" },
    { pid: 41, parent: 40, command: "node scripts/start.mjs" },
  ];
  const cwd = (pid) => (pid === 20 ? "/other" : "/project");
  assert.deepEqual(cleanupRoots(rows, [12], "/project", cwd, 3), [10, 13]);
  assert.deepEqual(cleanupRoots(rows, [41], "/project", cwd, 3), [41, 13]);
  assert.throws(() => cleanupRoots(rows, [2], "/project", cwd, 3), /restart command/);
});

test("container cleanup selects host ports, retaining unrelated containers and volumes", () => {
  const data =
    'aaaaaaaaaaaa {"3000/tcp":[{"HostPort":"8100"}]}\n' +
    'bbbbbbbbbbbb {"8100/tcp":[{"HostPort":"9200"}]}\n' +
    'cccccccccccc {"443/tcp":null}\n';
  assert.deepEqual(publishedContainers(data, [8100, 8101]), ["aaaaaaaaaaaa"]);
  assert.throws(() => publishedContainers("--volumes {}", [8100]), /Invalid Docker/);
});

async function listener(t, { reload = false, stubborn = false } = {}) {
  const server = `const net = require('node:net');
    ${stubborn ? "process.on('SIGTERM', () => {});" : ""}
    const s = net.createServer(); s.listen(0, '127.0.0.1', () => process.send({port:s.address().port, pid:process.pid}));`;
  const script = reload
    ? `const {spawn} = require('node:child_process');
    function run() { const c = spawn(process.execPath, ['-e', ${JSON.stringify(server)}], {stdio:['ignore','ignore','inherit','ipc']});
    c.on('message', m => process.send(m)); c.on('exit', run); } run();`
    : server;
  const root = mkdtempSync(path.join(tmpdir(), "compass-listener-"));
  const file = path.join(root, "listener.cjs");
  writeFileSync(file, script);
  const child = spawn(process.execPath, [file], {
    cwd: root,
    stdio: ["ignore", "ignore", "inherit", "ipc"],
  });
  t.after(async () => {
    await terminateProcesses([child.pid], { graceMs: 50 });
    if (child.connected) child.disconnect();
    rmSync(root, { recursive: true, force: true });
  });
  const [ready] = await once(child, "message");
  return { child, root, ...ready };
}

test(
  "occupied port is released, its reloader stops, and unrelated listeners stay alive",
  { timeout: 15000 },
  async (t) => {
    const target = await listener(t, { reload: true });
    const other = await listener(t);
    assert.ok(listenerPids(target.port).includes(target.pid));
    const exited = once(target.child, "exit");
    await clearAppPorts(target.root, { ports: [target.port], log: () => {} });
    await exited;
    assert.deepEqual(listenerPids(target.port), []);
    assert.throws(() => process.kill(target.child.pid, 0), { code: "ESRCH" });
    assert.ok(listenerPids(other.port).includes(other.pid));
    await clearAppPorts(target.root, { ports: [target.port], log: () => {} });
  },
);

test(
  "a listener ignoring SIGTERM is killed after the grace period",
  { timeout: 15000 },
  async (t) => {
    const target = await listener(t, { stubborn: true });
    await clearAppPorts(target.root, { ports: [target.port], graceMs: 50, log: () => {} });
    assert.deepEqual(listenerPids(target.port), []);
  },
);
