import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { parseEnv } from "node:util";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

import { terminateProcesses } from "./process-tree.mjs";
import { dockerAvailable, ensureDocker } from "./docker-ready.mjs";
import { listenControl } from "./local-control.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export function options(args) {
  if (args.some((arg) => arg !== "--langfuse") || args.length > 1)
    throw new Error("Usage: pnpm start [--langfuse]");
  return { langfuse: args.includes("--langfuse") };
}
export function workerEnvironments(env) {
  const credentials = JSON.parse(env.COMPASS_WORKER_CREDENTIALS || "{}");
  return ["worker-1", "worker-2"].map((id) => {
    if (typeof credentials[id] !== "string" || !credentials[id])
      throw new Error(`Configure COMPASS_WORKER_CREDENTIALS for ${id} in .env`);
    return { ...env, COMPASS_WORKER_ID: id, COMPASS_WORKER_TOKEN: credentials[id] };
  });
}
export async function main(args) {
  const { langfuse } = options(args);
  if (!existsSync(path.join(root, ".env"))) throw new Error("Create .env first; see README.md");
  const readEnv = (file) => parseEnv(readFileSync(path.join(root, file), "utf8"));
  const env = { ...readEnv(".env"), ...process.env };
  workerEnvironments(env);
  // Never terminate an unrelated listener (the Web dev command otherwise replaces it).
  for (const port of [8100, 8101, 8102, 8103]) {
    await new Promise((resolve, reject) => {
      const server = net.createServer();
      server.once("error", () =>
        reject(new Error(`Port ${port} is occupied; stop the existing app before pnpm start`)),
      );
      server.listen(port, "127.0.0.1", () => server.close(resolve));
    });
  }
  const children = new Set();
  const abort = new AbortController();
  let stopping = false;
  let closeControl;
  function stop() {
    if (stopping) return;
    stopping = true;
    abort.abort();
    void terminateProcesses([...children].map((child) => child.pid).filter(Boolean))
      .then(() => closeControl?.())
      .catch((error) => {
        console.error(error.message);
        process.exitCode = 1;
        closeControl?.(error);
      });
  }
  closeControl = await listenControl(root, stop);
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  function run(command, commandArgs, childEnv = env, persistent = false) {
    if (stopping) throw new Error("Startup cancelled");
    const child = spawn(command, commandArgs, {
      cwd: root,
      env: childEnv,
      stdio: "inherit",
      detached: true,
    });
    children.add(child);
    return new Promise((resolve, reject) => {
      child.once("error", (error) => {
        children.delete(child);
        reject(error);
      });
      child.once("exit", (code, signal) => {
        children.delete(child);
        if (stopping || (!persistent && code === 0)) resolve();
        else reject(new Error(`${command} exited (${signal ?? code})`));
      });
    });
  }
  const background = (filters, childEnv = env) => {
    void run("pnpm", ["dev", ...filters.flatMap((name) => ["-F", name])], childEnv, true).catch(
      (error) => {
        console.error(error.message);
        process.exitCode = 1;
        stop();
      },
    );
  };
  try {
    await ensureDocker({
      signal: abort.signal,
      probe: (timeout) => dockerAvailable(env, abort.signal, timeout),
      launch: async () => {
        console.log("Starting Docker Desktop; waiting for Docker API (up to 120 seconds)…");
        if (process.platform === "darwin") await run("open", ["-a", "Docker"]);
        await run("docker", ["desktop", "start", "--detach"]);
      },
    });
    await run("docker", [
      "compose",
      "--env-file",
      ".env",
      "-f",
      "deploy/compose/compose.yaml",
      "up",
      "-d",
      "--wait",
      "postgres",
      "redis",
    ]);
    await run("pnpm", ["sandbox", "up"]);
    Object.assign(env, readEnv("work/sandbox/local.env"));
    if (langfuse) {
      if (!existsSync(path.join(root, "work/observability/stack.env")))
        await run("pnpm", ["observability", "init"]);
      await run("pnpm", ["observability", "up"]);
      Object.assign(
        env,
        readEnv("work/observability/agent.env"),
        readEnv("work/observability/web.env"),
      );
      env.COMPASS_LANGFUSE_BASE_URL = "http://127.0.0.1:8104";
    }
    env.COMPASS_OBSERVABILITY_ENABLED = String(langfuse);
    await run("pnpm", ["db:migrate", "-F", "web", "-F", "agent"]);
    background(["web", "agent", "home", "memory"]);
    for (const port of [8100, 8101, 8102, 8103]) {
      const deadline = Date.now() + 120000;
      while (true) {
        if (stopping) return;
        try {
          const response = await fetch(
            `http://127.0.0.1:${port}/${port === 8101 ? "" : "readyz"}`,
            { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(2000)]) },
          );
          if (response.ok) break;
        } catch {
          /* wait for readiness */
        }
        if (Date.now() > deadline) throw new Error(`Readiness timeout on port ${port}`);
        await delay(500, undefined, { signal: abort.signal });
      }
    }
    for (const workerEnv of workerEnvironments(env)) background(["worker"], workerEnv);
    console.log(
      `\nCompass ready: Web http://127.0.0.1:8100 | Home http://127.0.0.1:8101 | Agent http://127.0.0.1:8102/docs\nLangfuse ${langfuse ? "http://127.0.0.1:8104" : "disabled"}. Ctrl+C stops apps; dependency containers and data are retained.`,
    );
  } catch (error) {
    if (!stopping) {
      console.error(error.message);
      process.exitCode = 1;
    }
    stop();
  }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
