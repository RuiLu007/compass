import path from "node:path";
import { fileURLToPath } from "node:url";
import { options, main as startApps } from "./start.mjs";
import { main as stopApps } from "./stop.mjs";

// pnpm's built-in restart runs stop → restart → start and forwards these arguments.
// Keep this phase finite so the following start phase owns the application lifecycle.
export async function main(
  args,
  { lifecycle = process.env.npm_command === "restart", stop = stopApps, start = startApps } = {},
) {
  options(args);
  if (!lifecycle) await stop(args, undefined, { restarting: true });
  console.log("Restarting Compass…");
  if (!lifecycle) await start(args);
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
