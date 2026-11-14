"use client";

import { useState } from "react";
import type { ExecutionNode } from "@/contracts/execution-node";
import { UiButton } from "./button";
import { executionEntries, UiExecutionTimeline } from "./execution-timeline";

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

function executionExample(status: ExecutionNode["status"]): ExecutionNode[] {
  const node = (
    n: number,
    kind: ExecutionNode["kind"],
    title: string,
    parent: number | null,
    fields: Partial<ExecutionNode> = {},
  ): ExecutionNode => ({
    id: id(n),
    parentId: parent === null ? null : id(parent),
    revision: 1,
    kind,
    title,
    status: "completed",
    startedAtMs: 1000,
    endedAtMs: 2400,
    dependsOn: [],
    display: { policy: "summary", language: "text" },
    ...fields,
  });
  const state = { status, endedAtMs: status === "running" || status === "preparing" ? null : 5200 };
  return [
    node(1, "workflow", "渠道异动归因", null, {
      ...state,
      workflow: { mode: "reused", assetId: id(100), name: "渠道异动归因", version: 1 },
      executor: { kind: "quickjs", runtime: "JavaScript" },
      summary: "复用已保存的流程，按本次日期重新分析。",
    }),
    node(2, "narration", "已确认分析范围", 1, {
      summary: "先比较渠道，再并行分析商品贡献与核对口径。",
    }),
    node(3, "skill", "加载经营复盘方法", 1, {
      skill: { name: "weekly-retail-review", version: "1", stage: "load" },
      summary: "已加载周期选择与核验约束。",
    }),
    node(4, "group", "商品贡献与口径复核", 1, {
      ...state,
      scheduling: "parallel",
      dependsOn: [id(2)],
    }),
    node(5, "subagent", "商品贡献分析", 4, {
      subagent: { name: "product_breakdown", childRunId: id(105) },
      summary: "已取得前三项商品贡献。",
    }),
    node(6, "model", "分析商品贡献", 5, {
      detail: {
        thinking:
          "先比较各渠道的实付金额变化，再看变化最大的渠道。商品明细按实付差值排序，最后核对汇总金额与渠道结果是否一致。",
      },
    }),
    node(7, "tool", "查询商品明细", 6, {
      origin: {
        kind: "mcp",
        toolName: "查询商品明细",
        serverId: "retail",
        serverName: "Retail 数据服务",
      },
      executor: { kind: "service" },
      summary: "协议示例：返回 3 条记录。",
      display: {
        policy: "preview",
        language: "json",
        inputPreview: '{"channel":"POS","limit":3}',
        outputPreview:
          '{"rowCount":3,"scope":"当前工作区","note":"这里使用组件演示数据，MCP 的真实工具接入在第 18 课完成。"}',
      },
    }),
    node(8, "subagent", "独立口径复核", 4, {
      ...state,
      subagent: { name: "scope_review", childRunId: id(108) },
      summary:
        status === "failed" ? "复核未完成，可检查数据范围后重试。" : "核对日期范围与金额变化方向。",
    }),
    node(9, "skill", "运行金额复核脚本", 8, {
      ...state,
      skill: { name: "retail-verification", version: "1", stage: "script" },
    }),
    node(10, "tool", "执行 Python 复核", 9, {
      ...state,
      origin: { kind: "builtin", toolName: "python_analysis" },
      executor: { kind: "sandbox", runtime: "Python", environmentId: "guide-example" },
      summary: "协议示例：Sandbox 执行环境由第 18 课接入。",
    }),
  ];
}

export function UiExecutionExample() {
  const [status, setStatus] = useState<ExecutionNode["status"]>("running");
  const variants = [
    ["running", "进行中"],
    ["completed", "成功"],
    ["failed", "失败"],
    ["waiting", "待处理"],
    ["canceled", "取消"],
    ["incomplete", "中断"],
  ] as const;
  return (
    <div className="ui-guide-example">
      <p className="ui-panel-description">
        以下为协议与组件示例。MCP 和 Sandbox 使用演示数据，真实工具执行体系在第 18 课接入。
      </p>
      <div className="ui-actions" role="group" aria-label="切换执行状态">
        {variants.map(([value, label]) => (
          <UiButton
            key={value}
            variant={status === value ? "primary" : "secondary"}
            aria-pressed={status === value}
            onClick={() => setStatus(value)}
          >
            {label}
          </UiButton>
        ))}
      </div>
      <UiExecutionTimeline
        entries={executionEntries(executionExample(status), status === "running")}
        label={
          {
            running: "正在分析",
            completed: "已完成",
            failed: "执行失败",
            waiting: "等待确认",
            canceled: "已终止",
            incomplete: "结果待确认",
            preparing: "准备中",
            empty: "无数据",
          }[status]
        }
        running={status === "running"}
        completed={status === "completed"}
        durationMs={status === "completed" ? 138000 : undefined}
      />
    </div>
  );
}
