# 985: 第 19 课｜从内存循环到持久运行：构建 Run State、Checkpoint 与 Human-in-the-Loop

| 项         | 值                                                                                                                                                                                |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase      | draft                                                                                                                                                                             |
| Profile    | standard                                                                                                                                                                          |
| 优先级     | P1                                                                                                                                                                                |
| 负责人     | andy                                                                                                                                                                              |
| 目标日期   | 待填                                                                                                                                                                              |
| 涉及子系统 | apps/agent（Session、消息、Agent/LLM Runtime、工具、Workflow、产物、HITL）、agent/main（模型和迁移）、apps/web（会话列表、Message 加载、Run 流式输出和控制）、contracts（Schema） |

## 一句话描述

补齐前序课程全部必要的运行持久化，让首问形成持久 Session、刷新恢复完整会话且与实时输出一致，并在此基座上完整实现 Human in the Loop。

## 本次范围

2026-11-12 课程负责人明确两条主线：

1. 完整保存 Session/Message、Agent 与 LLM Runtime、工具及 Workflow 的运行数据、状态和产物，交付字段、关系、表名规范与迁移。前序案例逐个验证刷新、重启可读和必要恢复。
2. 单独设计 HITL 增量，接入 M4 派发门禁，完成持久等待、批准/拒绝/到期、暂停/恢复/取消和动作结果核对。

会话内容采用两个接口：Message 非流式读取持久投影，Run 流式输出运行增量；两者使用同一 AgentResponse、同一业务包联合与 reducer。执行中刷新先加载 Message，再按返回游标续订原 Run。本课引入本地 Docker Redis Streams 保存短期流，数据库批量保存完整投影，关键变化以 Outbox 保障补发；本课完成单实例执行与连接解耦；M6 承接 Queue、Worker、Lease 和自动接管。

## 课堂演示与验收

先执行第 15–18 课全部案例，完成后刷新，从左侧打开同一 Session，核对问答、执行树、计划、脚本、产物及终态；刷新后追问使用服务端历史。再演示等待审批时重启服务、批准后原 Run 接续，以及动作已生效但回执中断后的核对。

## 有效产物

- [上下文索引](context/_INDEX.md)、[实施上下文](context/implementation_context.md)。
- [PRD](spec/PRD.md)：需求与 AC 唯一维护源，保留 AC-01～AC-08，新增 AC-09～AC-19。
- [持久化技术方案](design/TECH_DESIGN.md)与[HTML 审阅版](design/TECH_DESIGN.html)。
- [完整数据设计](design/DATA_DESIGN.md)、[Redis 与本地部署](design/REDIS_DESIGN.md)、[代码设计](design/CODE_DESIGN.md)。
- [HITL 增量方案](design/HITL_DESIGN.md)与[HTML 审阅版](design/HITL_DESIGN.html)。

本次完成 PRD 与技术设计修订，Phase 保持 draft，待方案评审。迁移、实现与业务验收均待实施；源材料和前序未提交实现保持原样。
