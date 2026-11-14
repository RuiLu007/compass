import assert from "node:assert/strict";
import test from "node:test";
import { ensureDocker } from "./docker-ready.mjs";

test("already running Docker API skips Desktop launch", async () => {
  await ensureDocker({
    probe: async () => true,
    launch: async () => assert.fail("unexpected launch"),
  });
});
test("Desktop already running response still waits for API readiness", async () => {
  let probes = 0,
    launches = 0,
    waits = 0;
  await ensureDocker({
    probe: async () => ++probes === 4,
    launch: async () => {
      launches++;
    },
    wait: async () => {
      waits++;
    },
  });
  assert.equal(launches, 1);
  assert.equal(probes, 4);
  assert.equal(waits, 2);
});
test("unavailable engine times out without proceeding", async () => {
  let time = 0;
  await assert.rejects(
    ensureDocker({
      probe: async () => false,
      launch: async () => {},
      now: () => time,
      wait: async (ms) => {
        time += ms;
      },
      timeout: 2000,
    }),
    /Docker API did not become ready/,
  );
  assert.equal(time, 2000);
});
test("cancel interrupts the wait without another probe", async () => {
  const controller = new AbortController();
  let probes = 0;
  await assert.rejects(
    ensureDocker({
      signal: controller.signal,
      probe: async () => {
        probes++;
        return false;
      },
      launch: async () => {},
      wait: async () => controller.abort(),
    }),
    { name: "AbortError" },
  );
  assert.equal(probes, 2);
});
