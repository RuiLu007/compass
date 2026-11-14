import { createInterface } from "node:readline";
import { getQuickJS } from "quickjs-emscripten";

const maxFrame = 64 * 1024;
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const pending = new Map();
let runtime;
let context;
let root;
let started = false;
let finished = false;
let nextId = 0;
let deadline;

function send(message) {
  const line = JSON.stringify(message);
  if (Buffer.byteLength(line) >= maxFrame) throw new Error("frame_limit");
  process.stdout.write(line + "\n");
}

function finish(message) {
  if (finished) return;
  send(message);
  finished = true;
  setImmediate(() => {
    input.close();
    process.stdin.pause();
  });
}

function fail(code = "script_failed") {
  finish({ type: "error", code });
}

function pump() {
  if (finished) return;
  try {
    const jobs = runtime.executePendingJobs(32);
    if (jobs.error) {
      jobs.error.dispose();
      fail();
      return;
    }
    const state = context.getPromiseState(root);
    if (state.type === "rejected") {
      state.error.dispose();
      fail();
    } else if (state.type === "fulfilled") {
      try {
        if (context.typeof(state.value) !== "string") throw new Error("invalid_result");
        const result = context.getString(state.value);
        if (Buffer.byteLength(result) >= maxFrame) throw new Error("frame_limit");
        if (pending.size) throw new Error("pending_child");
        finish({ type: "done", result: JSON.parse(result) });
      } finally {
        state.value.dispose();
      }
    } else if (runtime.hasPendingJob()) {
      setImmediate(pump);
    } else if (!pending.size) {
      fail();
    }
  } catch {
    fail();
  }
}

async function start(frame) {
  started = true;
  if (typeof frame.source !== "string" || Buffer.byteLength(frame.source) > 16384) {
    throw new Error("invalid_source");
  }
  deadline = performance.now() + Math.min(frame.timeoutMs, 180000);
  const quickJS = await getQuickJS();
  runtime = quickJS.newRuntime();
  runtime.setMemoryLimit(8 * 1024 * 1024);
  runtime.setMaxStackSize(256 * 1024);
  runtime.setInterruptHandler(() => performance.now() >= deadline);
  context = runtime.newContext();
  if (frame.replay) {
    if (!Number.isSafeInteger(frame.replay.timeMs) || !Number.isSafeInteger(frame.replay.seed))
      throw new Error("invalid_replay_clock");
    const clock = context.evalCode(`(() => {
      const NativeDate = Date;
      const fixed = ${frame.replay.timeMs};
      class ReplayDate extends NativeDate {
        constructor(...args) { super(...(args.length ? args : [fixed])); }
        static now() { return fixed; }
      }
      globalThis.Date = ReplayDate;
      let seed = ${frame.replay.seed} >>> 0;
      Object.defineProperty(Math, "random", { value: () => {
        seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
        return seed / 4294967296;
      }, writable: false, configurable: false });
    })()`);
    if (clock.error) {
      clock.error.dispose();
      throw new Error("invalid_replay_clock");
    }
    clock.value.dispose();
  }
  const bridge = context.newFunction("dispatch", (value) => {
    if (context.typeof(value) !== "string") throw new Error("invalid_request");
    const payload = context.getString(value);
    const message = JSON.parse(payload);
    if (message.type === "group") {
      send(message);
      return context.undefined;
    }
    if (Buffer.byteLength(payload) > 4096 || nextId >= 3) throw new Error("child_limit");
    const { profile, input: childInput, groupId } = message;
    const requestId = ++nextId;
    const promise = context.newPromise();
    pending.set(requestId, promise);
    send({ type: "agent", requestId, profile, input: childInput, groupId });
    return promise.handle;
  });
  const entry = context.evalCode(String.raw`
    (dispatch, input, source) => {
      "use strict";
      const parse = JSON.parse;
      const stringify = JSON.stringify;
      const all = Promise.all.bind(Promise);
      const args = parse(input);
      let nextGroup = 0;
      let activeGroup = null;
      const agent = async (profile, data) => parse(await dispatch(stringify({ profile, input: data, groupId: activeGroup })));
      const parallel = async (tasks) => {
        if (!Array.isArray(tasks) || tasks.length > 2) throw Error("invalid_parallel");
        const groupId = ++nextGroup;
        dispatch(stringify({ type: "group", groupId, status: "running" }));
        try {
          const values = tasks.map((task) => {
            const parent = activeGroup;
            activeGroup = groupId;
            try { return typeof task === "function" ? task() : task; }
            finally { activeGroup = parent; }
          });
          const results = await all(values);
          dispatch(stringify({ type: "group", groupId, status: "completed" }));
          return results;
        } catch (error) {
          dispatch(stringify({ type: "group", groupId, status: "failed" }));
          throw error;
        }
      };
      const AsyncFunction = (async () => {}).constructor;
      let run;
      try {
        // Also accept the anonymous async wrapper commonly emitted by models.
        const body = /^\s*async\s+function\s*\(\s*args\s*\)\s*\{/.test(source)
          ? "return await (" + source.trimEnd().replace(/;$/, "") + "\n)(args);"
          : source;
        run = new AsyncFunction("args", "agent", "parallel", body);
      } catch {
        return false;
      }
      return (async () => stringify(await run(args, agent, parallel)))();
    }
  `);
  if (entry.error) {
    entry.error.dispose();
    bridge.dispose();
    throw new Error("invalid_source");
  }
  const args = context.newString(JSON.stringify(frame.args));
  const source = context.newString(frame.source);
  const result = context.callFunction(entry.value, context.undefined, bridge, args, source);
  source.dispose();
  args.dispose();
  bridge.dispose();
  entry.value.dispose();
  if (result.error) {
    result.error.dispose();
    throw new Error("invalid_source");
  }
  root = result.value;
  if (context.typeof(root) === "boolean") {
    fail("invalid_workflow_script");
    return;
  }
  pump();
}

input.on("line", (line) => {
  if (finished) return;
  try {
    const frame = JSON.parse(line);
    if (!started && frame.type === "start") {
      void start(frame).catch(() => fail());
    } else if (frame.type === "result" && context) {
      const promise = pending.get(frame.requestId);
      if (!promise) throw new Error("unexpected_response");
      pending.delete(frame.requestId);
      const value = context.newString(JSON.stringify(frame.value));
      promise.resolve(value);
      value.dispose();
      promise.dispose();
      pump();
    } else throw new Error("unexpected_frame");
  } catch {
    fail();
  }
});

input.on("close", () => {
  for (const promise of pending.values()) promise.dispose();
  pending.clear();
  root?.dispose();
  context?.dispose();
  runtime?.dispose();
});
