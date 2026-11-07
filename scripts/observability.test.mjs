import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, statSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { initialize, commandArgs, services } from "./observability.mjs";

test("observability creates private separate configurations without overwriting", () => {
  const root = mkdtempSync(path.join(tmpdir(), "compass-observe-"));
  try {
    const dir = initialize(root);
    const stack = readFileSync(path.join(dir, "stack.env"), "utf8");
    const agent = readFileSync(path.join(dir, "agent.env"), "utf8");
    const web = readFileSync(path.join(dir, "web.env"), "utf8");
    assert.match(stack, /LF_ADMIN_PASSWORD=[a-f0-9]{64}/);
    assert.match(agent, /COMPASS_LANGFUSE_SECRET_KEY=sk-lf-/);
    assert.match(agent, /COMPASS_OBSERVABILITY_CAPTURE_CONTENT=true/);
    assert.match(agent, /COMPASS_OBSERVABILITY_CONTENT_MAX_BYTES=16384/);
    assert.match(agent, /COMPASS_LANGFUSE_BASE_URL=http:\/\/langfuse-web:3000\n/);
    assert.doesNotMatch(agent, /LF_POSTGRES_PASSWORD|LF_ADMIN_PASSWORD/);
    assert.doesNotMatch(web, /SECRET|PASSWORD/);
    for (const name of ["stack.env", "agent.env", "web.env"])
      assert.equal(statSync(path.join(dir, name)).mode & 0o777, 0o600);
    assert.throws(() => initialize(root), /already exists/);
    assert.equal(readFileSync(path.join(dir, "stack.env"), "utf8"), stack);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
test("observability commands only address six services and preserve volumes", () => {
  assert.equal(services.length, 6);
  for (const action of ["up", "status", "logs", "down"]) {
    const args = commandArgs("/project", action);
    assert.deepEqual(args.slice(-6), services);
    assert.ok(!args.includes("-v"));
  }
  assert.throws(() => commandArgs("/project", "reset"));
  assert.ok(commandArgs("/project", "down").includes("stop"));
});
