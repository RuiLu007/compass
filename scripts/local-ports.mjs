import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import path from "node:path";
import { processTable, terminateProcesses } from "./process-tree.mjs";

export const APP_PORTS = [8100, 8101, 8102, 8103];

function canonicalPath(value) {
  try {
    return realpathSync(value);
  } catch {
    return path.resolve(value);
  }
}

function lsof(args) {
  const result = spawnSync("lsof", ["-nP", ...args], { encoding: "utf8" });
  if (result.error)
    throw new Error("Port cleanup requires lsof on macOS / Linux", { cause: result.error });
  if (result.status === 1 && !result.stderr.trim()) return "";
  if (result.status !== 0) throw new Error(`Cannot inspect local ports: ${result.stderr.trim()}`);
  return result.stdout;
}

export function listenerPids(port) {
  return [
    ...new Set(
      lsof(["-a", `-iTCP:${port}`, "-sTCP:LISTEN", "-Fp"])
        .split("\n")
        .filter((line) => /^p\d+$/.test(line))
        .map((line) => Number(line.slice(1))),
    ),
  ];
}

function directory(pid) {
  return lsof(["-a", "-p", String(pid), "-d", "cwd", "-Fn"])
    .split("\n")
    .find((line) => line.startsWith("n"))
    ?.slice(1);
}

export function publishedContainers(output, ports) {
  return output
    .trim()
    .split("\n")
    .filter(Boolean)
    .flatMap((line) => {
      const split = line.indexOf(" ");
      const id = line.slice(0, split);
      if (!/^[a-f0-9]{12,64}$/.test(id)) throw new Error("Invalid Docker port inventory");
      const bindings = JSON.parse(line.slice(split + 1));
      return Object.values(bindings ?? {}).some((entries) =>
        (entries ?? []).some((entry) => ports.includes(Number(entry.HostPort))),
      )
        ? [id]
        : [];
    });
}

function stopPortContainers(ports) {
  const execute = (args) => {
    const result = spawnSync("docker", args, {
      encoding: "utf8",
      timeout: 20000,
      killSignal: "SIGKILL",
    });
    if (result.error || result.status !== 0)
      throw new Error(
        "Cannot release Docker-published application ports; Docker API is unavailable",
      );
    return result.stdout;
  };
  const ids = execute(["ps", "-q"]).trim().split(/\s+/).filter(Boolean);
  if (!ids.length || ids.some((id) => !/^[a-f0-9]{12,64}$/.test(id)))
    throw new Error("No container found for occupied Docker application ports");
  const owners = publishedContainers(
    execute(["inspect", "--format", "{{.Id}} {{json .NetworkSettings.Ports}}", ...ids]),
    ports,
  );
  if (!owners.length) throw new Error("No container found for occupied Docker application ports");
  execute(["stop", "--timeout", "10", ...owners]);
}

export function cleanupRoots(table, listeners, root, cwd, self = process.pid) {
  root = canonicalPath(root);
  const rows = new Map(table.map((row) => [row.pid, row]));
  // Never terminate the command issuing this restart, its shell or its host.
  const protectedPids = new Set([1]);
  for (let row = rows.get(self); row && !protectedPids.has(row.pid); row = rows.get(row.parent))
    protectedPids.add(row.pid);
  const inRepository = (pid) => {
    const value = cwd(pid);
    if (!value) return false;
    const relative = path.relative(root, canonicalPath(value));
    return (
      relative === "" ||
      (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`))
    );
  };
  const launcher = (row) => {
    const [binary, script = "", module = ""] = row.command.split(/\s+/);
    const name = path.basename(binary);
    if (/^(?:uv|uvicorn|pnpm|npm|npx|turbo)$/.test(name)) return true;
    if (!/^(?:node|python[\d.]*)$/.test(name)) return false;
    if (script === "-m") return /^(?:uvicorn|compass_agent\.scheduler\.main)$/.test(module);
    if (!script || script.startsWith("-")) return false;
    if (/^pnpm\.(?:cjs|mjs|js)$/.test(path.basename(script))) return true;
    const directory = cwd(row.pid);
    if (!directory) return false;
    const relative = path.relative(root, canonicalPath(path.resolve(directory, script)));
    return !path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`);
  };
  const roots = new Set();
  for (const pid of listeners) {
    if (protectedPids.has(pid))
      throw new Error(`Port is owned by the restart command's process tree (${pid})`);
    let row = rows.get(pid);
    if (!row) continue;
    for (
      let parent = rows.get(row.parent);
      parent && !protectedPids.has(parent.pid) && launcher(parent) && inRepository(parent.pid);
      parent = rows.get(row.parent)
    )
      row = parent;
    roots.add(row.pid);
  }
  // A manually started Scheduler has no HTTP port; include only this checkout's instance.
  for (const row of table) {
    if (
      !protectedPids.has(row.pid) &&
      launcher(row) &&
      /(?:^|\s)-m\s+compass_agent\.scheduler\.main(?:\s|$)/.test(row.command) &&
      inRepository(row.pid)
    )
      roots.add(row.pid);
  }
  return [...roots];
}

export async function clearAppPorts(
  root,
  { ports = APP_PORTS, graceMs = 5000, log = console.log } = {},
) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const occupied = ports
      .map((port) => ({ port, pids: listenerPids(port) }))
      .filter((item) => item.pids.length);
    const table = processTable();
    const relays = new Set(
      table
        .filter((row) =>
          /^(?:\S*\/)?(?:com\.docker\.backend|docker-proxy|vpnkit|rootlesskit)(?:\s|$)/.test(
            row.command,
          ),
        )
        .map((row) => row.pid),
    );
    const published = occupied.filter((item) => item.pids.some((pid) => relays.has(pid)));
    if (published.length) {
      log(`Stopping containers publishing ports ${published.map((item) => item.port).join(", ")}`);
      stopPortContainers(published.map((item) => item.port));
      continue;
    }
    const cwdCache = new Map();
    const roots = cleanupRoots(
      table,
      occupied.flatMap((item) => item.pids),
      root,
      (pid) => {
        if (!cwdCache.has(pid)) cwdCache.set(pid, directory(pid));
        return cwdCache.get(pid);
      },
    );
    if (!roots.length && !occupied.length) return;
    for (const { port, pids } of occupied) log(`Freeing port ${port}: PID ${pids.join(", ")}`);
    if (roots.length) await terminateProcesses(roots, { graceMs });
  }
  const occupied = ports.filter((port) => listenerPids(port).length);
  if (occupied.length)
    throw new Error(`Ports still occupied after cleanup: ${occupied.join(", ")}`);
}
