# Compass 第 19 课实施上下文｜M5

## 前置能力

第 17 课 M3 的人工等待语义，以及第 18 课 M4 的 Tool Execution Event、Artifact 和 Action Adapter。

## 本课要交付的运行能力

- 统一 Session → Run → Task/Step → Attempt → Action/Event 的标识和关系，关联 Checkpoint、Artifact。实现 Run State Store、Event Store、Checkpoint Store，采用 Step 边界的轻量持久化。
- 状态机覆盖 Created、Running、Waiting、Paused、Cancelling、Completed、Failed、Cancelled。约束合法转换、终态不可逆、事件去重和并发版本冲突。
- 实现 Pause、Interrupt/Cancel、Resume 和人工审批。等待释放执行资源，审批结果关联原 Run、等待点与待决提案。覆盖外部事件、拒绝、超时、过期与重复回调。
- 从 Checkpoint 继续未完成的步骤，保留稳定动作标识及可核对的执行结果。演示 GMV 诊断后等待广告预算建议审批，重启后原 Run 接续，已完成分析与已提交动作可核对且不重复。交付 M5、状态机、Checkpoint/Resume、HITL 与最小 Run Timeline。

## 课次边界

课堂动作使用受控演示适配器和合成数据。M5 聚焦持久运行，Queue、Worker 接管和客户端断线续流在 M6。长期记忆在第八篇。

## Compass 接入约束

- 运行数据由 Agent 所有，按 docs/database-migrations.md 管理 agent/main 的迁移及保留已有数据。审批入口沿用 Compass 的可信主体与租户边界。
- 跨进程恢复须经真实服务重启验收，核对原 Run、Checkpoint、事件与动作效果。审批批准及动作结果核对共同约束副作用。

## 跨课契约

任务以 Run 进入，外部能力经受控接口调用，关键状态变化生成结构化事件。课堂复用同一个 Compass 经营分析案例，每课以可观察的正常和异常结果验收本期增量。
