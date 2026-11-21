# 素材索引 — 975

| 文件                                                       | 类型     | 来源与原名                                   | 关键内容摘要                                                                    | 收集日期   | 派生自 |
| ---------------------------------------------------------- | -------- | -------------------------------------------- | ------------------------------------------------------------------------------- | ---------- | ------ |
| [lesson-21-requirements.png](./lesson-21-requirements.png) | png      | 课程负责人在本次会话提供的第21课完整需求清单 | 六组建设任务、六类示例、四层提示词以及六项量化验收指标。用于本课 Compass 需求。 | 2026-11-17 | —      |
| [chatgpt_tech_design.md](./chatgpt_tech_design.md)         | Markdown | 课程负责人提供的 Context Runtime 构想        | Context Schema、Prompt Runtime Support、四段管线、预算与固定版本。              | 2026-11-19 | —      |
| [chatgpt_promptops.md](./chatgpt_promptops.md)             | Markdown | 课程负责人提供的 PromptOps 构想              | 从资产、模板与版本，到发布包、活动指针和运行时精确读取。                        | 2026-11-19 | —      |
| [chatgpt_observerbility.md](./chatgpt_observerbility.md)   | Markdown | 课程负责人提供的装配观测构想                 | 决策、来源与变换、策略回放和装配指标。                                          | 2026-11-19 | —      |

## 关键发现

- 本课覆盖场景与阶段建模、提示词体系、PromptOps 管理、Context Runtime、动态装配与热更新、效果验证与回归。
- 原图记录课程最初的候选场景。2026-11-19 明确首版保留前四类，即经营问答、数据分析、看板创建、经营报告，四类均需通过交付验收。
- 当前质量目标为动态装配正确率 ≥98%、新版本回归通过率 ≥95%、发布生效 ≤1 分钟、关键中断 0、租户隔离与安全 100% 通过。样例数及判定方式见运行侧 PRD。
- 管理端按本地 Markdown 路径浏览和独立发布，整体发布保留完整版本快照。租户、部门、任务及场景的装配规则归代码维护。
- 工程设计依赖 Compass 第20课完成节点 compass/L20-complete。

## 当前规格

整体需求见[主 PRD](../spec/PRD.md)，四类场景的研究方法、指标语义召回与核对、上下文选取及交付原则见[场景扩展规格](../spec/SCENARIO_REQUIREMENTS.md)，管理侧以 [PromptOps PRD](../spec/PROMPTOPS_PRD.md) 为准。
