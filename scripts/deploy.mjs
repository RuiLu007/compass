import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import { devNull } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const apps = ["home", "web", "agent", "memory"];
const actions = ["up", "status", "logs", "down"];

export function parseArgs(args) {
  if (args.includes("--help") || args.includes("-h")) return { help: true };
  const [action = "up", app, ...extra] = args;
  if (!actions.includes(action) || extra.length || (app && !apps.includes(app)))
    throw new Error("Usage: pnpm run deploy [home|web|agent|memory]");
  if (action === "down" && app)
    throw new Error("deploy:down stops the whole project; it does not accept an app.");
  return { action, selected: app ? [app] : [...apps] };
}

export function deploymentEnv(source) {
  const env = { ...source };
  for (const key of Object.keys(env))
    if (
      key.startsWith("COMPOSE_") ||
      [
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "BUILDX_BUILDER",
      ].includes(key)
    )
      delete env[key];
  env.COMPOSE_DISABLE_ENV_FILE = "1";
  env.COMPOSE_BAKE = "false";
  return env;
}

export function parseContainers(output) {
  const value = output.trim();
  if (!value) return [];
  return value.startsWith("[")
    ? JSON.parse(value)
    : value.split("\n").map((line) => JSON.parse(line));
}

export function deploymentPlan(selected) {
  const migrations = [
    ...(selected.includes("agent") ? ["migrate"] : []),
    ...(selected.includes("web") ? ["web-migrate"] : []),
  ];
  const running = [
    ...selected,
    ...(selected.includes("agent") ? ["agent-gateway", "scheduler"] : []),
  ];
  return {
    migrations,
    running,
    services: [...running, ...migrations],
    database: migrations.length > 0,
    infrastructure: [
      ...(migrations.length ? ["postgres"] : []),
      ...(selected.includes("agent") ? ["redis"] : []),
    ],
  };
}

async function assertPortFree(port) {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", () =>
      reject(new Error(`Port ${port} is occupied. Stop its development service before deploying.`)),
    );
    server.listen(port, "127.0.0.1", () => server.close(resolve));
  });
}

function execute(command, args, { capture = false, env = process.env, timeout = 1200000 } = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    env,
    encoding: "utf8",
    stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
    timeout,
  });
  if (result.error || result.status !== 0)
    throw new Error(
      `${command} ${args.join(" ")} failed${result.error ? `: ${result.error.message}` : ` (exit ${result.status})`}${capture && result.stderr ? `\n${result.stderr.trim()}` : ""}`,
    );
  return result.stdout ?? "";
}

