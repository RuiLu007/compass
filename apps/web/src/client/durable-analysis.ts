import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  reduceAgentResponse,
  readAgentResponses,
  type Conversation,
  type RunView,
} from "../contracts/agent-response";
import { applyAnalysisRunEvent, confirmCanceledSteps, type AnalysisTurnView } from "./analysis-run";
import { toolPreparation } from "../contracts/analysis-run";
import { ApiError, type DocumentSession } from "./request-scope";

export const terminalRun = (run: RunView) =>
  !!run.run && ["completed", "failed", "canceled"].includes(run.run.status);
export async function requestDurableControl(
  api: DocumentSession,
  sessionId: string,
  runId: string,
  kind: string,
  body: Record<string, unknown>,
) {
  let command = body;
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await api.request(`analysis/runs/${runId}/${kind}`, "POST", command, {
        durable: true,
        retainOnNotFound: true,
      });
    } catch (error) {
      if (
        attempt >= 2 ||
        !["cancel", "pause"].includes(kind) ||
        !(error instanceof ApiError) ||
        error.code !== "version_conflict"
      )
        throw error;
      if (error.currentVersion) {
        command = {
          ...body,
          requestId: crypto.randomUUID(),
          expectedVersion: error.currentVersion,
        };
        continue;
      }
      const query = new URLSearchParams({
        tenantId: String(body.tenantId),
        workspaceId: String(body.workspaceId),
        runId,
      });
      const state = reduceAgentResponse(
        null,
        await api.request(`analysis/sessions/${sessionId}/messages?${query}`),
      );
      const run = state.runs[runId];
      if (!run?.run) throw error;
      if (terminalRun(run) || (kind === "pause" && run.run.status !== "running")) return;
      command = { ...body, requestId: crypto.randomUUID(), expectedVersion: run.run.version };
    }
  }
}
export function turnFromRun(view: RunView): AnalysisTurnView {
  const messages = Object.values(view.messages);
  let turn: AnalysisTurnView = {
    id: view.runId,
    runId: view.runId,
    requestId: view.requestId,
    question: messages.find((m) => m.role === "user")?.content.text ?? "",
    modelLabel: "Compass",
    steps: [],
    phase: "running",
    stopState: "idle",
  };
  const objects = Object.values(view.objects);
  const nodes = objects.filter((v) => v.kind === "execution.node").map((v) => v.value);
  const timeline = objects
    .filter((v) => v.kind === "timeline")
    .sort((a, b) => a.value.ordinal - b.value.ordinal);
  for (const {
    value: { event },
  } of timeline) {
    // The execution tree already includes model steps and tools, including child agents.
    if (
      nodes.length &&
      (event.type.startsWith("step.") ||
        event.type.startsWith("tool.") ||
        (event.type === "stream.extension" && toolPreparation(event)))
    )
      continue;
    turn = applyAnalysisRunEvent(turn, view.runId, event);
  }
  if (nodes.length) {
    turn.execution = nodes;
    const latest = nodes.reduce((last, node) =>
      (node.endedAtMs ?? node.startedAtMs) >= (last.endedAtMs ?? last.startedAtMs) ? node : last,
    );
    turn.latestExecutionId = view.latestExecutionId ?? latest.id;
  }
  turn.workflowDraft = objects.find((object) => object.kind === "workflow")?.value;
  const status = view.run?.status;
  turn.runStatus = status === "paused" && view.run?.reasonCode === "queued" ? "created" : status;
  turn.cancellationBlocked =
    status === "cancelling" &&
    (view.run?.recoveryStatus === "blocked" || view.run?.cleanupStatus === "unconfirmed");
  turn.submittedAtMs = view.submittedAtMs;
  if (view.finishedAtMs !== undefined && view.submittedAtMs !== undefined)
    turn.durationMs = Math.max(0, view.finishedAtMs - view.submittedAtMs);
  const answer = messages.find((m) => m.role === "assistant" && m.status === "completed");
  if (status === "completed" && answer) {
    turn.phase = "finished";
    turn.terminal = {
      status: "completed",
      finalAnswer: answer.content.text,
      file: { status: "none" },
    };
  } else if (status === "canceled") {
    turn.phase = "finished";
    turn.steps = confirmCanceledSteps(turn.steps);
    turn.terminal = { status: "canceled", cleanup: "confirmed", reason: "user" };
  } else if (status === "failed") {
    turn.phase = "interrupted";
    turn.terminal = {
      status: "failed",
      errorCode: (view.run?.reasonCode ?? "internal_error") as Extract<
        NonNullable<AnalysisTurnView["terminal"]>,
        { status: "failed" }
      >["errorCode"],
    };
  } else if (status === "paused" || status === "waiting") turn.phase = "interrupted";
  return turn;
}

