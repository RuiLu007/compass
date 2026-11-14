# 第 20 课实施记录

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
