import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { listenControl, requestStop, controlPath } from "./local-control.mjs";
import { containerIds } from "./stop.mjs";

test("stop acknowledges only after supervisor cleanup; repeated stop is harmless", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "compass-control-test-"));
  let close;
  let requested = false;
  try {
    assert.equal(await requestStop(root), false);
    close = await listenControl(root, () => {
      requested = true;
      setTimeout(close, 30);
    });
    await assert.rejects(
      listenControl(root, () => {}),
      /already managed/,
    );
    assert.equal(requested, false);
    assert.equal(await requestStop(root), true);
    assert.equal(requested, true);
    assert.equal(await requestStop(root), false);
  } finally {
    close?.();
    rmSync(path.dirname(controlPath(root)), { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
});
test("Docker stop only accepts IDs, including an empty already-stopped inventory", () => {
  assert.deepEqual(containerIds(""), []);
  assert.deepEqual(containerIds("abcdef123456\n012345abcdef\n"), ["abcdef123456", "012345abcdef"]);
  assert.throws(() => containerIds("--volumes"));
});

test("failed shutdown rejects the stop request", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "compass-control-error-"));
  let close;
  try {
    close = await listenControl(root, () => close(new Error("failed")));
    await assert.rejects(requestStop(root), /not confirmed/);
  } finally {
    close?.();
    rmSync(path.dirname(controlPath(root)), { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
});
