import { randomBytes, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { deploymentEnv } from "./deploy.mjs";

export const services = [
  "langfuse-web",
  "langfuse-worker",
  "langfuse-postgres",
  "langfuse-clickhouse",
  "langfuse-redis",
  "langfuse-minio",
];
export function initialize(root) {
  const directory = path.join(root, "work/observability");
  const targets = ["stack.env", "agent.env", "web.env"];
  if (targets.some((name) => existsSync(path.join(directory, name))))
    throw new Error("Private observability configuration already exists; preserving it.");
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const secret = () => randomBytes(32).toString("hex");
  const values = Object.fromEntries(
    [
      "LF_POSTGRES_PASSWORD",
      "LF_CLICKHOUSE_PASSWORD",
      "LF_REDIS_PASSWORD",
      "LF_MINIO_PASSWORD",
      "LF_NEXTAUTH_SECRET",
      "LF_SALT",
      "LF_ENCRYPTION_KEY",
      "LF_ADMIN_PASSWORD",
    ].map((key) => [key, secret()]),
  );
  Object.assign(values, {
    LF_MINIO_USER: `lf${randomBytes(10).toString("hex")}`,
    LF_ORG_ID: randomUUID(),
    LF_PROJECT_ID: randomUUID(),
    COMPASS_LANGFUSE_PUBLIC_KEY: `pk-lf-${randomUUID()}`,
    COMPASS_LANGFUSE_SECRET_KEY: `sk-lf-${secret()}`,
  });
  const serialize = (data) =>
    Object.entries(data)
      .map(([key, value]) => `${key}=${value}`)
      .join("\n") + "\n";
  writeFileSync(path.join(directory, "stack.env"), serialize(values), { mode: 0o600, flag: "wx" });
  writeFileSync(
    path.join(directory, "agent.env"),
    serialize({
      COMPASS_OBSERVABILITY_ENABLED: "true",
      COMPASS_OBSERVABILITY_ENVIRONMENT: "local",
      COMPASS_OBSERVABILITY_CAPTURE_CONTENT: "true",
      COMPASS_OBSERVABILITY_CONTENT_MAX_BYTES: "16384",
      COMPASS_LANGFUSE_BASE_URL: "http://langfuse-web:3000",
      COMPASS_LANGFUSE_PUBLIC_KEY: values.COMPASS_LANGFUSE_PUBLIC_KEY,
      COMPASS_LANGFUSE_SECRET_KEY: values.COMPASS_LANGFUSE_SECRET_KEY,
    }),
    { mode: 0o600, flag: "wx" },
  );
  writeFileSync(
    path.join(directory, "web.env"),
    serialize({
      COMPASS_LANGFUSE_PROJECT_URL: `http://127.0.0.1:8104/project/${values.LF_PROJECT_ID}`,
    }),
    { mode: 0o600, flag: "wx" },
  );
  return directory;
}
export function commandArgs(root, action) {
  const base = [
    "compose",
    "-p",
    "compass",
    "--env-file",
    path.join(root, "work/observability/stack.env"),
    "-f",
    "deploy/compose/compose.yaml",
    "-f",
    "deploy/compose/observability.yaml",
    "--profile",
    "observability",
  ];
  const actions = {
    up: ["up", "-d", "--wait", "--wait-timeout", "240", ...services],
    status: ["ps", ...services],
    logs: ["logs", "--tail", "100", ...services],
    down: ["stop", ...services],
  };
  if (!Object.hasOwn(actions, action))
    throw new Error("Usage: pnpm observability <init|up|status|logs|down>");
  return [...base, ...actions[action]];
}
export function main(
  args,
  root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."),
) {
  if (args.length !== 1) throw new Error("Expected one observability action");
  if (args[0] === "init") {
    console.log(`Private configuration created: ${initialize(root)}`);
    return;
  }
  const command = commandArgs(root, args[0]);
  if (!existsSync(path.join(root, "work/observability/stack.env")))
    throw new Error("Run pnpm observability init first");
  const result = spawnSync("docker", command, {
    cwd: root,
    env: deploymentEnv(process.env),
    stdio: "inherit",
    timeout: 600000,
  });
  if (result.error || result.status !== 0)
    throw new Error(
      "Observability command failed; existing volumes and configuration are preserved",
    );
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
