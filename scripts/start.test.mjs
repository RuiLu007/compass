import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { agentCommand, homeAssetsReady, options } from "./start.mjs";

test("start defaults to no Langfuse and accepts only the explicit opt-in", () => {
  assert.deepEqual(options([]), { langfuse: false });
  assert.deepEqual(options(["--langfuse"]), { langfuse: true });
  assert.throws(() => options(["--unknown"]), /Usage/);
});

test("startup reuses complete Home resources and regenerates when a resource is missing", (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "compass-home-resources-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const app = path.join(root, "apps/home");
  mkdirSync(path.join(app, "src/lib"), { recursive: true });
  writeFileSync(
    path.join(app, "src/lib/lesson-resources.json"),
    JSON.stringify([{ artifacts: [{ href: "/resources/lessons/sample.html" }] }]),
  );
  assert.equal(homeAssetsReady(root), false);
  for (const file of [
    "prototype/index.html",
    "resources/system-architecture.png",
    "resources/business-schema.json",
    "resources/system-schema.json",
    "resources/system-data-model.md",
    "resources/lessons/sample.html",
  ]) {
    const target = path.join(app, "public", file);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, "fixture");
  }
  assert.equal(homeAssetsReady(root), true);
  rmSync(path.join(app, "public/resources/lessons/sample.html"));
  assert.equal(homeAssetsReady(root), false);
});
test("Agent defaults to two replicas and uses the configured replica count", () => {
  const args = agentCommand({});
  assert.equal(args[args.indexOf("--workers") + 1], "2");
  assert.equal(agentCommand({ COMPASS_AGENT_REPLICAS: "1" })[args.indexOf("--workers") + 1], "1");
  assert.throws(() => agentCommand({ COMPASS_AGENT_REPLICAS: "0" }), /positive integer/);
});
