# 986: 第 18 课｜从 Tool Calling 到受控执行：构建 Tool Executor、MCP 与 Sandbox

| 项         | 值                                                                                     |
| ---------- | -------------------------------------------------------------------------------------- |
| Phase      | draft                                                                                  |
| Profile    | standard                                                                               |
| 优先级     | P1                                                                                     |
| 负责人     | andy                                                                                   |
| 目标日期   | 待填                                                                                   |
| 涉及子系统 | apps/agent（工具执行、内置工具、MCP、SandboxClient、Artifact）、deploy（隔离执行环境） |

## 一句话描述

统一执行内置工具、隔离 Python 与用户 MCP 工具；每次任务装载专属工具列表，并按策略串行或并行执行。

## 背景

以前序 M1 的真实 Retail 查询和 M3 的混合控制流为基础。M4 为真实工具补齐统一授权、分发、隔离与执行结果约束。

## 关键决策

- 把 M1 的真实 Retail 查询纳入最小 Tool Registry 和统一 Dispatcher。执行层校验可信授权、参数、副作用和审批策略，通过后按执行绑定派发。
- Retail 查询等内置工具在当前进程执行，SQL 沿用受限零售身份。模型生成的 Python 接入真实隔离后端。统一处理有界并行、限额、超时、取消、资源回收、Artifact 与执行事件。
- 每次 Run 按可信用户、租户和工作区读取 MCP 配置，发现并冻结本次工具列表。首版接入远程 Streamable HTTP，持久配置归 Agent。
- 同轮多调用使用独立批次策略，默认串行，可配置有界并行；审批、副作用和执行绑定分别维护。
- Sandbox 选型为 OpenSandbox + NFS-Ganesha，课堂在 Docker 中模拟独立计算分配、NAS 文件保留与重挂载；云端接 gVisor + 托管 NAS。见[选型 ADR](../../docs/adr/0003-tool-sandbox.md)。
- 外部业务写入由执行策略要求审批，派发前返回待审批提案。持久审批在 M5，完整 MCP 管理、Skill 与 Action 治理在后续篇章。
- 模块按 `api / runtime / harness / workflows / tools` 分工；MCP 与 Sandbox 实现归 `tools`，HTTP 路由归 `api`。本课方案明确现有文件迁移与单向依赖。

## 课堂演示与验收

- 复用 M1 查询样本核对接入统一执行层前后的结果。同一 Run 查询授权 Retail 数据，再在隔离 Python 中计算、绘图并取回 Artifact。
- 验收权限拒绝、并发上限、超时与取消。演示计算删除后 NFS 原文件仍可授权读取。核对后端实际终止和资源清理，观察结果按原调用 ID 回传。

## 上下文索引

- [素材索引](context/_INDEX.md)
- [产品需求文档](spec/PRD.md)是本课功能规格与验收标准的维护入口。
- [技术方案](design/TECH_DESIGN.md)与[HTML 审阅版](design/TECH_DESIGN.html)承接有效 PRD，当前待评审。
