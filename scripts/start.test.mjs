import assert from "node:assert/strict";
import test from "node:test";
import { agentCommand, options } from "./start.mjs";

test("start defaults to no Langfuse and accepts only the explicit opt-in", () => {
  assert.deepEqual(options([]), { langfuse: false });
  assert.deepEqual(options(["--langfuse"]), { langfuse: true });
  assert.throws(() => options(["--unknown"]), /Usage/);
});
test("Agent defaults to two replicas and uses the configured replica count", () => {
  const args = agentCommand({});
  assert.equal(args[args.indexOf("--workers") + 1], "2");
  assert.equal(agentCommand({ COMPASS_AGENT_REPLICAS: "1" })[args.indexOf("--workers") + 1], "1");
  assert.throws(() => agentCommand({ COMPASS_AGENT_REPLICAS: "0" }), /positive integer/);
});
