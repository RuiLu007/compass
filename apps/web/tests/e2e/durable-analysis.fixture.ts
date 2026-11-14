import { createHash, randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import type { Page, Route } from "@playwright/test";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import type { components } from "../../src/generated/api";

type Schema = components["schemas"];
export type AnalysisRequest = Schema["SessionSubmit"] & { tenantId: string; workspaceId: string };
export type Scenario =
  | "normal"
  | "canceled_confirmed"
  | "cancel_conflict"
  | "cleanup_unconfirmed"
  | "already_terminal_completed"
  | "already_terminal_disconnected"
  | "plan";
type Progress = Schema["TimelineValue"]["event"];
type Event = Schema["AgentPacket"]["event"];
const generatedSchema = createRequire(import.meta.url)(
  "../../src/generated/model-contracts.schema.json",
);
const validator = new Ajv2020({ allErrors: true, strict: false });
addFormats(validator);
validator.addSchema(generatedSchema, "compass-models");
const validateResponse = validator.compile({ $ref: "compass-models#/$defs/AgentResponse" });
function agentResponse(value: unknown): Schema["AgentResponse"] {
  if (!validateResponse(value))
    throw new Error(`Invalid v2 UI fixture: ${JSON.stringify(validateResponse.errors)}`);
  return value as Schema["AgentResponse"];
}
type Run = {
  id: string;
  sessionId: string;
  request: AnalysisRequest;
  ordinal: number;
  public: Schema["RunPublicSnapshot"];
  messages: Schema["MessageSnapshot"][];
  objects: Record<string, Extract<Event, { type: "view.upsert" }>["payload"]>;
  packets: Schema["AgentPacket"][];
  scenario: Scenario;
  held: boolean;
  failedRead: boolean;
  timeline: number;
  stepId: string;
  modelCallId: string;
  toolId: string;
};
export const fixtureProviderId = "99300000-0000-4000-8000-000000000101";
const providerId = fixtureProviderId;
export function normalResult() {
  const channelStepId = "98900000-0000-4000-8000-000000000301";
  const productStepId = "98900000-0000-4000-8000-000000000302";
  const channelModelCallId = "98900000-0000-4000-8000-000000000303";
  const productModelCallId = "98900000-0000-4000-8000-000000000304";
  const answerStepId = "98900000-0000-4000-8000-000000000305";
  const answerModelCallId = "98900000-0000-4000-8000-000000000306";
  const analysisNotes = Array.from(
    { length: 55 },
    (_, index) => `${index + 1}. 销售分析示例：先比较渠道表现，再核对商品与库存变化。`,
  ).join("\n");
  const text = [
    "## 销售表现",
    "POS 渠道贡献了 **120 元**，口径见[分析说明](https://example.com/analysis)。",
    "> 按实付金额汇总。",
    "| 渠道 | 实付金额 | 对比时段 | 商品 |" +
      "\n| --- | ---: | --- | --- |\n| POS | 120 元 | 上周 | 示例商品 |",
    "```sql\nSELECT " + "channel_code, ".repeat(12) + "paid_cent FROM sales;\n```",
    "- [x] 已核对渠道\n- [ ] 待核对库存",
    '<script id="answer-script">window.answerScriptRan = true</script>',
    "[不安全链接](javascript:alert%281%29)",
    analysisNotes,
  ].join("\n\n");
  const csv = "product,amount\n示例商品,120\n";
  const events = [
    {
      type: "run.routed",
      payload: {
        providerId,
        modelLabel: "DeepSeek V4 Pro",
        sceneId: "SC-02",
        toolsEnabled: true,
      },
    },
    {
      type: "step.started",
      payload: { stepId: channelStepId, stepIndex: 1, modelCallId: channelModelCallId },
    },
    {
      type: "tool.started",
      payload: {
        stepId: channelStepId,
        toolCallId: "channel_query",
        toolIndex: 1,
        toolKind: "retail_sales",
        inputPreview: '{"dimension":"channel"}',
        params: {
          dimension: "channel",
          period: { start: "2025-11-24", end: "2025-11-30" },
          compareTo: { start: "2025-11-17", end: "2025-11-23" },
        },
      },
    },
    {
      type: "tool.finished",
      payload: {
        stepId: channelStepId,
        toolCallId: "channel_query",
        toolIndex: 1,
        status: "ok",
        rowCount: 2,
        resultPreview: '{"rows":[{"code":"POS","paidCent":"12000"}]}',
      },
    },
    { type: "step.finished", payload: { stepId: channelStepId, status: "completed" } },
    {
      type: "step.started",
      payload: { stepId: productStepId, stepIndex: 2, modelCallId: productModelCallId },
    },
    {
      type: "tool.started",
      payload: {
        stepId: productStepId,
        toolCallId: "product_query",
        toolIndex: 1,
        toolKind: "retail_sales",
        params: {
          dimension: "product",
          channelCode: "POS",
          period: { start: "2025-11-24", end: "2025-11-30" },
          compareTo: { start: "2025-11-17", end: "2025-11-23" },
        },
      },
    },
    {
      type: "tool.finished",
      payload: {
        stepId: productStepId,
        toolCallId: "product_query",
        toolIndex: 1,
        status: "ok",
        rowCount: 1,
      },
    },
    { type: "step.finished", payload: { stepId: productStepId, status: "completed" } },
    {
      type: "step.started",
      payload: { stepId: answerStepId, stepIndex: 3, modelCallId: answerModelCallId },
    },
    { type: "step.finished", payload: { stepId: answerStepId, status: "completed" } },
  ];
  return { events, text, csv, productStepId };
}

/** A per-test durable service: browser reloads retain sessions, messages and native cursors. */
export class DurableAnalysisFixture {
  readonly requests: AnalysisRequest[] = [];
  readonly histories: { question: string; answer: string }[][] = [];
  readonly controls: { runId: string; body: Record<string, unknown> }[] = [];
  readonly runs = new Map<string, Run>();
  readonly sessions = new Map<string, Run[]>();
  snapshotReads = 0;
  artifactDownloads = 0;
  cancelCount = 0;
  streamRequests = 0;
  lostStreamResponses = 0;
  workflowReads = 0;
  workflowConfirmations = 0;
  private expiredDrafts = new Set<string>();
  private mode: Scenario = "normal";
  private held = false;
  private loseNextStream = false;
  private current?: Run;
  private notifications = new Set<() => void>();

  constructor(
    private page: Page,
    readonly revision: string,
  ) {}

  async install() {
    // Keep protocol routes scoped to this test's browser context.
    await this.page.context().route("**/api/web/v1/analysis/**", (route) => this.route(route));
  }

  scenario(value: Scenario) {
    this.mode = value;
  }
  holdNext() {
    this.held = true;
  }
  loseFirstStream() {
    this.loseNextStream = true;
  }
  release() {
    if (this.current) this.completeNormal(this.current);
  }
  releaseTool() {
    const run = this.required();
    this.progress(run, this.preparation(run, "prepared"));
    this.startTool(run);
  }
  finishInteractive() {
    const run = this.required();
    this.progress(run, {
      type: "tool.finished",
      payload: {
        stepId: run.stepId,
        toolCallId: run.toolId,
        toolIndex: 1,
        status: "ok",
        rowCount: 1,
      },
    });
    this.progress(run, {
      type: "step.finished",
      payload: { stepId: run.stepId, status: "completed" },
    });
    this.complete(run, "已持久保存的完整回答。");
  }
  releaseTerminal() {
    const run = this.required();
    if (run.scenario.startsWith("already_terminal")) {
      run.failedRead = false;
      this.notify();
    } else if (run.scenario === "cleanup_unconfirmed") {
      this.status(run, "paused", "cleanup_unconfirmed", "unconfirmed", "blocked");
    } else {
      this.status(run, "canceled", null, "confirmed");
    }
  }
  advancePlan() {
    this.plan(this.required(), 2, "t2", "verified", "pending", "select");
  }
  finishPlan() {
    const run = this.required();
    this.plan(run, 3, null, "verified", "verified", "complete");
    this.progress(run, {
      type: "step.finished",
      payload: { stepId: run.stepId, status: "completed" },
    });
    this.complete(run, "已核对两周渠道变化与商品贡献。");
  }
  publishWorkflow(
    status: Schema["PublicWorkflow"]["status"],
    name: string,
    options: {
      expiresAt?: string;
      unavailable?: boolean;
      definition?: Pick<
        Schema["WorkflowDraftView"],
        "entryContract" | "description" | "parameters" | "args" | "scriptSource"
      >;
    } = {},
  ) {
    const run = this.required();
    const draftId = randomUUID();
    const value: Schema["PublicWorkflow"] = {
      draftId,
      draft: {
        name,
        entryContract: "channel_attribution@1",
        description: null,
        parameters: null,
        scriptSource: "return await agent.channel_attribution(args);",
        args: {
          baseline: { start: "2025-11-17", end: "2025-11-24" },
          current: { start: "2025-11-24", end: "2025-12-01" },
        },
        summary: "渠道与商品分析已经完成。",
        ...options.definition,
      },
      status,
      assetId: status === "saved" ? randomUUID() : null,
      expiresAt: options.expiresAt ?? new Date(Date.now() + 60_000).toISOString(),
    };
    this.progress(run, {
      type: "stream.extension",
      payload: {
        name: "compass.workflow.node",
        version: 1,
        required: false,
        data: {
          source: "dynamic",
          key: "save_offer",
          status: "completed",
          detail: draftId,
          asset: null,
        },
      },
    });
    this.emit(run, {
      type: "view.upsert",
      payload: { objectId: draftId, revision: 1, kind: "workflow", value },
    });
    if (options.unavailable) this.expiredDrafts.add(draftId);
    return value;
  }

  private required() {
    if (!this.current) throw new Error("No accepted UI fixture run");
    return this.current;
  }
  private notify() {
    for (const resolve of this.notifications) resolve();
  }
  private async changed() {
    let timer: ReturnType<typeof setTimeout>;
    let settle = () => {};
    await new Promise<void>((resolve) => {
      settle = resolve;
      this.notifications.add(resolve);
      timer = setTimeout(resolve, 1000);
    });
    clearTimeout(timer!);
    this.notifications.delete(settle);
  }
  private emit(run: Run, event: Event) {
    if (event.type === "run.upsert") run.public = event.payload;
    else if (event.type === "message.upsert")
      run.messages = [
        ...run.messages.filter((m) => m.messageId !== event.payload.messageId),
        event.payload,
      ];
    else if (event.type === "view.upsert") run.objects[event.payload.objectId] = event.payload;
    run.packets.push({
      packetId: `${run.id}:${run.packets.length + 1}`,
      runId: run.id,
      requestId: run.request.requestId,
      seq: `${1000 + run.packets.length + 1}-0`,
      occurredAt: "2026-11-12T00:00:00.000Z",
      event,
    });
    this.notify();
  }
  private progress(run: Run, event: Progress) {
    const ordinal = ++run.timeline;
    this.emit(run, {
      type: "view.upsert",
      payload: {
        objectId: `timeline:${ordinal}`,
        revision: 1,
        kind: "timeline",
        value: { ordinal, event },
      },
    });
  }
  private status(
    run: Run,
    status: Schema["RunPublicSnapshot"]["status"],
    reasonCode: string | null = null,
    cleanupStatus: Schema["RunPublicSnapshot"]["cleanupStatus"] = "confirmed",
    recoveryStatus: Schema["RunPublicSnapshot"]["recoveryStatus"] = "ready",
  ) {
    this.emit(run, {
      type: "run.upsert",
      payload: {
        ...run.public,
        version: run.public.version + 1,
        status,
        reasonCode,
        cleanupStatus,
        recoveryStatus,
      },
    });
  }
  private complete(run: Run, text: string) {
    const assistant = run.messages.find((m) => m.role === "assistant")!;
    this.emit(run, {
      type: "message.upsert",
      payload: {
        ...assistant,
        revision: assistant.revision + 1,
        status: "completed",
        content: {
          text,
          artifactIds: Object.values(run.objects)
            .filter((o) => o.kind === "artifact")
            .map((o) => o.value.id),
        },
      },
    });
    this.status(run, "completed");
  }
  private completeNormal(run: Run) {
    if (run.public.status === "completed") return;
    run.held = false;
    const result = normalResult();
    for (const event of result.events) this.progress(run, event as Progress);
    const id = randomUUID();
    this.emit(run, {
      type: "view.upsert",
      payload: {
        objectId: id,
        revision: 1,
        kind: "artifact",
        value: {
          id,
          name: `retail-products-${run.id}.csv`,
          mediaType: "text/csv",
          byteSize: Buffer.byteLength(result.csv),
          sha256: createHash("sha256").update(result.csv).digest("hex"),
          stepId: result.productStepId,
          toolCallId: "product_query",
        },
      },
    });
    this.complete(run, result.text);
  }
  private preparation(run: Run, status: "preparing" | "prepared"): Progress {
    return {
      type: "stream.extension",
      payload: {
        name: "compass.tool.preparation",
        version: 1,
        required: false,
        data: { stepId: run.stepId, toolIndex: 1, status },
      },
    };
  }
  private startTool(run: Run) {
    this.progress(run, {
      type: "tool.started",
      payload: {
        stepId: run.stepId,
        toolCallId: run.toolId,
        toolIndex: 1,
        toolKind: "retail_sales",
        params: {
          dimension: "channel",
          period: { start: "2025-11-24", end: "2025-11-30" },
          compareTo: null,
        },
      },
    });
  }
  private plan(
    run: Run,
    actionIndex: number,
    selectedTodoId: string | null,
    first: string,
    second: string,
    action: string,
  ) {
    const item = (id: string, goal: string, status: string, dependsOn: string[]) => ({
      id,
      goal,
      status,
      dependsOn,
      optional: false,
      reason: null,
    });
    this.progress(run, {
      type: "stream.extension",
      payload: {
        name: "compass.autonomy.action",
        version: 1,
        required: false,
        data: {
          stepId: run.stepId,
          actionIndex,
          action,
          code: "ok",
          level: null,
          layer: null,
          planVersion: 1,
          selectedTodoId,
          items: [
            item("t1", "比较渠道实付金额", first, []),
            item("t2", "分析商品贡献", second, ["t1"]),
          ],
        },
      },
    });
  }
  private create(request: AnalysisRequest) {
    const sessionId = request.sessionId ?? randomUUID();
    const previous = this.sessions.get(sessionId) ?? [];
    this.histories.push(
      previous
        .filter((r) => r.public.status === "completed")
        .map((r) => ({
          question: r.request.question,
          answer: r.messages.find((m) => m.role === "assistant")!.content.text,
        })),
    );
    const run: Run = {
      id: randomUUID(),
      sessionId,
      request,
      ordinal: previous.length + 1,
      public: {
        ordinal: previous.length + 1,
        version: 1,
        status: "running",
        reasonCode: null,
        recoveryStatus: "ready",
        cleanupStatus: "none",
      },
      messages: [],
      objects: {},
      packets: [],
      scenario: this.mode,
      held: this.held,
      failedRead: false,
      timeline: 0,
      stepId: randomUUID(),
      modelCallId: randomUUID(),
      toolId: "interactive_channel_query",
    };
    this.held = false;
    this.runs.set(run.id, run);
    this.sessions.set(sessionId, [...previous, run]);
    this.current = run;
    this.emit(run, { type: "run.upsert", payload: run.public });
    this.emit(run, {
      type: "message.upsert",
      payload: {
        messageId: randomUUID(),
        ordinal: 1,
        role: "user",
        revision: 1,
        status: "completed",
        content: { text: request.question, artifactIds: [] },
      },
    });
    this.emit(run, {
      type: "message.upsert",
      payload: {
        messageId: randomUUID(),
        ordinal: 2,
        role: "assistant",
        revision: 1,
        status: "streaming",
        content: { text: "", artifactIds: [] },
      },
    });
    if (run.scenario !== "normal") {
      this.progress(run, {
        type: "run.routed",
        payload: {
          providerId,
          modelLabel: "DeepSeek V4 Pro",
          sceneId: "SC-02",
          toolsEnabled: true,
        },
      });
      this.progress(run, {
        type: "step.started",
        payload: { stepId: run.stepId, stepIndex: 1, modelCallId: run.modelCallId },
      });
      if (run.scenario === "plan") this.plan(run, 1, "t1", "pending", "pending", "select");
      else if (run.scenario === "cleanup_unconfirmed")
        this.progress(run, this.preparation(run, "preparing"));
      else this.startTool(run);
    }
    return run;
  }
  private snapshot(sessionId: string) {
    const runs = this.sessions.get(sessionId) ?? [];
    const packets: Schema["AgentPacket"][] = [];
    for (const run of runs) {
      const events: Event[] = [
        { type: "run.upsert", payload: run.public },
        ...run.messages.map((payload): Event => ({ type: "message.upsert", payload })),
        ...Object.values(run.objects).map((payload): Event => ({ type: "view.upsert", payload })),
      ];
      events.forEach((event, index) =>
        packets.push({
          packetId: `snapshot:${run.id}:${index}`,
          runId: run.id,
          requestId: run.request.requestId,
          seq: null,
          occurredAt: "2026-11-12T00:00:00.000Z",
          event,
        }),
      );
    }
    return agentResponse({
      protocol: "compass.run",
      version: 2,
      sessionId,
      mode: "snapshot",
      packets,
      cursors: runs.map((r) => ({ runId: r.id, seq: r.packets.at(-1)!.seq })),
      page: { nextCursor: null },
    });
  }
  private async route(route: Route) {
    const request = route.request(),
      url = new URL(request.url());
    const parts = url.pathname.replace("/api/web/v1/analysis/", "").split("/");
    const headers = {
      "X-Compass-View-Revision": this.revision,
      "Cache-Control": "private, no-store",
    };
    const json = (body: unknown, status = 200) => route.fulfill({ status, headers, json: body });
    if (parts[0] === "workflows") {
      if (parts[1] === "drafts") {
        if (request.method() === "POST") this.workflowConfirmations++;
        else this.workflowReads++;
        if (this.expiredDrafts.has(parts[2]))
          return json({ error: { code: "draft_expired", message: "保存邀请已失效。" } }, 410);
        const draft = [...this.runs.values()]
          .flatMap((run) => Object.values(run.objects))
          .find((object) => object.kind === "workflow" && object.value.draftId === parts[2]);
        if (draft?.kind === "workflow") return json(draft.value.draft);
        return json({ error: { code: "not_found", message: "保存邀请不存在。" } }, 404);
      }
      return json({ items: [] });
    }
    if (parts.join("/") === "sessions/submit") {
      const body = request.postDataJSON() as AnalysisRequest;
      this.requests.push(body);
      const existing = [...this.runs.values()].find((r) => r.request.requestId === body.requestId);
      const run = existing ?? this.create(body);
      return json(
        {
          sessionId: run.sessionId,
          runId: run.id,
          streamUrl: `/v1/analysis/runs/${run.id}/events`,
        },
        202,
      );
    }
    if (parts.join("/") === "sessions")
      return json({
        items: [...this.sessions.entries()].reverse().map(([sessionId, runs]) => {
          const run = runs.at(-1)!;
          return {
            sessionId,
            title: runs[0].request.question.slice(0, 40),
            sessionType: "chat",
            iconKey: "chat",
            version: run.public.version,
            updatedAt: "2026-11-12T00:00:00.000Z",
            latestRunId: run.id,
            executionStatus: run.public.status,
          };
        }),
        nextCursor: null,
      });
    if (parts[0] === "sessions" && parts[2] === "messages") {
      this.snapshotReads++;
      if (this.sessions.get(parts[1])?.some((r) => r.failedRead))
        return json(
          { error: { code: "unavailable", message: "会话读取暂时不可用，请重新加载。" } },
          503,
        );
      return json(this.snapshot(parts[1]));
    }
    const run = this.runs.get(parts[1]);
    if (!run) return json({ error: { code: "not_found", message: "测试会话不存在" } }, 404);
    if (parts[2] === "cancel") {
      this.cancelCount++;
      this.controls.push({ runId: run.id, body: request.postDataJSON() });
      if (run.scenario === "cancel_conflict" && this.cancelCount === 1) {
        this.status(run, "running");
        return json(
          { error: { code: "version_conflict", message: "任务状态已更新，请重新加载后操作。" } },
          409,
        );
      }
      if (run.scenario === "cancel_conflict") {
        if (request.postDataJSON().expectedVersion !== run.public.version)
          throw new Error("Control retry did not use the current Message version");
      }
      if (run.scenario.startsWith("already_terminal")) {
        this.progress(run, {
          type: "tool.finished",
          payload: {
            stepId: run.stepId,
            toolCallId: run.toolId,
            toolIndex: 1,
            status: "ok",
            rowCount: 1,
          },
        });
        this.progress(run, {
          type: "step.finished",
          payload: { stepId: run.stepId, status: "completed" },
        });
        this.complete(run, "已持久保存的完整回答。");
        run.failedRead = run.scenario === "already_terminal_disconnected";
      } else {
        this.progress(run, {
          type: "step.finished",
          payload: { stepId: run.stepId, status: "canceled" },
        });
        this.status(run, "cancelling", null, "pending");
      }
      return json({
        runId: run.id,
        sessionId: run.sessionId,
        version: run.public.version,
        status: run.public.status,
        code: null,
      });
    }
    if (parts[2] === "artifacts") {
      this.artifactDownloads++;
      const artifact = Object.values(run.objects).find(
        (o) => o.kind === "artifact" && o.value.id === parts[3],
      );
      if (!artifact || artifact.kind !== "artifact") throw new Error("Unknown fixture artifact");
      return route.fulfill({
        headers: {
          ...headers,
          "Content-Type": "text/csv; charset=utf-8",
          "Content-Disposition": `attachment; filename="${artifact.value.name}"`,
        },
        body: normalResult().csv,
      });
    }
    if (parts[2] === "events") {
      this.streamRequests++;
      if (this.loseNextStream) {
        this.loseNextStream = false;
        for (const event of normalResult().events.slice(0, 5))
          this.progress(run, event as Progress);
        const assistant = run.messages.find((message) => message.role === "assistant")!;
        this.emit(run, {
          type: "message.upsert",
          payload: {
            ...assistant,
            revision: assistant.revision + 1,
            content: { text: "已核对 POS 渠道，后续商品分析尚未完成。", artifactIds: [] },
          },
        });
        this.status(run, "paused", "stream_data_lost", "none", "blocked");
        this.lostStreamResponses++;
        return json(
          {
            error: { code: "stream_data_lost", message: "增量记录不可用，请重新加载会话。" },
          },
          410,
        );
      }
      if (run.scenario === "normal" && !run.held) this.completeNormal(run);
      if (run.failedRead) return route.abort("failed");
      const after = Number((url.searchParams.get("after") ?? "0-0").split("-")[0]);
      let packets = run.packets.filter((p) => Number(p.seq!.split("-")[0]) > after);
      if (!packets.length) {
        await this.changed();
        if (run.failedRead) return route.abort("failed");
        packets = run.packets.filter((p) => Number(p.seq!.split("-")[0]) > after);
      }
      const body = packets.length
        ? (() => {
            const response = agentResponse({
              protocol: "compass.run",
              version: 2,
              sessionId: run.sessionId,
              mode: "delta",
              packets,
              cursors: [{ runId: run.id, seq: packets.at(-1)!.seq }],
              page: null,
            });
            return `event: agent\nid: ${run.id}:${packets.at(-1)!.seq}\ndata: ${JSON.stringify(response)}\n\n`;
          })()
        : ": heartbeat\n\n";
      return route.fulfill({ headers: { ...headers, "Content-Type": "text/event-stream" }, body });
    }
    throw new Error(`Unhandled durable fixture endpoint: ${url.pathname}`);
  }
}
