import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import { main } from "./restart.mjs";

test("pnpm's built-in restart phase leaves stop/start to the lifecycle", async () => {
  await main(["--langfuse"], {
    lifecycle: true,
    stop: () => assert.fail("duplicate stop"),
    start: () => assert.fail("duplicate start"),
  });
});

test("pnpm run restart and direct script execution perform stop then start", async () => {
  const calls = [];
  await main(["--langfuse"], {
    lifecycle: false,
    stop: async (args, _, options) => calls.push(["stop", args, options]),
    start: async (args) => calls.push(["start", args]),
  });
  assert.deepEqual(calls, [
    ["stop", ["--langfuse"], { restarting: true }],
    ["start", ["--langfuse"]],
  ]);
});

test("invalid restart options fail before any process is stopped", async () => {
  await assert.rejects(
    main(["--unknown"], {
      lifecycle: false,
      stop: () => assert.fail("unexpected stop"),
    }),
    /Usage/,
  );
});

test("installed pnpm preserves restart context and forwards flags to all three phases", async (t) => {
  const dir = mkdtempSync(path.join(tmpdir(), "compass-restart-lifecycle-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(
    path.join(dir, "package.json"),
    JSON.stringify({
      private: true,
      scripts: Object.fromEntries(
        ["stop", "restart", "start"].map((phase) => [phase, "node lifecycle.cjs"]),
      ),
    }),
  );
  writeFileSync(
    path.join(dir, "lifecycle.cjs"),
    `require('node:fs').appendFileSync('calls.jsonl', JSON.stringify({event:process.env.npm_lifecycle_event, command:process.env.npm_command, args:process.argv.slice(2)})+'\\n');`,
  );
  await promisify(execFile)("pnpm", ["restart", "--", "--langfuse"], { cwd: dir, timeout: 15000 });
  assert.deepEqual(
    readFileSync(path.join(dir, "calls.jsonl"), "utf8").trim().split("\n").map(JSON.parse),
    ["stop", "restart", "start"].map((event) => ({
      event,
      command: "restart",
      args: ["--langfuse"],
    })),
  );
});
