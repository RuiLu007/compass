# 第 17 课原始上下文｜从自主循环到混合编排：构建 Workflow + Agent 执行引擎

摘自课程负责人提供的第五篇调整材料，按课次整理。

**核心问题**　经营分析链路中，确定性流程与自主 Agent 如何交接控制权？

## 上半节｜理论、行业实践与架构选型

### Workflow 与 Agent Loop 的执行差异

- 预定义路径与运行时动态路径。
- 确定性节点与模型决策节点。
- 状态驱动与事件驱动。
- Workflow 与 Agent 的控制权如何交接。

### Workflow 的核心控制结构

- Sequence。
- Conditional Routing。
- Parallel / Join。
- Loop。
- Subworkflow。
- Agent Node。
- Human Decision Node。

本课定义 Human Decision Node 的接口和等待语义。第 19 课实现持久化等待与审批恢复。

### 两种典型混合模式

- Agent-in-Workflow：确定性流程控制整体，局部节点由 Agent 自主完成。
- Workflow-as-Tool：Agent 主导任务，调用确定性 Workflow 完成标准化操作。

讲清两者的控制权归属、状态管理与失败传播。

### Workflow Engine 选型

围绕以下四类方案选型。

- Python 原生代码编排。
- 轻量状态机或 DAG。
- LangGraph 等图执行框架。
- Temporal 等持久工作流系统。

分别评估执行图编排和持久化执行两类能力。

### DataAgent 选型

采用 Workflow Shell + Autonomous Analysis Node。

确定性流程控制经营分析的阶段边界。开放分析调用第 16 课实现的 Agent Executor。

首期使用进程内轻量编排，持久化能力留给第19课。

## 下半节｜DataAgent 实战

实战可实现最小 Workflow Engine，也可复用选定框架。

1. 定义 Workflow、Node、Edge。
2. 实现 Sequence 与 Conditional Router。
3. 实现 Parallel / Join 的基础能力。
4. 将 Agent Executor 封装为 Workflow Node。
5. 实现节点输入输出契约。
6. 建立 Workflow 与 Agent 的错误传播机制。
7. 为人工审批节点预留挂起接口。

### 课堂演示

1. 用户经营分析请求
2. 确定性任务分类与权限预检
3. Autonomous Analysis Agent
4. Plan → Execute → Verify
5. 确定性结果校验与路由
6. 进入只读报告或行动建议待审批分支。

### 本课交付

M3 混合执行引擎、Workflow 控制流、混合架构 ADR。
