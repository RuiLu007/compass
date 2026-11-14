import assert from "node:assert/strict";
import test from "node:test";
import { options, workerEnvironments } from "./start.mjs";

test("start defaults to no Langfuse and accepts only the explicit opt-in", () => {
  assert.deepEqual(options([]), { langfuse: false });
  assert.deepEqual(options(["--langfuse"]), { langfuse: true });
  assert.throws(() => options(["--unknown"]), /Usage/);
});
test("each worker receives its own registered token without mutating the parent", () => {
  const env = {
    COMPASS_WORKER_CREDENTIALS: JSON.stringify({ "worker-1": "one", "worker-2": "two" }),
    COMPASS_WORKER_TOKEN: "old",
  };
  const workers = workerEnvironments(env);
  assert.deepEqual(
    workers.map((e) => [e.COMPASS_WORKER_ID, e.COMPASS_WORKER_TOKEN]),
    [
      ["worker-1", "one"],
      ["worker-2", "two"],
    ],
  );
  assert.equal(env.COMPASS_WORKER_TOKEN, "old");
  assert.throws(() => workerEnvironments({}), /worker-1/);
});
