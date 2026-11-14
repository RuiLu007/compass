import type { Page, Route } from "@playwright/test";
import type { components } from "../../src/generated/api";
import { agentResponse, compareStreamIds } from "../../src/contracts/agent-response";

type Schema = components["schemas"];
type Event = Schema["AgentPacket"]["event"];
type ObjectView = Extract<Event, { type: "view.upsert" }>["payload"];
export const runtimeIds = {
  session: "98400000-0000-4000-8000-000000000001",
  run: "98400000-0000-4000-8000-000000000002",
  request: "98400000-0000-4000-8000-000000000003",
  user: "98400000-0000-4000-8000-000000000004",
  assistant: "98400000-0000-4000-8000-000000000005",
  node: "98400000-0000-4000-8000-000000000006",
};
export const allowedDetail = "AUTHORIZED_CHANNEL_DETAIL_984";
export const answerText = "渠道与商品分析已经完成，确认实付金额为 120 元。";
const submittedAt = Date.now();
const completedAt = submittedAt + 138_000;
export const narrationText = "过程提到最终回答，当前仍在核对渠道数据。";

function occurredAt(event: Event) {
  const terminal =
    event.type === "run.upsert" &&
    ["completed", "failed", "canceled"].includes(event.payload.status);
  return new Date(terminal ? completedAt : submittedAt).toISOString();
}

/** Fixed public contract inputs; expected business values are independent of the reducer. */
export class RuntimeProtocol {
  readonly packets: Schema["AgentPacket"][] = [];
  readonly messages = new Map<string, Schema["MessageSnapshot"]>();
  readonly objects = new Map<string, ObjectView>();
  state: Schema["RunPublicSnapshot"] = {
    ordinal: 1,
    version: 1,
    status: "created",
    reasonCode: "queued",
    recoveryStatus: "ready",
    cleanupStatus: "confirmed",
  };

  constructor(
    readonly question = "第 20 课异步经营分析",
    readonly requestId = runtimeIds.request,
  ) {
    this.append([
      { type: "run.upsert", payload: this.state },
      {
        type: "message.upsert",
        payload: {
          messageId: runtimeIds.user,
          ordinal: 1,
          role: "user",
          revision: 1,
          status: "completed",
          content: { text: question, artifactIds: [] },
        },
      },
      {
        type: "message.upsert",
        payload: {
          messageId: runtimeIds.assistant,
          ordinal: 2,
          role: "assistant",
          revision: 1,
          status: "streaming",
          content: { text: "", artifactIds: [] },
        },
      },
      {
        type: "view.upsert",
        payload: {
          objectId: runtimeIds.node,
          revision: 1,
          kind: "execution.node",
          value: {
            id: runtimeIds.node,
            revision: 1,
            kind: "narration",
            status: "running",
            title: "任务已接收，等待执行。",
            startedAtMs: submittedAt,
            dependsOn: [],
            display: { policy: "summary" },
          },
        },
      },
    ]);
  }

  append(events: Event[]) {
    for (const event of events) {
      if (event.type === "run.upsert") this.state = event.payload;
      if (event.type === "message.upsert")
        this.messages.set(event.payload.messageId, event.payload);
      if (event.type === "view.upsert") this.objects.set(event.payload.objectId, event.payload);
      const ordinal = this.packets.length + 1;
      this.packets.push({
        packetId: `${runtimeIds.run}:${ordinal}`,
        runId: runtimeIds.run,
        requestId: this.requestId,
        seq: `${1000 + ordinal}-0`,
        occurredAt: occurredAt(event),
        event,
      });
    }
  }

  transition(status: Schema["RunPublicSnapshot"]["status"], reasonCode: string | null = null) {
    this.append([
      {
        type: "run.upsert",
        payload: { ...this.state, version: this.state.version + 1, status, reasonCode },
      },
    ]);
  }

  begin() {
    const current = this.node();
    this.transition("running");
    this.append([
      {
        type: "view.upsert",
        payload: {
          ...current,
          revision: current.revision + 1,
          value: {
            ...current.value,
            revision: current.value.revision + 1,
            kind: "tool",
            title: "渠道查询",
            summary: narrationText,
            display: {
              policy: "preview",
              language: "json",
              inputPreview: '{"dimension":"channel","date":"2026-11-01"}',
              outputPreview: JSON.stringify({ rowCount: 2, channel: allowedDetail }),
            },
          },
        },
      },
    ]);
  }

