# 第 20 课原始上下文｜从单进程到企业级 Runtime：实现异步调度、资源治理与可靠性验证

摘自课程负责人提供的第五篇调整材料，按课次整理。

**核心问题**　多个租户同时运行长任务时，如何调度、限制资源并接管失联 Worker 的任务？

## 上半节｜理论、行业实践与架构选型

### 三种部署形态

- 同步单进程。
- API + 异步 Worker。
- Scheduler + Queue + Worker Pool。

比较吞吐、任务时长、恢复目标、隔离要求、运维复杂度和成本。

### Agent API 与异步执行协议

- Submit Run。
- Get Run Status。
- Stream Run Events。
- Cancel / Resume Run。
- 人工审批回调。
- Run 与 Session 解耦。
- 事件序号与断线重连。

### 调度与任务接管

- Scheduler。
- Queue。
- Worker。
- Lease。
- Heartbeat。
- Visibility Timeout。
- 重复投递与幂等恢复。
- Worker Crash Recovery。

### 多租户与资源治理

- 并发配额。
- Queue Limit。
- Budget Enforcement。
- Tenant Fairness。
- Backpressure。
- Worker Pool 隔离。
- 资源与成本统计。

### 可观测与故障验证

- Run/Step/Tool/Model Trace 关联。
- Event Timeline。
- 故障分类。
- 故障注入。
- Retry / Resume / Replay 区分。
- Runtime Invariant。
- 恢复成功率、排队时延和资源利用率。

### DataAgent 部署选型

先完成模块化 Runtime + 异步 Worker，根据课堂负载实验决定是否将 Scheduler、Queue 独立部署并扩展到多个 Worker。

课堂对比分布式拓扑，由负载、恢复目标和隔离需求决定部署形态。

## 下半节｜DataAgent 实战

1. 将同步 Run 改造为异步提交。
2. 实现 Queue 与 Worker。
3. 实现 Lease / Heartbeat。
4. 实现多租户并发限制。
5. 实现 Run Event Streaming。
6. 关联模型、工具、Workflow 和 Run Trace。
7. 注入 Worker Crash、重复投递、超时和取消竞态。
8. 完成 Runtime 验收。

### 课堂演示

同时提交多个经营分析任务。

观察任务排队和实时进度。终止一个 Worker，检查任务接管。取消某个 Run，确认后续业务动作停止派发。最后用 Run Timeline 解释故障与恢复过程。

### 本课交付

M6 企业级 Agent Runtime、部署拓扑 ADR、Failure Matrix、运行与恢复验收报告。
