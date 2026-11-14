# 988: 第 16 课｜从 ReAct 到 Plan-and-Execute：建立受控自主执行机制

| 项         | 值                                                       |
| ---------- | -------------------------------------------------------- |
| Phase      | dev                                                      |
| Profile    | standard                                                 |
| 优先级     | P1                                                       |
| 负责人     | andy                                                     |
| 目标日期   | 待填                                                     |
| 涉及子系统 | apps/agent（Run、验证与预算）、apps/web（Chat 计划面板） |

## 一句话描述

在同一 ReAct 循环内加入 Todo-driven Plan-and-Execute，模型规划、推进与重规划，Runtime 负责验证、预算和停止。

## 背景

以 M1 的同步 Agent 与真实 Retail 查询为基础。多步骤诊断需要管理计划、验证关键结论，并在重复尝试或资源耗尽时及时收口。

## 关键决策

- 模型通过 Todo 工具选择是否规划、推进哪一项和如何调整，复用 M1 的 Run 入口和 Retail 查询。计划定义与实际进度分别维护。
- 独立 Verifier 检查步骤与最终结论。模型根据反馈重查或调整，Runtime 执行预算与停止规则。
- 本课负责单个 Run 的预算与派发前停止。第 17 课负责外层 Workflow，第 18 课负责工具实际执行的限额与清理。

## 课堂演示与验收

- 对同一 Retail 分析演示直接行动、先列计划和运行中建立计划，观察模型选择与实际执行。注入缺失数据或验证失败，展示重查、重规划和有效结果复用。
- 缩小预算后核对派发计数。达到限制或需人工介入时结束本次自主推进，返回原因和已有结果。

## 上下文索引

- [素材索引](context/_INDEX.md)
- [产品需求文档](spec/PRD.md)，本课需求与验收标准的维护位置。
- [技术方案](design/TECH_DESIGN.md) · [代码设计](design/CODE_DESIGN.md) · [HTML 审阅版](design/TECH_DESIGN.html)。方案已复评，实施基线为已同步的第 15 课代码。
- [技术方案评审](review/design_review.md) · [实施记录](IMPLEMENTATION.md) · [课堂运行说明](../../docs/lesson-16-controlled-autonomy.md)。
