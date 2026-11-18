# 第 20 课实施记录

## 双角色版本 · 2026-11-16

按 [PRD](spec/PRD.md) 的 AC-01～AC-21、[技术方案](design/TECH_DESIGN.md)和[评审 R5～R18](review/design_review.md)实施。

| 范围             | 交付                                                                                                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Agent 受理和执行 | 即时与定时共用原子受理和公平队列。Agent 副本以每 Run 的租约和 epoch 领取、处理共享暂停与取消意图，并从持久 Checkpoint 接续。集群容量与预算策略通过指纹校验，本地消费槽可以独立配置。  |
| 事件和诊断       | SQL 保留 Journal、Outbox 和 Message 投影。Redis 按发布世代和包身份批量去重；活跃流失效时客户端可重载已确认历史。Timeline 关联计划、触发、Run 和模型调用。                             |
| 定时交付         | 同一镜像运行单实例 Scheduler。按 IANA 时区计算五段 Cron，漏跑合并，已有未结束触发或 Run 时跳过。到期通过具名触发的内部 HTTP 受理；按共享配额退避，丢回执时核对原 Run。                |
| Web 与 Chat      | 工作区提供列表、表单、触发预览、执行记录和原会话链接。Chat 读写共用计划服务，普通所有者可确认变更。计划事务同步提交 Action、Attempt、Checkpoint 和 Outbox，审批后的当前权限再次校验。 |
| 发布和数据       | Compose 默认两个 Agent 容器、一个入口网关、一个独立 Scheduler；根启动脚本运行两个 Agent 进程及独立 Scheduler。Workspace、构建、部署、CI 和公开契约移除独立 Worker。                   |

数据库 owner 为 `agent/main`，区间 `0014 → 0015`，唯一迁移源为 `apps/agent/migrations/versions/0015_scheduled_tasks.py`。完整与增量 SQL 在 `apps/agent/db/main/sql/` 由既有命令生成。Scheduler 专用数据库账号只可写触发和自己的租约，查询 Run 采用受限摘要。先停旧角色并封存写凭据，备份主库、Redis 和 Runtime 密钥，再供应账号和迁移；先验证新 Agent 的即时受理和恢复，再启动 Scheduler。回到 0014 使用升级前的备份，保留升级后的数据用于核对受理和外部效果。见[部署与恢复说明](../../docs/lesson-20-production-runtime.md#发布与恢复)。

本地运行采用可控模型和合成业务样本。新拓扑的验证范围和结果见[验收记录](test/ACCEPTANCE.md)及[故障矩阵](test/FAILURE_MATRIX.md)。

## 历史记录 · 双 Worker 基线

2026-11-12 · Profile `standard`。按 [PRD](spec/PRD.md) AC-01～AC-11、[主方案](design/TECH_DESIGN.md)、[代码设计](design/CODE_DESIGN.md)与[评审](review/design_review.md)实施。保留第 15～19 课已有未提交实现；工作区文件摘要保存在忽略目录 `work/lesson-20/`。

| 任务 | 范围 | 当前状态 |
| --- | --- | --- |
| T0 | M5 0009、持久协议、恢复、HITL 与实际执行接缝 | 已核对；复用现有 RuntimeStore / ExecutionJournal / OutboxPublisher。单次推进将沿根调用完成的 Checkpoint 边界交还 Worker。 |
| T1 | 0010、原子 job、容量与公平准入 | 已完成；调度30项与迁移22项通过，历史52份迁移摘要保持 |
| T2 | Worker HTTP 消费、租约、重复推进、回收接管 | 已完成；Worker27项、三次SIGKILL接管、Agent强杀恢复及在途SIGTERM通过 |
| T3 | 安全事件、运行诊断/用量、Chat 状态 | 已完成；权限/协议26项、浏览器12项、实时撤权/观测故障5项通过 |
| T4 | 单测、真库、双进程故障、浏览器、构建和容器 | 已完成；573项应用单测、130项Agent集成、22项Web集成、12项浏览器用例通过；check/build及五应用容器与双Worker消费通过 |

M5 的设计与本课评审中“0009 尚未交付”是设计时背景；当前源码与第 19 课验收已具备前置能力。M6 新增迁移 owner 为 `agent/main`，区间 `0009 → 0010`，唯一真源为 `apps/agent/migrations/versions/0010_run_scheduler.py`；完整与增量 SQL 由现有数据库入口生成。Worker 经所有者 HTTP API 运行，不持有业务数据库凭据。

本次保持 BRIEF Phase 与 ROADMAP 原值。本阶段实现与测试已完成，完整结果见[验收记录](test/ACCEPTANCE.md)及[故障矩阵](test/FAILURE_MATRIX.md)。