// Injected boundaries allow order/failure/port tests without touching Docker or user services.
export async function deploy(
  args,
  { run = execute, sourceEnv = process.env, checkPort = assertPortFree, workspace = root } = {},
) {
  const options = parseArgs(args);
  if (options.help) {
    console.log(
      "pnpm run deploy [home|web|agent|memory]\npnpm deploy:status [app]\npnpm deploy:logs [app]\npnpm deploy:down\nNo app selects all four apps. down preserves volumes and images.",
    );
    return;
  }
  const { action, selected } = options;
  const plan = deploymentPlan(selected);
  const env = deploymentEnv(sourceEnv);
  const capture = { capture: true, env, timeout: 20000 };
  let endpoint = !sourceEnv.DOCKER_CONTEXT && sourceEnv.DOCKER_HOST;
  if (!endpoint) {
    const context = sourceEnv.DOCKER_CONTEXT || run("docker", ["context", "show"], capture).trim();
    endpoint = JSON.parse(
      run(
        "docker",
        ["context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"],
        capture,
      ),
    );
  }
  if (typeof endpoint !== "string" || !endpoint.startsWith("unix:///"))
    throw new Error(
      "Local deployment requires a Unix-socket Docker endpoint. Remote TCP/SSH contexts are rejected.",
    );
  const docker = (params, settings = {}) =>
    run("docker", ["--host", endpoint, ...params], { env, ...settings });
  const prefix = [
    "compose",
    "--project-name",
    "compass",
    "--env-file",
    devNull,
    "-f",
    path.join(workspace, "deploy/compose/compose.yaml"),
    "--profile",
    "app",
    "--profile",
    "home",
    "--profile",
    "memory",
  ];
  const compose = (params, settings = {}) => docker([...prefix, ...params], settings);
  docker(["info", "--format", "{{.OSType}}"], capture);
  compose(["version", "--short"], capture);
  if (action === "status") {
    compose(["ps", "--all", ...plan.services, ...plan.infrastructure]);
    return;
  }
  if (action === "logs") {
    compose(["logs", "--tail", "100", ...plan.services, ...plan.infrastructure]);
    return;
  }
  mkdirSync(path.join(workspace, "work"), { recursive: true });
  const lock = path.join(workspace, "work/local-deploy.lock");
  try {
    mkdirSync(lock);
  } catch (error) {
    if (error.code === "EEXIST")
      throw new Error(
        `Deployment lock exists: ${lock}. Check its owner.json and active process before removing a stale lock.`,
      );
    throw error;
  }
  try {
    writeFileSync(
      path.join(lock, "owner.json"),
      JSON.stringify({ pid: process.pid, action, selected }),
    );
    if (action === "down") {
      compose(["down", "--timeout", "15"]);
      console.log("Compose project stopped; volumes and images retained.");
      return;
    }
    const config = JSON.parse(compose(["config", "--format", "json"], capture));
    const containers = parseContainers(compose(["ps", "--all", "--format", "json"], capture));
    if (
      plan.database &&
      containers.some(
        (item) =>
          item.Service === "postgres" && item.State === "running" && item.Health === "unhealthy",
      )
    )
      throw new Error(
        "Existing PostgreSQL container is unhealthy. Inspect Docker/storage health before retrying; existing data is preserved.",
      );
    if (selected.includes("agent") && !env.COMPASS_RUNTIME_STATE_KEY)
      throw new Error(
        "Agent deployment requires COMPASS_RUNTIME_STATE_KEY; preserve it with backups.",
      );
    for (const service of [...plan.running, ...plan.infrastructure]) {
      if (!config.services[service]) throw new Error(`Missing Compose service: ${service}`);
      for (const port of config.services[service].ports ?? []) {
        if (port.host_ip !== "127.0.0.1")
          throw new Error(`Service ${service} must bind only to 127.0.0.1.`);
        const owned = containers.some(
          (item) =>
            (item.Service === service ||
              (service === "agent-gateway" && item.Service === "agent")) &&
            item.State === "running" &&
            item.Publishers?.some(
              (entry) =>
                entry.PublishedPort === Number(port.published) && entry.URL === "127.0.0.1",
            ),
        );
        if (!owned) await checkPort(Number(port.published));
      }
    }
    if (selected.includes("web")) run("pnpm", ["contracts:check"], { env });
    if (selected.includes("home"))
      run(process.execPath, ["apps/home/scripts/prepare-assets.mjs"], { env });
    console.log(`Deploying locally: ${selected.join(", ")}`);
    compose(["build", ...plan.services.filter((service) => config.services[service]?.build)]);
    if (plan.infrastructure.length)
      compose(["up", "--detach", "--wait", "--wait-timeout", "120", ...plan.infrastructure]);
    const agent = selected.includes("agent");
    if (agent) compose(["stop", "--timeout", "12", "scheduler"]);
    const serving = plan.running.filter((service) => service !== "scheduler");
    compose(["stop", "--timeout", "15", ...serving]);
    for (const migration of plan.migrations) compose(["run", "--rm", "--no-deps", "-T", migration]);
    const up = (services) =>
      compose([
        "up",
        "--detach",
        "--no-deps",
        "--no-build",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "120",
        ...services,
      ]);
    up(serving);
    run(process.execPath, ["scripts/smoke.mjs", "--local", "--apps", selected.join(",")], { env });
    if (agent) {
      up(["scheduler"]);
      const roles = parseContainers(
        compose(["ps", "--all", "--format", "json", "agent", "scheduler"], capture),
      );
      const agents = roles.filter((role) => role.Service === "agent");
      const schedulers = roles.filter((role) => role.Service === "scheduler");
      const expected = Number(config.services.agent.scale ?? 2);
      if (
        agents.length !== expected ||
        schedulers.length !== 1 ||
        roles.some((role) => role.State !== "running" || role.Health !== "healthy")
      )
        throw new Error("Agent replicas and the Scheduler must be healthy.");
    }
    console.log(`Local deployment passed: ${selected.join(", ")}.`);
  } finally {
    rmSync(lock, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    await deploy(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    console.error(
      "Deployment stopped. Inspect pnpm deploy:status / pnpm deploy:logs. No volumes were deleted.",
    );
    process.exitCode = 1;
  }
}