  complete(status: "completed" | "failed" | "canceled" = "completed") {
    const current = this.node();
    const assistant = this.messages.get(runtimeIds.assistant)!;
    this.append([
      {
        type: "view.upsert",
        payload: {
          ...current,
          revision: current.revision + 1,
          value: {
            ...current.value,
            revision: current.value.revision + 1,
            status,
            endedAtMs: completedAt,
          },
        },
      },
      {
        type: "message.upsert",
        payload: {
          ...assistant,
          revision: assistant.revision + 1,
          status: status === "completed" ? "completed" : "incomplete",
          content: {
            text: status === "completed" ? answerText : "这段分析尚未提交为最终回答。",
            artifactIds: [],
          },
        },
      },
      {
        type: "run.upsert",
        payload: {
          ...this.state,
          version: this.state.version + 1,
          status,
          reasonCode: status === "failed" ? "upstream_error" : null,
        },
      },
    ]);
  }

  node() {
    const object = this.objects.get(runtimeIds.node)!;
    if (object.kind !== "execution.node") throw new Error("Missing fixture execution node");
    return object;
  }

  restrictDetails() {
    const current = this.node();
    // A current-scope snapshot preserves object identity/revision while omitting private detail.
    this.objects.set(runtimeIds.node, {
      ...current,
      value: {
        ...current.value,
        title: "渠道查询",
        summary: "已保留允许的执行状态。",
        display: { policy: "summary" },
      },
    });
  }

  snapshot() {
    const events: Event[] = [
      { type: "run.upsert", payload: this.state },
      ...[...this.messages.values()].map((payload): Event => ({ type: "message.upsert", payload })),
      ...[...this.objects.values()].map((payload): Event => ({ type: "view.upsert", payload })),
    ];
    return agentResponse({
      protocol: "compass.run",
      version: 2,
      sessionId: runtimeIds.session,
      mode: "snapshot",
      packets: events.map((event, index) => ({
        packetId: `snapshot:${runtimeIds.run}:${index}`,
        runId: runtimeIds.run,
        requestId: this.requestId,
        seq: null,
        occurredAt: occurredAt(event),
        event,
      })),
      cursors: [{ runId: runtimeIds.run, seq: this.packets.at(-1)!.seq }],
      page: { nextCursor: null },
    });
  }

  delta(after: string) {
    const packets = this.packets.filter((packet) => compareStreamIds(packet.seq!, after) > 0);
    if (!packets.length) return null;
    return agentResponse({
      protocol: "compass.run",
      version: 2,
      sessionId: runtimeIds.session,
      mode: "delta",
      packets,
      cursors: [{ runId: runtimeIds.run, seq: packets.at(-1)!.seq }],
      page: null,
    });
  }
}

/** Browser-only Agent protocol boundary; Web authentication and UI remain real. */
export class ProductionRuntimeFixture {
  readonly submissions: Record<string, unknown>[] = [];
  readonly delivered: Schema["AgentResponse"][] = [];
  readonly controls: string[] = [];
  rejectSubmissions = 0;
  disconnectNext = false;
  duplicateFrames = false;
  streamRequests = 0;
  snapshotReads = 0;
  sessionListReads = 0;
  protocol: RuntimeProtocol | null = null;
  titleStatus: Schema["SessionListItem"]["titleStatus"] = "fallback";
  private title: string | null = null;
  private metadataVersion = 0;
  private deleted = false;
  deletedRequests = 0;
  private waiters = new Set<() => void>();

  constructor(
    private page: Page,
    readonly revision: string,
  ) {}

  async install() {
    await this.page.context().route("**/api/web/v1/analysis/**", (route) => this.route(route));
  }

  generateTitle(title: string) {
    if (this.titleStatus !== "pending" || this.deleted) return;
    this.title = title;
    this.titleStatus = "generated";
    this.metadataVersion++;
  }

