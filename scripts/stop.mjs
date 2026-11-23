import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { options } from "./start.mjs";
import { requestStop } from "./local-control.mjs";
import { clearAppPorts } from "./local-ports.mjs";

export function containerIds(output) {
  const ids = output.trim().split(/\s+/).filter(Boolean);
  if (ids.some((id) => !/^[a-f0-9]{12,64}$/.test(id)))
    throw new Error("Invalid Docker container ID");
  return ids;
}
export function stopDependencies({ restarting = false, execute = spawnSync } = {}) {
  for (const project of ["compass", "compass-sandbox"]) {
    const result = execute(
      "docker",
      ["ps", "-q", "--filter", `label=com.docker.compose.project=${project}`],
      { encoding: "utf8", timeout: 5000, killSignal: "SIGKILL" },
    );
    if (result.error || result.status !== 0) {
      if (restarting && result.error?.code !== "ENOENT") {
        console.log(
          "Docker API unavailable. Containers are retained; startup will recover Docker.",
        );
        return false;
      }
      throw new Error("Docker unavailable; dependency container stop could not be verified");
    }
    const ids = containerIds(result.stdout);
    if (ids.length) {
      const result = execute("docker", ["stop", "--timeout", "30", ...ids], {
        stdio: "inherit",
        timeout: 45000,
        killSignal: "SIGKILL",
      });
      if (result.error || result.status !== 0) {
        if (restarting) {
          console.log(
            `Could not stop ${project} containers; startup will recheck Docker readiness.`,
          );
          return false;
        }
        throw new Error(`Could not stop ${project} containers`);
      }
    }
  }
  return true;
}
export async function main(
  args = [],
  root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."),
  { restarting = process.env.npm_command === "restart" } = {},
) {
  // pnpm restart forwards its arguments to both stop and start.
  options(args);
  let stopped = false;
  try {
    stopped = await requestStop(root);
  } catch (error) {
    if (!restarting) throw error;
    console.warn(`${error.message}. Restart will clean up application ports.`);
  }
  console.log(stopped ? "Local apps stopped." : "No managed local apps are running.");
  if (restarting) await clearAppPorts(root);
  if (stopDependencies({ restarting }))
    console.log("Compass stopped. All database and sandbox volumes are retained.");
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
