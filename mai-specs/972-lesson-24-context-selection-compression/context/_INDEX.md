# 第 24 课设计输入

[定稿课件 · 共 21 页](../../../../../../lessons/v5/formal/lesson_24_context_selection_compression/production/slides.pptx)是本课技术选型的课程输入。第 21 页是 Compass 选型的分隔页。

| 页    | 在本课设计中的作用                                                                                                         |
| ----- | -------------------------------------------------------------------------------------------------------------------------- |
| 1–3   | 第六篇定位、从判断材料价值到验证质量和成本的目标、概念篇入口                                                               |
| 4–8   | 窗口与实际输入的区别、长上下文的计算代价、噪声与中段信息遗漏。用文件回读和长历史交班验证。                                 |
| 9–10  | 相关性、重要性、必要性、可靠性、时效性、重复度与成本；给输出预留空间。给固定输入、当前任务和最近原文分别计量。             |
| 11–12 | 方法篇与过滤、选择、排序、压缩、摘要、截断、预算及生命周期的分工。                                                         |
| 13–15 | 三类 Harness 对工具观察、历史压缩和新窗口重建的处理。Compass 对接现有 Journal 与原生续轮。                                 |
| 16–18 | 过滤、回读与压缩研究。工具留原件，历史先由 JEV 选择，容量仍紧张时剪枝、摘要，再按预算接回最近轮。                          |
| 19–20 | JEV 的 keep/compress/drop 与动态预算示例。Compass 使用 JEV 的 keep / drop 判断完整历史轮；选择后仍有容量压力再做交接摘要。 |
| 21    | [Compass 本课选型](../design/TECH_DESIGN.md)。首页两例覆盖超大工具结果的渐进读取和长会话压缩。                             |

[单课大纲](../../../../../../lessons/v5/formal/lesson_24_context_selection_compression/brief/lesson_outline.md)与[逐页草案](../../../../../../lessons/v5/formal/lesson_24_context_selection_compression/brief/lesson_outline_by_page.md)保留筹备背景。这里的页码对应定稿课件。

## 技术阅读版维护

有效技术源为 `../design/TECH_DESIGN.md`；`../design/document/guide.md` 按相同技术结构编排，图表与代码通过 `edition.json` 从 Markdown 引用。修改叙述时同步 guide，提示词及代码只维护技术源后重新生成。

从 Compass 根目录执行：

```bash
node scripts/course-design/reader.mjs mai-specs/972-lesson-24-context-selection-compression/design/document/edition.json
node apps/home/scripts/lesson-resources.mjs --lesson 24
```

Home 仅刷新第 24 课。当前版本明确本课目标、6 张架构／时序／流程图、完整提示词及文件级实施清单；Runtime 能力保留设计状态。