  private sessionItem(): Schema["SessionListItem"] | null {
    if (!this.protocol || this.deleted) return null;
    return {
      sessionId: runtimeIds.session,
      title: this.title ?? this.protocol.question,
      titleStatus: this.titleStatus,
      sessionType: "chat",
      iconKey: "chat",
      version: this.protocol.state.version + this.metadataVersion,
      updatedAt: "2026-11-12T00:00:00.000Z",
      latestRunId: runtimeIds.run,
      executionStatus: this.protocol.state.status,
    };
  }

  update(change: (protocol: RuntimeProtocol) => void) {
    if (!this.protocol) throw new Error("No accepted fixture Run");
    change(this.protocol);
    for (const wake of this.waiters) wake();
  }

  private async changed() {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let wake: () => void = () => {};
    await new Promise<void>((resolve) => {
      wake = resolve;
      this.waiters.add(wake);
      timer = setTimeout(resolve, 1000);
    });
    clearTimeout(timer);
    this.waiters.delete(wake);
  }

  private async route(route: Route) {
    const request = route.request(),
      url = new URL(request.url());
    const path = url.pathname.replace("/api/web/v1/analysis/", "");
    const headers = {
      "X-Compass-View-Revision": this.revision,
      "Cache-Control": "private, no-store",
    };
    const json = (body: unknown, status = 200) => route.fulfill({ status, headers, json: body });
    if (path === "sessions/submit") {
      const input = request.postDataJSON() as Record<string, unknown>;
      this.submissions.push(input);
      if (this.rejectSubmissions > 0) {
        this.rejectSubmissions--;
        return route.fulfill({
          status: 429,
          headers: { ...headers, "Retry-After": "1" },
          json: {
            error: { code: "rate_limited", message: "当前任务已满，请稍后重试。你的问题已保留。" },
          },
        });
      }
      this.protocol ??= new RuntimeProtocol(String(input.question), String(input.requestId));
      return json(
        {
          sessionId: runtimeIds.session,
          runId: runtimeIds.run,
          streamUrl: `/v1/analysis/runs/${runtimeIds.run}/events`,
          session: this.sessionItem(),
        },
        202,
      );
    }
    const item = this.sessionItem();
    if (path === "sessions") {
      this.sessionListReads++;
      return json({ items: item ? [item] : [], nextCursor: null });
    }
    if (path === "workflows") return json({ items: [] });
    if (!this.protocol || this.deleted)
      return json({ error: { code: "not_found", message: "无此任务。" } }, 404);
    if (path === `sessions/${runtimeIds.session}`) {
      if (request.method() === "PATCH") {
        this.title = request.postDataJSON().title;
        this.titleStatus = "manual";
        this.metadataVersion++;
      }
      if (request.method() === "DELETE") {
        this.deletedRequests++;
        if (!["completed", "failed", "canceled"].includes(this.protocol.state.status))
          return json(
            { error: { code: "session_busy", message: "请先停止任务，等任务结束后再删除会话。" } },
            409,
          );
        this.deleted = true;
        return json({ sessionId: runtimeIds.session });
      }
      return json(this.sessionItem());
    }
    if (path === `sessions/${runtimeIds.session}/messages`) {
      this.snapshotReads++;
      const response = this.protocol.snapshot();
      this.delivered.push(response);
      return json(response);
    }
    if (path === `runs/${runtimeIds.run}/events`) {
      this.streamRequests++;
      if (this.disconnectNext) {
        this.disconnectNext = false;
        return route.abort("failed");
      }
      const after = url.searchParams.get("after") ?? "0-0";
      let delta = this.protocol.delta(after);
      if (!delta) {
        await this.changed();
        if (this.page.isClosed()) return;
        delta = this.protocol.delta(after);
      }
      let body = ": heartbeat\n\n";
      if (delta) {
        this.delivered.push(delta);
        body = `event: agent\nid: ${runtimeIds.run}:${delta.cursors[0].seq}\ndata: ${JSON.stringify(delta)}\n\n`;
        if (this.duplicateFrames) body += body;
      }
      return route.fulfill({ headers: { ...headers, "Content-Type": "text/event-stream" }, body });
    }
    if (/^runs\/[^/]+\/(pause|resume|cancel|decisions)$/.test(path)) {
      this.controls.push(path.split("/").at(-1)!);
      return json(
        { error: { code: "state_conflict", message: "当前调度状态无需再次恢复。" } },
        409,
      );
    }
    throw new Error(`Unexpected production UI fixture endpoint: ${path}`);
  }
}
