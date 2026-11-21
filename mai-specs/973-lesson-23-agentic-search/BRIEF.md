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

[PRD](spec/PRD.md) 维护默认问题卡、材料、金标准与验收要求。通用 Skill 指导模型按信息缺口选源，单次运行的工具顺序由模型决定。

## 上下文索引

- [素材索引](context/_INDEX.md)

## 技术方案

[Compass Chat 高保真交互原型](design/CHAT_PROTOTYPE.html) · [离线 HTML 阅读版](design/TECH_DESIGN.html) · [主方案](design/TECH_DESIGN.md) · [结论与引用校验](design/EVIDENCE.md) · [Retail 数据与验收](design/RETAIL_CASE.md) · [代码设计](design/CODE_DESIGN.md)

本课的具体交付按[模型工具与语义层清单](design/TECH_DESIGN.md#1-本课实施清单)、[上下文装配](design/TECH_DESIGN.md#2-上下文装配)、[Prompt 设定](design/TECH_DESIGN.md#3-prompt-设定)和[Verify 接入](design/TECH_DESIGN.md#4-verify-接入)查阅。

主 Agent 通过 Plan → Execute → Verify 驱动检索。Context Runtime 全量装配选中场景的基础方法，按需装入经营事实，再根据已核断言、缺口与实际预算收口。技术方案待评审，文件级工作列在[开发顺序](design/TECH_DESIGN.md#7-开发顺序兼容与恢复)。

## 实施与验收

[实施记录与端到端验收](test/ACCEPTANCE.md)
