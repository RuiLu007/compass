# 974: 第 22 课｜企业经营语义治理

| 项         | 值                                   |
| ---------- | ------------------------------------ |
| Phase      | review                               |
| Profile    | standard                             |
| 优先级     | P1                                   |
| 负责人     | andy                                 |
| 目标日期   | 待填                                 |
| 涉及子系统 | Web/BFF、Agent Runtime、受管业务连接 |

## 一句话描述

为 Compass 建立经营语义层，让各部门能维护自己的指标，让员工从 Chat 中找到可用口径、确认歧义，并把已确认的开放查询保存为指标草稿。

## 背景

统一多租户、多部门、多岗位的指标口径，缩短定义与审查周期。

实现与代码复评已完成。第 22 课保持 review，识别与消歧的冻结标签待业务人员独立复核。[本轮验收](test/TEST_PLAN.md)

## 关键决策

先在企业语义内容专项中定义术语、指标、实体关系与可信执行绑定，再由主 PRD 规定平台和 Chat 如何使用这些内容。技术方案采用 PostgreSQL 和 JSONB，复用 Compass 的权限、上下文与持久运行机制。全局共享预置定义，各租户分别授权数据和执行绑定。指标、别名、默认口径与置顶上下文按范围入库；场景 SOP 由 PromptOps 管理，Agent 通过共用工具发现、解释、查询和提议修订。

## 上下文索引

- [素材索引](context/_INDEX.md)
- [企业语义内容专项需求](spec/SEMANTIC_CONTENT_PRD.md) · 先读业务词典、指标口径、实体关系和教学数据映射
- [PRD](spec/PRD.md) · 需求与验收条件的维护入口
- [指标管理与 Chat 交互原型](spec/METRIC_WORKSPACE.html) · 管理页和三种对话场景，浏览器本地可操作
- [默认零售语义资产设计](design/RETAIL_SEMANTIC_ASSETS.md) · 业务分层、两个租户、可执行绑定、教学 SQL 安装与验收
- [技术方案](design/TECH_DESIGN.md) · 模块、接口、迁移与实施切片
- [HTML 审阅版](design/TECH_DESIGN.html) · 架构、发布与执行图解
- [架构通用性评审](review/design_review.md) · 现有运行链路的改造点、兼容边界和验证用例

- [实施与验证](IMPLEMENTATION.md)
- [代码评审](review/code_review.md)
- [业务测试与验收](test/TEST_PLAN.md)
