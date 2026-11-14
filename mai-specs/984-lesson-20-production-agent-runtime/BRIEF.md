# 984: 第 20 课｜从单进程到企业级 Runtime：实现异步调度、资源治理与可靠性验证

| 项         | 值                                                                                                                                                               |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase      | draft                                                                                                                                                            |
| Profile    | standard                                                                                                                                                         |
| 优先级     | P1                                                                                                                                                               |
| 负责人     | andy                                                                                                                                                             |
| 目标日期   | 待填                                                                                                                                                             |
| 涉及子系统 | apps/agent（Run API、调度、配额与 Timeline）、apps/worker（异步消费与租约）、apps/web（BFF 运行接口与分析 Chat）、contracts（API 与事件）、deploy（Worker 运行） |

## 一句话描述

接入异步 Run API、Queue/Worker、租户配额与事件流，并验收故障接管、取消竞态和运行追踪。

## 背景

M5 的 Run 能跨进程保留状态。多个租户并行提交长任务时，需要从请求解耦执行，管理资源并在 Worker 失联时接管。

## 关键决策

- 用异步 Run API 和 Queue/Worker 承接 M5 的持久 Run。Agent 拥有权威状态，Worker 持有效 Lease 领取、执行和回报。
- 按可信租户实施并发配额、队列上限和背压。串联排队、节点、工具与模型 Trace，通过事件游标恢复客户端订阅。分析 Chat 接入按权限生成的过程旁白和明确的最终回答阶段。
- 实战以模块化 Runtime 和异步 Worker 起步。独立 Scheduler、队列服务及 Worker 池按负载、隔离与运维实验决定。

## 课堂演示与验收

- 并发提交不同租户的任务。保存 Checkpoint 后终止实际 Worker，观察新进程接续原 Run，再演示重复投递、取消竞态和断线续流。
- 验收过期 Lease 回报被拒绝、已确认动作效果不重复、其他有容量租户仍可运行，并交付 Failure Matrix 与恢复验收报告。

## 上下文索引

- [素材索引](context/_INDEX.md)
- [产品需求文档](spec/PRD.md)（需求与验收标准的有效维护入口，standard 草稿）
- [技术方案](design/TECH_DESIGN.md)、[代码设计](design/CODE_DESIGN.md)与[HTML 审阅版](design/TECH_DESIGN.html)承接有效 PRD；2026-11-12 已完成 MVP 评审和必要修订，结论为有条件通过，实施依赖 M5 交付。
- [设计评审](review/design_review.md)记录问题、修正和前置条件。本轮按用户要求止于设计，Phase 保持 draft，未进入代码实现与业务验收。

## 2026-11-12 前置契约更新

第 19 课现已将 Session/消息/运行各层持久化、单实例执行与连接解耦、Message 非流式加载、Run 流式输出及共享响应协议、本地 Docker Redis Streams 与数据库投影纳入 M5。M6 复用这些基线，聚焦 Queue、Worker、Lease、资源治理与自动接管。有效前置合同见[第 19 课技术方案](../985-lesson-19-durable-runtime-hitl/design/TECH_DESIGN.md)。本次已对齐 AgentResponse v2、Redis Run 游标、Message 完整投影、状态包及 api/runtime 模块路径；统一 Session 起始锁序，补齐所有接续入口的排队和租约回收。当前源码迁移到 0008，M5 的 0009 与运行持久化仍待实现；M6 的 T0 先核验这些前置交付。
