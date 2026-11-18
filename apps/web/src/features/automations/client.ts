import type { AnalysisRunScope } from "@/client/analysis-run";
import type { components } from "@/generated/api";

type Schemas = components["schemas"];
export type Plan = Schemas["ScheduleView"];
export type Plans = Schemas["SchedulePage"];
export type Trigger = Schemas["TriggerView"];
export type Triggers = Schemas["TriggerPage"];
export type PlanReceipt = Schemas["ScheduleWriteReceipt"];
export type Preview = Schemas["SchedulePreviewResult"];

export function schedulePath(
  scope: AnalysisRunScope,
  suffix = "",
  filters: Record<string, string> = {},
) {
  return `analysis/schedules${suffix}?${new URLSearchParams({ ...scope, ...filters })}`;
}

export const statusLabel: Record<Plan["status"], string> = {
  enabled: "已启用",
  disabled: "已停用",
  deleted: "已删除",
};

export const deliveryLabel: Record<Trigger["deliveryStatus"], string> = {
  pending: "等待交付",
  dispatching: "交付中",
  retry_wait: "等待重试",
  reconcile_pending: "正在核对接单回执",
  submitted: "Agent 已接单",
  delivery_failed: "交付失败",
  skipped: "已跳过",
};

export const runLabel = {
  created: "排队中",
  running: "执行中",
  waiting: "等待人工确认",
  paused: "已暂停",
  cancelling: "取消中",
  completed: "已完成",
  failed: "执行失败",
  canceled: "已取消",
};

const reasons: Record<string, string> = {
  schedule_overlap: "上一轮交付或运行尚未结束。可在原会话处理待确认、暂停或取消。",
  schedule_changed: "交付前计划已更新、停用或删除。",
  runtime_cluster_backpressure: "集群积压已满。调度器将按下次尝试时间继续交付。",
  runtime_tenant_backpressure: "租户积压已满。调度器将按下次尝试时间继续交付。",
  runtime_backpressure: "Agent 积压已满。调度器将按下次尝试时间继续交付。",
  schedule_permission_revoked: "所有者的定时任务或分析权限已失效，请核对工作区权限。",
  schedule_delivery_rejected: "Agent 拒绝接单，请检查计划配置。",
  schedule_delivery_unavailable: "Agent 暂时无法接单。",
  schedule_delivery_unreachable: "暂未收到 Agent 响应，调度器将核对原触发。",
  delivery_expired: "本次触发已超过交付期限。",
  delivery_interrupted: "交付连接中断，调度器将核对原触发。",
  model_rate_limited: "上游模型限流，可在原会话查看详情或更换模型后重试。",
  runtime_overloaded: "模型容量暂时不足，请稍后在原会话重试。",
  model_unavailable: "计划所选模型已不可用，请更新计划的模型。",
  route_binding_missing: "自动选模缺少路由配置，请联系管理员或选择固定模型。",
  run_budget_exhausted: "运行预算已耗尽，请在原会话核对分析范围。",
  budget_exhausted: "运行预算已耗尽，请在原会话核对分析范围。",
  budget_policy_mismatch: "缺少本次运行的预算配置，请联系管理员。",
  tool_not_authorized: "当前工具权限不足，请核对数据或工具配置。",
  tool_unavailable: "工具暂不可用，请打开原会话查看详情。",
  workflow_unavailable: "当前工作流执行入口不可用，请检查计划输入。",
  execution_unknown: "在途结果待核对，请打开原会话。",
  cleanup_unconfirmed: "执行资源待核对，请打开原会话。",
  run_execution_issue: "请打开原会话查看运行详情。",
};

export function triggerReason(trigger: Trigger) {
  const code = trigger.runReasonCode ?? trigger.reasonCode;
  if (!code) return null;
  if (
    trigger.deliveryStatus === "delivery_failed" &&
    [
      "runtime_backpressure",
      "runtime_cluster_backpressure",
      "runtime_tenant_backpressure",
    ].includes(code)
  )
    return "积压持续超过容量，本次触发的交付窗口已关闭。";
  return reasons[code] ?? null;
}

export function planTime(value?: string | null, timeZone?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).format(new Date(value));
}

export function describeRule(cron: string) {
  const [minute, hour, day, month, week] = cron.split(" ");
  if (!/^\d+$/.test(hour) || !/^\d+$/.test(minute) || day !== "*" || month !== "*") return cron;
  const time = `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  if (week === "*") return `每天 ${time}`;
  if (/^[0-7]$/.test(week)) return `每周${"日一二三四五六日"[Number(week)]} ${time}`;
  return cron;
}
