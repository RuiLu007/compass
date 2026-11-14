import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, statSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { initialize, commands } from "./sandbox.mjs";

test("sandbox initialization preserves private keys and shutdown preserves NAS volumes", () => {
  const root = mkdtempSync(path.join(tmpdir(), "compass-sandbox-"));
  try {
    const file = initialize(root);
    const first = readFileSync(file, "utf8");
    assert.match(first, /COMPASS_SANDBOX_API_KEY=[a-f0-9]{64}\n/);
    initialize(root);
    assert.equal(readFileSync(file, "utf8"), first);
    assert.equal(statSync(file).mode & 0o777, 0o600);
    assert.deepEqual(commands(root, "down")[0].slice(-3), ["stop", "opensandbox", "nas"]);
    assert.equal(commands(root, "up").length, 3);
    assert.throws(() => commands(root, "reset"));
  } finally {
    rmSync(root, { recursive: true });
  }
});
