import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { setTimeout as delay } from "node:timers/promises";

const execute = promisify(execFile);
export async function dockerAvailable(env, signal, timeout = 3000) {
  signal?.throwIfAborted();
  try {
    await execute("docker", ["info", "--format", "{{.ServerVersion}}"], {
      env,
      signal,
      timeout,
      killSignal: "SIGKILL",
    });
    return true;
  } catch (error) {
    signal?.throwIfAborted();
    if (error.code === "ENOENT")
      throw new Error("Docker CLI is missing; install Docker Desktop first");
    return false;
  }
}
export async function ensureDocker({
  launch,
  recover,
  probe,
  signal,
  timeout = 120000,
  now = Date.now,
  wait = (ms) => delay(ms, undefined, { signal }),
}) {
  signal?.throwIfAborted();
  if (await probe(3000)) return;
  for (let attempt = 0; attempt < (recover ? 2 : 1); attempt++) {
    try {
      await launch();
      const deadline = now() + timeout;
      while (now() < deadline) {
        signal?.throwIfAborted();
        if (await probe(Math.max(1, Math.min(3000, deadline - now())))) return;
        const remaining = deadline - now();
        if (remaining > 0) await wait(Math.min(1000, remaining));
      }
    } catch (error) {
      signal?.throwIfAborted();
      if (attempt || !recover) throw error;
    }
    if (attempt === 0 && recover) {
      signal?.throwIfAborted();
      await recover();
    }
  }
  throw new Error(
    `Docker API did not become ready after ${recover ? "two attempts" : `${timeout / 1000} seconds`}. Open Docker Desktop and check its engine status.`,
  );
}
