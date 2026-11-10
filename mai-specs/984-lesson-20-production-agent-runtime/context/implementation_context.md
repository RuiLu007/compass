# Compass 第 20 课实施上下文｜M6

## 前置能力

第 19 课 M5 的持久化 Run、审批控制、Checkpoint、事件和稳定动作标识，以及第四篇模型 Trace。

## 本课要交付的运行能力

- 将同步 Run 改为异步提交，提供 Submit、Get Status、Stream Events、Cancel/Resume 和审批回调。Session 与 Run 解耦，事件有顺序，客户端按游标断线续流。
- 实现 Queue、Worker、Lease/Heartbeat、可见性超时与重复投递下的幂等接续。原 Worker 失联时从可恢复位置接管，过期执行权不能覆盖接管结果。
- 执行租户并发配额、队列上限、预算与背压，考虑租户公平性、Worker Pool 隔离、资源和成本统计。教学基线为模块化 Runtime + 异步 Worker。负载实验用于判断是否独立拆分 Scheduler、Queue 与扩展池。
- 关联 Run/Step/Tool/Workflow/Model Trace 与 Timeline，区分 Retry / Resume / Replay。注入 Worker Crash、重复投递、超时及取消竞态，检查恢复成功率、排队时延、资源利用率和运行不变量。
- 并发提交多个 GMV 诊断，观察排队、实时进度、Worker 被杀后的接管及取消后阻止后继动作。交付 M6、部署拓扑 ADR、Failure Matrix 与运行恢复验收报告。

## 课次边界

要求异步执行和真实接管演示，部署拆分由实验决定。故障验证在受控环境使用合成任务，覆盖已提交动作的接续和取消边界。

## Compass 接入约束

- apps/worker 已有生命周期和部署脚手架。Agent 仍拥有 Run 及执行状态，Worker 经所有者 API 与受控契约领取或回报，遵守跨 app 源码和数据库边界。
- 复用 M5 的审批、Checkpoint 及动作幂等机制。若对现有 Web 提供入口，BFF 负责用户与租户鉴权。技术设计决定 API 路径与展示方式，现有 Chat 接口维持兼容。

## 跨课契约

任务以 Run 进入，外部能力经受控接口调用，关键状态变化生成结构化事件。课堂复用同一个 Compass 经营分析案例，每课以可观察的正常和异常结果验收本期增量。
