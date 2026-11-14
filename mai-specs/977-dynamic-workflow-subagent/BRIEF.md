# 977: 第 17 课｜动态 Workflow 与 SubAgent

| 项         | 值                                                                                |
| ---------- | --------------------------------------------------------------------------------- |
| Phase      | dev                                                                               |
| Profile    | standard                                                                          |
| 优先级     | P1                                                                                |
| 负责人     | andy                                                                              |
| 目标日期   | 待填                                                                              |
| 涉及子系统 | Agent Workflow Engine、SubAgent、工具注册与 Run 持久化；Web Chat 与 Workflow 管理 |

## 一句话描述

在 Chat 验证自主探索、工作流创建与保存，以及换参数后的模型自主复用。

## 背景

第 17 课需要展示模型生成编排 DSL、Workflow Engine 调度独立子 Agent，以及工作流资产进入个人工具目录的完整链路。

## 关键决策

- 工作流和子 Agent 通过 Tool Calling 进入 Runtime。模型编写 DSL，用户确认保存后，后续 Run 按当前用户和工作区加载。
- 子 Agent 使用独立进程与消息上下文。父 Run 持久记录它的执行关系和最终结果。

## 上下文索引

- [素材索引](context/_INDEX.md)
- [PRD 与预置文案](spec/PRD.md)
- [技术方案](design/TECH_DESIGN.md) · [HTML 审阅版](design/TECH_DESIGN.html)
- [实施记录](IMPLEMENTATION.md)
