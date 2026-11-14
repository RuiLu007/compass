import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { options } from "./start.mjs";
import { requestStop } from "./local-control.mjs";

export function containerIds(output) {
  const ids = output.trim().split(/\s+/).filter(Boolean);
  if (ids.some((id) => !/^[a-f0-9]{12,64}$/.test(id)))
    throw new Error("Invalid Docker container ID");
  return ids;
}
export async function main(
  args = [],
  root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."),
) {
  // pnpm restart forwards its arguments to both stop and start.
  options(args);
  const stopped = await requestStop(root);
  console.log(stopped ? "Local apps stopped." : "No managed local apps are running.");
  for (const project of ["compass", "compass-sandbox"]) {
    const result = spawnSync(
      "docker",
      ["ps", "-q", "--filter", `label=com.docker.compose.project=${project}`],
      { encoding: "utf8" },
    );
    if (result.error || result.status !== 0)
      throw new Error("Docker unavailable; dependency container stop could not be verified");
    const ids = containerIds(result.stdout);
    if (ids.length) {
      const result = spawnSync("docker", ["stop", "--timeout", "30", ...ids], { stdio: "inherit" });
      if (result.error || result.status !== 0)
        throw new Error(`Could not stop ${project} containers`);
    }
  }
  console.log("Compass stopped. All database and sandbox volumes are retained.");
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
