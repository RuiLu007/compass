# 素材索引 — 985

| 文件                                                   | 类型 | 来源与原名                         | 关键内容                                  | 收集日期   |
| ------------------------------------------------------ | ---- | ---------------------------------- | ----------------------------------------- | ---------- |
| [source_context.md](source_context.md)                 | md   | 课程负责人第五篇调整材料，第 19 课 | 原始课次范围，保留为只读输入              | 2026-11-09 |
| [implementation_context.md](implementation_context.md) | md   | 课程材料、当前工程与本次负责人纠正 | 完整持久化、双接口同协议、HITL 和跨课边界 | 2026-11-12 |

## 当前有效范围

2026-11-12 的负责人指示已落实到 [PRD](../spec/PRD.md)：补齐前序全部必要持久化，首问形成 Session，刷新完整恢复且与实时输出一致；Human in the Loop 单独设计。本地 Docker Redis 已纳入正式范围，承担短期流；SQL 保存完整投影和关键 Outbox。最新接口澄清：Message 非流式、Run 流式，响应共用同一业务结构与 reducer。原始材料中的审批中心叙事及 M6 续流边界按新 PRD 修订。

技术与数据合同见[主方案](../design/TECH_DESIGN.md)、[数据设计](../design/DATA_DESIGN.md)、[Redis 部署与投影](../design/REDIS_DESIGN.md)、[HITL 增量方案](../design/HITL_DESIGN.md)。前置 M4 尚有待落地部分，后续验证须以实际实现与完整案例清单为准。
