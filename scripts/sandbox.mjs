import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function initialize(root) {
  const directory = path.join(root, "work/sandbox");
  const target = path.join(directory, "local.env");
  if (existsSync(target)) return target;
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  writeFileSync(
    target,
    `COMPASS_SANDBOX_ENABLED=true\nCOMPASS_SANDBOX_DOMAIN=127.0.0.1:8112\nCOMPASS_SANDBOX_API_KEY=${randomBytes(32).toString("hex")}\n`,
    { mode: 0o600, flag: "wx" },
  );
  return target;
}

export function commands(root, action) {
  const compose = [
    "compose",
    "--env-file",
    path.join(root, "work/sandbox/local.env"),
    "-f",
    "deploy/compose/sandbox.yaml",
  ];
  if (action === "up")
    return [
      ["docker", ...compose, "build", "nas", "python-image"],
      ["docker", ...compose, "up", "-d", "--wait", "--wait-timeout", "120", "nas", "opensandbox"],
      ["uv", "run", "--locked", "python", "deploy/sandbox/volume_admin.py"],
    ];
  if (action === "status") return [["docker", ...compose, "ps"]];
  if (action === "logs") return [["docker", ...compose, "logs", "--tail", "60"]];
  if (action === "down") return [["docker", ...compose, "stop", "opensandbox", "nas"]];
  throw new Error("Usage: pnpm sandbox <init|up|status|logs|down>");
}

export function main(
  args,
  root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."),
) {
  if (args.length !== 1) throw new Error("Usage: pnpm sandbox <init|up|status|logs|down>");
  const [action] = args;
  if (action === "init" || action === "up") {
    initialize(root);
    console.log("Private Agent/control-plane configuration: work/sandbox/local.env");
    if (action === "init") return;
  }
  for (const [command, ...parameters] of commands(root, action)) {
    const result = spawnSync(command, parameters, { cwd: root, stdio: "inherit" });
    if (result.status !== 0) throw new Error(`${command} failed; existing volumes are preserved`);
  }
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
