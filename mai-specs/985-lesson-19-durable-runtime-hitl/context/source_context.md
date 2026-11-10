# 第 19 课原始上下文｜从内存循环到持久运行：构建 Run State、Checkpoint 与 Human-in-the-Loop

摘自课程负责人提供的第五篇调整材料，按课次整理。

**核心问题**　如何让 Agent 的执行过程在 Python 进程重启后继续？

## 上半节｜理论、行业实践与架构选型

### 统一执行领域模型

统一执行层级如下。

`Session → Run → Task / Step → Attempt → Action / Event`

并明确 Checkpoint、Artifact 与各对象的关系。

### Run State Machine

覆盖八种运行状态。

- Created。
- Running。
- Waiting。
- Paused。
- Cancelling。
- Completed。
- Failed。
- Cancelled。

定义合法状态转换、终态不可逆、并发更新冲突和版本控制。

### Event 与 Checkpoint

- Event Log 与 State Snapshot。
- Checkpoint 生成时机。
- 状态恢复粒度。
- 事件去重。
- Checkpoint 与外部工具副作用的协调。
- Resume 与 Retry 的区别。

### Interrupt 与 Human-in-the-Loop

- Pause、Cancel、Resume 的区别。
- 人工审批与外部事件等待。
- 审批结果如何关联原 Run。
- 等待期间如何释放执行资源。
- 恢复后如何避免重复执行。
- 人工拒绝、超时与审批过期如何处理。

### 持久执行方案选型

围绕以下三类方案选型。

- 关系数据库 + 自研状态机。
- Agent 框架内置 Checkpointer。
- Durable Workflow Engine。

DataAgent 采用支持 Step 边界持久化的轻量方案。出现复杂跨服务持久编排需求时，再评估持久工作流引擎。

## 下半节｜DataAgent 实战

1. 建立 Run/Step/Attempt 数据模型。
2. 实现 Run State Store。
3. 实现 Event Store 与 Checkpoint Store。
4. 实现状态持久化与 Resume。
5. 实现 Interrupt/Cancel 控制接口。
6. 实现 Human Approval 等待与恢复。
7. 建立最小 Run Timeline。

### 课堂演示

Agent 完成 GMV 诊断后，提出调整广告预算的建议，Workflow 进入审批等待。

系统释放执行资源。用户稍后批准，任务从保存的状态继续执行。

随后人为重启服务，验证任务不会从头重复分析，也不会重复执行已提交动作。

跨进程恢复依赖持久状态、稳定的动作标识和执行结果记录。从已保存的执行位置接续任务。

### 本课交付

M5 Durable Agent Runtime、Run State Machine、Checkpoint/Resume、HITL。
