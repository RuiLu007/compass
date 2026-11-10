# Compass 第 15 课实施上下文｜M1

## 前置能力

第四篇已交付的 LLM Runtime。复用可靠执行与模型 Trace，统一契约已支持消息、工具定义、工具调用结果和停止原因。

## 本课要交付的运行能力

- 以一个经营分析任务建立 Agent、Run、Step、Message、ToolCall、ToolResult、AgentState、AgentExecutor 及最小 Context Provider。Context 组装任务、历史和工具结果。
- 通过 AgentExecutor.run() 同步推进 Model → Action → Observation → Model，复用统一模型调用契约。多轮和同轮多个 ToolCall 按调用标识回填结果。本课讲解并行执行，统一的有界并行与取消机制在 M4 落地。
- 接入 Mock 指标查询与经营数据查询工具。区分最终回答、可回传的工具错误和执行异常，按 Run/Step 记录基本结构化事件。
- 实战走通「华东区 GMV 为什么下降？」。多轮查询 GMV、订单数、转化率后给出初步分析，同时可演示工具失败、模型失败与退出。交付 M1、执行架构图、执行内核选型 ADR。

## 课次边界

进程内、单 Agent、同步执行与内存状态。策略预算在 M2，工作流在 M3，隔离执行在 M4，持久化和调度在 M5–M6。

## Compass 接入约束

- 第四篇模型层入口位于 apps/agent/src/compass_agent/llm/，沿用已有调用契约及 Trace。原有 Chat / 模型管理行为按当前工程基线继续工作。
- M1 以 Agent 应用内可运行演示验收，工程演示入口由技术设计决定。Mock 工具使用合成数据。

## 跨课契约

任务以 Run 进入，外部能力经受控接口调用，关键状态变化生成结构化事件。课堂复用同一个 Compass 经营分析案例，每课以可观察的正常和异常结果验收本期增量。
