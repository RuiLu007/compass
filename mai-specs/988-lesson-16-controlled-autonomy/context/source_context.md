# 第 16 课原始上下文｜从 ReAct 到 Plan-and-Execute：建立受控自主执行机制

摘自课程负责人提供的第五篇调整材料，按课次整理。

**核心问题**　如何让 Agent 完成复杂任务，并把自主执行控制在可验证、可停止的范围内？

## 上半节｜理论、行业实践与架构选型

### 三类自主执行模式

- ReAct：每一步根据观察结果决定下一步。
- Plan-and-Execute：先形成计划，再逐步执行。
- Plan–Execute–Verify–Replan：加入验证与动态修正。

比较任务复杂度、规划成本、环境变化、验证难度及适用场景。

### Planning 机制

- 计划数据结构与步骤依赖。
- 计划与实际执行状态分离。
- 静态计划与动态重规划。
- 计划失败、步骤跳过和计划失效。
- 如何避免过度规划。

### Verifier 机制

- 模型自我检查与独立验证的区别。
- 规则验证、Schema 验证、工具结果验证。
- Step-level 与 Run-level 验证。
- 验证失败后的 Retry、Replan、Fail。
- 如何避免验证循环无限嵌套。

### 自主性预算与终止机制

- Max Steps、Max Tool Calls、Token Budget、Cost Budget。
- Deadline 与执行超时。
- 连续失败次数限制。
- 无进展检测与重复调用检测。
- 成功、失败、预算耗尽、需要人工介入等终态。

### DataAgent 选型

ReAct 作为基础执行模式，复杂经营诊断启用轻量 Plan-and-Execute，关键结论加入独立 Verifier。

确定性代码执行预算与终止规则，硬约束优先于模型决策。

## 下半节｜DataAgent 实战

实战在 M1 基础上实现以下能力。

1. `Planner` 和结构化 `ExecutionPlan`。
2. `PlanExecutor` 与步骤推进。
3. `Verifier` 接口和规则验证器。
4. `Replanner`。
5. `AutonomyBudget`。
6. `StopPolicy`。
7. 执行策略切换：ReAct / Plan-and-Execute。

### 课堂演示

Agent 先规划「指标确认 → 分解 GMV → 检查流量与转化 → 排查库存 → 验证假设 → 输出结论」。

人为制造一个数据异常或缺失，观察 Agent 如何验证失败、修正计划、重新查询，并在预算耗尽时停止。

### 本课交付

M2 受控自主分析 Agent、执行策略选型矩阵、预算与终止策略。
