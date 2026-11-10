# 986: 第 18 课｜从 Tool Calling 到受控执行：构建 Tool Executor 与 Sandbox

| 项         | 值                                                                                     |
| ---------- | -------------------------------------------------------------------------------------- |
| Phase      | draft                                                                                  |
| Profile    | standard                                                                               |
| 优先级     | P1                                                                                     |
| 负责人     | andy                                                                                   |
| 目标日期   | 待填                                                                                   |
| 涉及子系统 | apps/agent（工具执行、SQL Adapter、Sandbox Adapter、Artifact）、deploy（隔离执行环境） |

## 一句话描述

通过统一 Tool Executor 执行受限 SQL 和隔离 Python，规范结果、Artifact、超时、取消与错误。

## 背景

以前序 M1 的真实 Retail 查询和 M3 的混合控制流为基础。M4 为真实工具补齐统一授权、分发、隔离与执行结果约束。

## 关键决策

- 把 M1 的真实 Retail 查询纳入最小 Tool Registry 和统一 Dispatcher。执行层校验可信授权、参数与执行类别，再分发到相应后端。
- 可信只读函数在进程内执行。SQL 沿用受限零售身份，Python 接入真实隔离后端。统一处理有界并行、限额、超时、取消、资源回收、Artifact 与执行事件。
- 有副作用的请求经 Action Adapter 返回待审批提案。持久审批在 M5，动态工具供给、Skill/MCP 与完整 Action 治理在后续篇章。

## 课堂演示与验收

- 复用 M1 查询样本核对接入统一执行层前后的结果。同一 Run 查询授权 Retail 数据，再在隔离 Python 中计算、绘图并取回 Artifact。
- 验收权限拒绝、并发上限、超时与取消。核对后端实际终止和资源清理，观察结果按原调用 ID 回传。

## 上下文索引

- [素材索引](context/_INDEX.md)
- [产品需求文档](spec/PRD.md)是本课功能规格与验收标准的维护入口。
