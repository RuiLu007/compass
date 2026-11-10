# 第 18 课原始上下文｜从 Tool Calling 到受控执行：构建 Tool Executor 与 Sandbox

摘自课程负责人提供的第五篇调整材料，按课次整理。

**核心问题**　如何把模型生成的 Tool Call 转成经过校验、隔离和资源约束的执行？

## 上半节｜理论、行业实践与架构选型

### Tool Calling 与 Tool Execution 的边界

- 模型生成 Tool Call。
- Runtime 校验与分发。
- Executor 执行。
- 结果标准化。
- Observation 返回 Agent。

### Tool Executor 架构

- Tool Registry 的最小实现。
- Tool Dispatcher。
- 参数校验。
- 执行超时。
- 并行工具执行。
- 错误分类。
- Cancellation。
- 结果大小与输出限制。

### Sandbox 的必要性与技术选型

围绕以下四类环境选型。

- 进程内函数。
- 子进程。
- 容器隔离。
- 远程 Sandbox 服务。

分析隔离强度、启动时延、资源成本、环境复用和运维复杂度。

为 Python 代码执行、SQL 查询和业务 API 操作分别制定执行安全策略。

### Sandbox 核心机制

- CPU、内存、时间限制。
- 文件系统隔离。
- 网络访问策略。
- 临时文件与 Artifact。
- 执行环境生命周期。
- 终止与资源清理。

### DataAgent 选型

- 普通可信只读函数：进程内 Tool Executor。
- SQL：只读查询接口与权限校验。
- Python：独立容器或远程 Sandbox。
- 有副作用动作：本篇只建立 Action Adapter 和审批挂钩，完整治理留给第七篇。

## 下半节｜DataAgent 实战

1. 实现统一 Tool Dispatcher。
2. 建立 Tool Execution Result。
3. 加入参数校验、超时、取消和错误语义。
4. 接入 SQL 查询工具。
5. 接入隔离的 Python Sandbox。
6. 建立 Artifact 输出机制。
7. 记录 Tool Execution Event。

### 课堂演示

Agent 查询华东区经营数据后，生成 Python 分析代码，在 Sandbox 中计算指标变化、绘制图表，并将结果返回分析流程。

同时演示超时、代码异常、资源限制与执行取消。

### 本课交付

M4 Tool Execution Runtime、Sandbox Adapter、执行环境选型 ADR。