export function useDurableConversation(
  api: DocumentSession,
  sessionId: string | null,
  scope: { tenantId: string; workspaceId: string } | undefined,
) {
  const [state, setState] = useState<Conversation | null>(null),
    [error, setError] = useState("");
  const [loading, setLoading] = useState(!!sessionId),
    [revision, refresh] = useState(0);
  const [missingSessionId, setMissingSessionId] = useState<string | null>(null);
  const current = useRef<Conversation | null>(null);
  const scopeQuery = scope ? new URLSearchParams(scope).toString() : "";
  const accept = useCallback((raw: unknown) => {
    current.current = reduceAgentResponse(current.current, raw);
    setState(current.current);
  }, []);
  useEffect(() => {
    current.current = null;
    if (!sessionId || !scopeQuery) return;
    const controller = new AbortController();
    const path = `analysis/sessions/${sessionId}/messages?${scopeQuery}`;
    let subscribed = false;
    const run = async () => {
      await Promise.resolve();
      if (controller.signal.aborted) return;
      setState(null);
      setError("");
      setMissingSessionId(null);
      setLoading(true);
      try {
        accept(
          await api.request(path, "GET", undefined, {
            signal: controller.signal,
            retainOnNotFound: true,
          }),
        );
        setLoading(false);
        while (!controller.signal.aborted) {
          const active = Object.values(current.current!.runs).find((r) => !terminalRun(r));
          if (!active) return;
          const blocked = active.run?.recoveryStatus === "blocked";
          if (blocked && active.run?.status !== "cancelling") return;
          subscribed = true;
          try {
            if (blocked) {
              accept(
                await api.request(path, "GET", undefined, {
                  signal: controller.signal,
                  retainOnNotFound: true,
                }),
              );
              setError("");
            } else {
              await api.streamRequest(
                `analysis/runs/${active.runId}/events?${scopeQuery}&after=${active.cursor}`,
                {},
                {
                  method: "GET",
                  signal: controller.signal,
                  timeoutMs: 70000,
                  retainOnNotFound: true,
                  consume: async (body, signal) => {
                    for await (const response of readAgentResponses(body, signal)) {
                      accept(response);
                      setError("");
                    }
                  },
                },
              );
            }
          } catch (failure) {
            if (controller.signal.aborted) return;
            if (failure instanceof ApiError && [409, 410].includes(failure.status)) {
              accept(
                await api.request(path, "GET", undefined, {
                  signal: controller.signal,
                  retainOnNotFound: true,
                }),
              );
            } else {
              setError("连接已中断，正在重新连接。任务状态会在连接恢复后同步。");
            }
          }
          await new Promise<void>((resolve) => {
            if (controller.signal.aborted) return resolve();
            const finish = () => {
              clearTimeout(timer);
              controller.signal.removeEventListener("abort", finish);
              resolve();
            };
            const timer = setTimeout(finish, 1500);
            controller.signal.addEventListener("abort", finish, { once: true });
          });
        }
      } catch (failure) {
        if (!controller.signal.aborted) {
          if (!current.current && failure instanceof ApiError && failure.status === 404)
            setMissingSessionId(sessionId);
          setError(failure instanceof Error ? failure.message : "会话加载失败。");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void run();
    const remove = api.onClear(() => {
      controller.abort();
      current.current = null;
      setState(null);
    });
    return () => {
      controller.abort();
      remove();
      if (subscribed) api.setDirty(false);
    };
  }, [api, sessionId, scopeQuery, revision, accept]);
  const runs = useMemo(
    () =>
      Object.values(state?.runs ?? {}).sort(
        (a, b) => (a.run?.ordinal ?? 0) - (b.run?.ordinal ?? 0),
      ),
    [state],
  );
  const turns = useMemo(() => runs.map(turnFromRun), [runs]);
  const reload = useCallback(() => refresh((n) => n + 1), []);
  const older = async () => {
    if (!current.current?.nextCursor || !sessionId) return;
    try {
      accept(
        await api.request(
          `analysis/sessions/${sessionId}/messages?${scopeQuery}&cursor=${encodeURIComponent(current.current.nextCursor)}`,
        ),
      );
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "历史记录加载失败。");
    }
  };
  return {
    state,
    runs,
    turns,
    loading,
    error,
    missing: Boolean(sessionId && missingSessionId === sessionId),
    reload,
    older,
    active: runs.find((r) => !terminalRun(r)),
  };
}
