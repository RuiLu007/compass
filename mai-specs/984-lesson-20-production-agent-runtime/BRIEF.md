# 984: 第 20 课｜从单进程到企业级 Runtime：实现异步调度、资源治理与可靠性验证

| 项         | 值                                                                |
| ---------- | ----------------------------------------------------------------- |
| Phase      | draft                                                             |
| Profile    | standard                                                          |
| 优先级     | P1                                                                |
| 负责人     | andy                                                              |
| 目标日期   | 待填                                                              |
| 涉及子系统 | Agent scheduler、Run 消费与追踪，Web/BFF、契约，Worker 迁移与部署 |

## 一句话描述

让用户通过 Web「定时任务」页面、自然语言对话或授权 API 管理定时任务，由独立运行的 Scheduler 可靠地触发 Run、跟踪交付和运行状态。

## 本课目标

- 从单进程执行所有者演进为可水平扩展的多副本 Agent，按 Run 分配执行权，支持跨实例恢复和续流。
- 通过 Web 和自然语言管理定时任务，由独立 Scheduler 角色可靠交付到统一 Run 队列，并可追踪结果。

## 关键决策

- 同一个 `apps/agent` 和镜像部署两个角色。Scheduler 单实例负责到期触发、HTTP 交付、重试和状态同步，Agent 多副本接单及执行。计划、触发和 Run 同属 `agent/main`。
- Agent 对即时与定时 Run 统一做授权、准入、配额和公平领取。多个健康副本可并行执行，每条 Run 同时只有一个有效租约。实例失联后由其他健康副本接续。
- 现有 Worker 的消费循环迁回 Agent，保留共享队列、租户配额、Lease、Checkpoint 和恢复。迁移后移除独立 Worker App。
- 定时触发与 API 即时提交共用 Run 受理、执行和权限。通过触发历史和 Timeline 查到运行结果。

## 课堂演示与验收

在 Web 列表和自然语言对话里各自完成一轮创建、查询、修改和删除，交叉检查两边查看的是同一计划。到期时注入背压，丢弃受理响应并重启 Scheduler，核对原触发只关联一个 Run，Agent 期间继续受理即时任务。终止执行其中一条 Run 的 Agent 副本，观察其他健康副本从 Checkpoint 接续，再通过触发历史追到执行结果。

## 上下文索引

- [素材索引](context/_INDEX.md)
- [产品需求](spec/PRD.md)是需求与验收的有效入口。
- [技术方案与架构图](design/TECH_DESIGN.md)、[代码设计](design/CODE_DESIGN.md)、[HTML 阅读版](design/TECH_DESIGN.html)与[16:9 绿色选型图](design/async-architecture-choices.svg)记录部署与职责。
- [评审与修订映射](review/design_review.md)记录 R5～R18 的设计落点。[实施记录](IMPLEMENTATION.md)、[业务测试计划](test/TEST_PLAN.md)及[验收](test/ACCEPTANCE.md)记录双角色版本的交付范围、执行结果和原双 Worker 基线。
