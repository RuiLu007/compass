# 973: 第 23 课 Agentic Search：企业知识库与跨源经营诊断

| 项         | 值                                                      |
| ---------- | ------------------------------------------------------- |
| Phase      | draft                                                   |
| Profile    | standard                                                |
| 优先级     | P1                                                      |
| 负责人     | andy                                                    |
| 目标日期   | 待填                                                    |
| 涉及子系统 | Agent Runtime / PromptOps、经营语义、多源检索、课程评测 |

## 一句话描述

让模型在普通 Chat 内自主跨业务域补查，结合正式指标、企业知识、历史产物与外部资料，交付可以审批的经营意见。

## 背景

以行野华东微信小程序的跑鞋引流预算为主案例，使用学员版 retail 快照复核销售、商品访问、锁库短缺与采购实收。

## 关键决策

2026-11-22 按用户确认重梳技术方案：数据 Agent 默认采用通用 Loop 中的搜索方法，移除搜索模式开关；来源存在性、权限与资料状态由工具和来源服务处理。Verify 在工具批次返回后和候选交付前通用接入，可修复缺口回到同一循环。统一 final_answer 入口按任务提交 plain 或 evidence，校验接受并持久发布后进入最终正文。

本轮已按连续授权实施通用 Loop、回答协议与知识库接入，并完成工程检查及真实 Chat 链路复验。综合案例已交付带 10 个引用的部分回答，完整履约诊断与采购资格仍有缺口；PRD 业务验收未达结项门槛。历史验收保留，当前结果分别登记在 dev/IMPLEMENTATION.md、review/code_review.md 与 test/ACCEPTANCE.md。[PRD](spec/PRD.md) 的业务问题、材料、金标准与验收保留；涉及技术机制的当前决定记录在主方案。

## 上下文索引

- [素材索引](context/_INDEX.md)

## 技术方案

[离线 HTML 阅读版](design/TECH_DESIGN.html) · [主方案](design/TECH_DESIGN.md) · [回答路由与引用契约](design/EVIDENCE.md) · [供评审的代码设计](design/CODE_DESIGN.md) · [知识库与向量检索](design/VECTOR_SEARCH.md) · [课堂数据与验收](design/RETAIL_CASE.md)

重点阅读[通用 Loop 与工具边界](design/TECH_DESIGN.md#2-通用-loop-与工具边界)、[Verify 的通用接入](design/TECH_DESIGN.md#4-verify-的通用接入)、[普通回答循证回答与正文边界](design/TECH_DESIGN.md#5-普通回答循证回答与正文边界)。前端传输与引用用例见[接口协议](design/TECH_DESIGN.md#7-前端推理接口协议)，文件级调整见[按 App 划分的文件改动](design/TECH_DESIGN.md#8-按-app-划分的文件改动)。

[原 Chat 交互原型](design/CHAT_PROTOTYPE.html)保留为旧版交互参考；新版候选、校验与正文的边界以主方案和回答契约为准。

## 实施与验收

[实施记录与端到端验收](test/ACCEPTANCE.md)

## 工作区知识库管理

第 23 课同时交付「数据与语义 → 知识文档」。工作区维护者可以创建知识库、批量导入文件、检查解析结果、编辑和启停单个分段，管理操作直接影响 Agentic Search 的召回与原文回读。详细交互和数据库设计沿用[向量检索方案](design/VECTOR_SEARCH.md)。

平台管理员从经营总部打开知识文档即可浏览各租户与工作区的知识库、原文及分段。资料的维护权限和 Agent 检索范围按对应工作区授权。
