# 第 15 课原始上下文｜从 LLM Runtime 到 Agent Runtime：搭建 Agent 执行内核

摘自课程负责人提供的第五篇调整材料，按课次整理。

**核心问题**　已有 LLM Runtime 时，还需要哪些运行机制才能完成一次 Agent Run？

## 上半节｜理论、行业实践与架构选型

### Agent Runtime 的定义与职责

- LLM Runtime 与 Agent Runtime 的本质区别。
- 一次 LLM Call、一次 Agent Step、一次 Agent Run 的区别。
- Agent Runtime 的核心职责：决策驱动、工具执行、状态维护、生命周期控制。
- Agent Runtime 与 Harness、Workflow Engine、业务服务的边界。

### Agent Runtime 的基本执行机制

- Model → Action → Observation → Model 的闭环。
- ReAct 与 Tool Calling 的运行机制。
- 多轮工具调用如何实现。
- 多工具调用与并行调用如何处理。
- 工具错误如何反馈给 Agent。
- 模型生成最终结果与继续调用工具的判定。

### Agent Runtime 的核心抽象

首课的最小对象集合包括

- `Agent`：定义角色、模型与能力配置。
- `Run`：一次任务执行实例。
- `Step`：一次执行推进。
- `Message`：模型交互消息。
- `ToolCall` / `ToolResult`：工具调用与观察结果。
- `AgentState`：运行中的工作状态。
- `AgentExecutor`：执行控制循环。

此时只定义接口和内存态实现，完整持久化放在第19课。

### 主流框架及技术选型

围绕以下三条路线选型。

- 自己实现轻量 Agent Loop。
- 复用 Agent SDK 的执行内核。
- 采用具备图编排能力的 Agent Framework。

可选案例包括 OpenAI Agents SDK、PydanticAI、LangGraph。比较执行抽象、状态所有权、扩展接口、调试成本与技术锁定风险。

### DataAgent 的选型结论

采用进程内轻量 Agent Executor、第四篇统一 LLM Runtime 与可插拔 Tool Dispatcher。

首期采用单 Agent、同步执行和内存状态。Queue、持久执行与部署演进由后续课次实现。

## 下半节｜DataAgent 实战

实战复用已有 LLM Runtime。

1. 定义 Agent、Run、Step、ToolCall 的基础数据结构。
2. 实现 `AgentExecutor.run()`。
3. 实现 ReAct 循环和多轮 Tool Calling。
4. 接入 Mock 指标查询、经营数据查询工具。
5. 处理模型最终回答、工具失败、异常退出。
6. 记录最基本的 Run/Step 执行日志。

### 课堂演示

用户输入「华东区 GMV 为什么下降？」

Agent 自动查询 GMV、订单数、转化率等指标，完成多轮工具调用并输出初步分析结果。

### 本课交付

M1 最小 Agent Runtime、基础执行架构图、执行内核选型 ADR。
