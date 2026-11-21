# 975: 第 21 课｜系统提示词与 Context Runtime

| 项         | 值                                       |
| ---------- | ---------------------------------------- |
| Phase      | review                                   |
| Profile    | standard                                 |
| 优先级     | P1                                       |
| 负责人     | andy                                     |
| 目标日期   | 待填                                     |
| 涉及子系统 | agent runtime, promptops, web management |

## 一句话描述

经营人员在 Compass 中完成经营问答、数据分析、看板创建和经营报告。平台管理员可在 Web 中按本地目录维护 Markdown 与 JSON 提示词资产，独立发布文件或整体发布生成快照。代码按租户、部门、任务与场景组合提示词、工具和 Skill。

## 背景

经营人员用同一组数据问一个数、做比较、做看板或写周报时，需要的材料与交付形式各不相同。管理员也需要知道一次改词会影响哪些任务，能在线验证并恢复旧版。

## 关键决策

本课使用 standard Profile。2026-11-19 修订后，主 PRD 维护产品目标、流程和验收，场景扩展文档定义四类任务的研究方法、指标语义召回、上下文选取、回答风格与产物要求，管理侧 PRD 维护本地文件树、单文件在线改版与整库快照的用户故事。课程起点为 `compass/L20-complete`（提交 `b547e91`）。两份技术方案面向当前 Compass 集成提交 `d5d36e5`、Agent 库 0015。运行侧与管理侧共用 Agent 库里的不可变版本清单。11 月 19 日完成两份设计复评，并将四项 P1 修正纳入交付。实际开发与端到端验收已有记录。

## 上下文索引

- 2026-11-20 用户确认调整资产粒度，当前有效修订见 [PromptOps 资产粒度修订](design/ASSET_MODEL_UPDATE.md)，本次交付见 [实施与验证记录](test/ASSET_MODEL_VERIFICATION.md)。本次实现聚合场景、工具完整声明和分组指令；沿用现有数据库表，以显式事务同步源码与数据库，保留历史和草稿。

- [素材索引](context/_INDEX.md)
- [Context Runtime PRD 与课程验收口径](spec/PRD.md)
- [四个经营场景的产品规格](spec/SCENARIO_REQUIREMENTS.md)
- [PromptOps PRD 与管理端验收](spec/PROMPTOPS_PRD.md)
- [PromptOps 离线交互原型](spec/prototype/promptops.html)
- [Context Runtime 技术方案](design/TECH_DESIGN.md) · [运行时 HTML 审阅版](design/TECH_DESIGN.html)
- [PromptOps 技术方案](design/PROMPTOPS_TECH_DESIGN.md) · [管理端 HTML 审阅版](design/PROMPTOPS_TECH_DESIGN.html)
- [提示词资产迁移与跨入口验证附录](design/CODE_DESIGN.md)
- [设计复评](review/design_review.md) · [开发与端到端验收记录](test/VERIFICATION.md)
