import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentSession } from "../src/client/request-scope";
import type { SessionView } from "../src/contracts/page";
import { PAGE_CATALOG } from "../src/lib/page-catalog";

const id = "99500000-0000-4000-8000-000000000001";
let view: SessionView;
let session: DocumentSession;
let documentEvents: EventTarget & {
  visibilityState: string;
  querySelectorAll: () => HTMLElement[];
};
let root: HTMLElement;
let portal: HTMLElement;
let stop: (() => void) | undefined;
const fetcher = vi.fn<typeof fetch>();
const replace = vi.fn();
const clear = vi.fn();
const sessionResponse = (patch: Partial<SessionView> = {}) => {
  const body = { ...view, ...patch, csrfToken: "x".repeat(43) };
  return Response.json(body, { headers: { "X-Compass-View-Revision": body.viewRevision } });
};
function visibility(state: string) {
  documentEvents.visibilityState = state;
  documentEvents.dispatchEvent(new Event("visibilitychange"));
}
async function start() {
  stop = session.start();
  await vi.advanceTimersByTimeAsync(0);
  expect(session.phase).toBe("active");
}
beforeEach(() => {
  vi.useFakeTimers({
    toFake: ["Date", "performance", "setTimeout", "clearTimeout", "setInterval", "clearInterval"],
  });
  vi.setSystemTime(new Date("2026-11-05T00:00:00Z"));
  view = {
    user: { id, loginName: "test.admin", displayName: "管理员", preferences: {}, version: "1" },
    sessionId: id,
    isPlatformAdmin: false,
    expiresAt: new Date(Date.now() + 3600000).toISOString(),
    viewRevision: "a".repeat(64),
    authzVersion: null,
    capabilities: [],
    tenantCapabilities: [],
    navigation: [],
    scope: {},
    tenant: null,
    workspace: null,
  };
  root = { hidden: true, inert: true } as HTMLElement;
  portal = { hidden: false, inert: false } as HTMLElement;
  documentEvents = Object.assign(new EventTarget(), {
    visibilityState: "visible",
    querySelectorAll: () => [portal],
  });
  vi.stubGlobal("document", documentEvents);
  vi.stubGlobal("window", Object.assign(new EventTarget(), { confirm: vi.fn(() => true) }));
  vi.stubGlobal("location", {
    pathname: "/settings/profile",
    search: "",
    href: "http://compass.test/settings/profile",
    replace,
  });
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.stubGlobal("fetch", fetcher);
  fetcher.mockImplementation(async () => sessionResponse());
  session = new DocumentSession({
    page: PAGE_CATALOG.find((p) => p.id === "profile")!,
    session: view,
    viewKey: "/settings/profile",
  });
  session.attach(root);
  session.onClear(clear);
});
afterEach(() => {
  stop?.();
  stop = undefined;
  session.dispose();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe("quiet background authorization", () => {
  it("keeps the page unchanged across app switches and continues the regular heartbeat", async () => {
    await start();
    session.setDirty(true);
    visibility("hidden");
    visibility("visible");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(session.phase).toBe("active");
    expect(root.hidden).toBe(false);
    expect(portal.hidden).toBe(false);
    visibility("hidden");
    await vi.advanceTimersByTimeAsync(65000);
    expect(fetcher).toHaveBeenCalledTimes(5);
    expect(session.dirty).toBe(true);
    expect(clear).not.toHaveBeenCalled();
    await expect(session.request("me", "PATCH", {})).resolves.toMatchObject({ sessionId: id });
    visibility("visible");
    expect(root.hidden).toBe(false);
    expect(portal.hidden).toBe(false);
    expect(replace).not.toHaveBeenCalled();
  });

  it("continues streaming and accepts the final result while the page is in the background", async () => {
    await start();
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    let signal!: AbortSignal;
    fetcher.mockImplementation(async (url, options) => {
      if (String(url).includes("/session?")) return sessionResponse();
      signal = options!.signal!;
      return new Response(
        new ReadableStream({
          start(controller) {
            stream = controller;
          },
        }),
        {
          headers: {
            "content-type": "text/event-stream",
            "X-Compass-View-Revision": view.viewRevision,
          },
        },
      );
    });
    const onEvent = vi.fn();
    const call = session.modelRequest(
      `platform/models/${id}/tests`,
      { call_id: id },
      {
        signal: new AbortController().signal,
        parseResult: (value) => value,
        parseEvent: (value) => value as { type: string; call_id: string },
        onEvent,
      },
    );
    await vi.advanceTimersByTimeAsync(0);
    const send = (type: string, text?: string) =>
      stream.enqueue(
        new TextEncoder().encode(`data: ${JSON.stringify({ type, call_id: id, text })}\n\n`),
      );
    send("text_delta", "前台片段");
    await vi.advanceTimersByTimeAsync(0);
    visibility("hidden");
    await vi.advanceTimersByTimeAsync(40000);
    expect(signal.aborted).toBe(false);
    send("text_delta", "后台片段");
    send("final");
    stream.close();
    await call;
    expect(onEvent.mock.calls.map(([event]) => event.type)).toEqual([
      "text_delta",
      "text_delta",
      "final",
    ]);
    expect(clear).not.toHaveBeenCalled();
    visibility("visible");
    expect(session.phase).toBe("active");
    expect(root.hidden).toBe(false);
    expect(replace).not.toHaveBeenCalled();
  });

  it("binds analysis streams to the document generation and aborts them on retirement", async () => {
    await start();
    let streamSignal!: AbortSignal;
    fetcher.mockImplementation(
      async () =>
        new Response(new ReadableStream(), {
          headers: {
            "content-type": "text/event-stream",
            "X-Compass-View-Revision": view.viewRevision,
          },
        }),
    );
    const owner = new AbortController();
    const pending = session.streamRequest(
      "analysis/runs",
      { tenantId: "tenant", workspaceId: "workspace", question: "分析" },
      {
        signal: owner.signal,
        consume: async (_body, signal) => {
          streamSignal = signal;
          await new Promise<never>((_resolve, reject) => {
            const aborted = () => reject(signal.reason);
            if (signal.aborted) aborted();
            else signal.addEventListener("abort", aborted, { once: true });
          });
        },
      },
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(streamSignal.aborted).toBe(false);

    session.retire("访问范围已变化，请重新加载。");

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(streamSignal.aborted).toBe(true);
    expect(session.phase).toBe("retired");
    expect(clear).toHaveBeenCalledOnce();
  });

  it("rejects oversized analysis input before issuing a request", async () => {
    await start();
    const calls = fetcher.mock.calls.length;
    await expect(
      session.streamRequest(
        "analysis/runs",
        { question: "问".repeat(64 * 1024) },
        {
          signal: new AbortController().signal,
          consume: async () => undefined,
        },
      ),
    ).rejects.toMatchObject({ status: 422 });
    expect(fetcher).toHaveBeenCalledTimes(calls);
  });

  it("uses the delivered CSV filename and releases download URLs on conversation reset", async () => {
    await start();
    const click = vi.fn();
    const anchor = { href: "", download: "", click };
    Object.assign(documentEvents, { createElement: vi.fn(() => anchor) });
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:analysis");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);

    session.download(new Blob(["a,b\n1,2\n"]), "retail-products-run.csv");

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(anchor).toMatchObject({
      href: "blob:analysis",
      download: "retail-products-run.csv",
    });
    expect(click).toHaveBeenCalledOnce();
    session.releaseDownloads();
    expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith("blob:analysis");
    await vi.advanceTimersByTimeAsync(1000);
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    createObjectURL.mockRestore();
    revokeObjectURL.mockRestore();
  });

  it.each(["denial", "revision", "error"])(
    "keeps an in-flight probe current through app switches and processes its %s",
    async (kind) => {
      await start();
      let finish!: (response: Response) => void;
      let fail!: (error: Error) => void;
      let signal!: AbortSignal;
      fetcher.mockImplementationOnce((_url, options) => {
        signal = options!.signal!;
        return new Promise<Response>((resolve, reject) => {
          finish = resolve;
          fail = reject;
        });
      });
      const pending = session.probe();
      visibility("hidden");
      visibility("visible");
      expect(signal.aborted).toBe(false);
      expect(fetcher).toHaveBeenCalledTimes(2);
      if (kind === "error") fail(new Error("temporary failure"));
      else
        finish(
          kind === "denial"
            ? new Response("{}", { status: 403 })
            : sessionResponse({ viewRevision: "b".repeat(64) }),
        );
      await pending;
      expect(session.phase).toBe(kind === "error" ? "active" : "retired");
      expect(clear).toHaveBeenCalledTimes(kind === "error" ? 0 : 1);
    },
  );

  it("aborts an existing stream when the background heartbeat confirms access removal", async () => {
    await start();
    let signal!: AbortSignal;
    fetcher.mockImplementation(async (url, options) => {
      if (String(url).includes("/session?")) return sessionResponse();
      signal = options!.signal!;
      return new Response(new ReadableStream(), {
        headers: {
          "content-type": "text/event-stream",
          "X-Compass-View-Revision": view.viewRevision,
        },
      });
    });
    const onEvent = vi.fn();
    const call = session
      .modelRequest(
        `platform/models/${id}/tests`,
        { call_id: id },
        {
          signal: new AbortController().signal,
          parseResult: (value) => value,
          parseEvent: (value) => value as { type: string; call_id: string },
          onEvent,
        },
      )
      .catch((error) => error as Error);
    await vi.advanceTimersByTimeAsync(0);
    visibility("hidden");
    fetcher.mockResolvedValueOnce(new Response("{}", { status: 403 }));
    await vi.advanceTimersByTimeAsync(15000);
    expect(signal.aborted).toBe(true);
    expect(await call).toMatchObject({ name: "AbortError" });
    expect(onEvent).not.toHaveBeenCalled();
    expect(clear).toHaveBeenCalledOnce();
    expect(session.phase).toBe("retired");
  });

  it.each([401, 403, "revision"] as const)(
    "still clears state on a confirmed %s response while hidden",
    async (outcome) => {
      await start();
      session.setDirty(true);
      visibility("hidden");
      fetcher.mockImplementation(async () =>
        outcome === "revision"
          ? sessionResponse({ viewRevision: "b".repeat(64) })
          : new Response("{}", { status: outcome }),
      );
      await session.probe();
      expect(session.phase).toBe("retired");
      expect(root.hidden).toBe(true);
      expect(clear).toHaveBeenCalledOnce();
      expect(session.dirty).toBe(false);
      visibility("visible");
      expect(session.phase).toBe("retired");
    },
  );

  it("keeps the page and draft unchanged across transient heartbeat failures", async () => {
    await start();
    session.setDirty(true);
    fetcher.mockImplementation(async () => new Response("{}", { status: 503 }));
    visibility("hidden");
    await vi.advanceTimersByTimeAsync(65000);
    visibility("visible");
    expect(session.phase).toBe("active");
    expect(root.hidden).toBe(false);
    expect(portal.hidden).toBe(false);
    expect(session.dirty).toBe(true);
    expect(clear).not.toHaveBeenCalled();
    fetcher.mockImplementation(async () => sessionResponse());
    await vi.advanceTimersByTimeAsync(15000);
    expect(session.phase).toBe("active");
    expect(replace).not.toHaveBeenCalled();
  });

  it("allows ordinary requests after a long browser timer pause without a resume gate", async () => {
    await start();
    session.setDirty(true);
    visibility("hidden");
    vi.setSystemTime(Date.now() + 120000);
    visibility("visible");
    await expect(session.request("me")).resolves.toMatchObject({ sessionId: id });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(root.hidden).toBe(false);
    expect(clear).not.toHaveBeenCalled();
  });

  it("still retires an expired login while the document is hidden", async () => {
    view.expiresAt = new Date(Date.now() + 1000).toISOString();
    await start();
    visibility("hidden");
    await vi.advanceTimersByTimeAsync(1000);
    expect(session.phase).toBe("retired");
    expect(clear).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledWith("/login");
  });
});
