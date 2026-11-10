# 第 25 课大纲节选｜长任务上下文治理：让数小时运行和多 Agent 协作不丢目标、不重做、不漂移

来源为课程仓库当前工作区的 `course/curriculum/v5/course_outline.md`。

本课目标：让 DataAgent 在长时间运行、中断恢复和多 Agent 协作中持续保持正确的目标、状态、约束和证据。

- 架构选型视角：根据数据用途、生命周期和恢复责任，在 History、State、Checkpoint、Summary、Artifact 与 Handoff 中选择承载位置，避免用聊天历史同时承担状态库和证据库职责。
- 区分 History、State、Checkpoint 和 Summary：解决所有过程信息都依赖聊天历史保存的问题。
- 管理上下文生命周期：防止只在当前 Step 有效的信息被错误带入整个 Run 或后续 Session。
- 按用途生成不同摘要：解决步骤摘要、阶段摘要、交接摘要和用户报告相互混用的问题。
- 定义压缩保留不变量：确保目标、未完成任务、关键约束、失败记录和证据引用不会被摘要删除。
- 大型对象进入 Artifact Store：避免 SQL 结果、图表和日志在每轮调用中重复传输。
- 恢复时重新装配上下文：避免中断后简单重放聊天历史，导致重复查询或使用过期权限。
- 显式设计 Parent-Child Handoff：防止子 Agent 获得完整父任务上下文和不必要的数据权限。
- 检测 Context Drift：发现目标偏移、事实失真、任务重复和证据来源丢失。
- DataAgent 实战：完成月度经营分析任务的中断恢复、阶段压缩和库存分析 Child Run 交接。
