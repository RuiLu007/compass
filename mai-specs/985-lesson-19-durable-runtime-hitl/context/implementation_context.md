# Compass 第 19 课实施上下文｜M5

## 2026-11-12 范围纠正

课程负责人本次明确：第五篇从 Agent Runtime 建立以来，大部分会话和运行对象依靠内存；本课要补齐全部必要持久化，并独立设计 Human in the Loop 增量。有效需求及验收统一维护在[PRD](../spec/PRD.md)。

核心验收为：只问一次就形成左侧 Session；前序案例执行完成后刷新，原问答、过程、状态、Workflow 和产物全部保留；刷新恢复的最终业务视图与实时流结束时一致。2026-11-12 最新澄清为两个接口共用响应协议：Message 返回非流式 JSON，加载持久内容；Run 返回 SSE，持续输出增量。JSON 响应体和每个 SSE data 均使用 AgentResponse，前端共用 validator、reducer 和渲染组件。执行中断线与业务取消分离。最新存储决定为本地 Docker Redis 必选：Streams 记录高频增量并持久确认，SQL 批量投影完整视图，关键业务事务与 Outbox 同提交，关键展示边界强制投影。

口述中的“ARM runtime”按现有底层 LLM Runtime 模块承接，覆盖逻辑模型调用、每次真实 Attempt、必要输入输出、用量和私有续接状态。

## 已读取的工程现状

- AppShell 会话列表为空占位，AnalysisComposer 的 turns 和追问历史在前端内存。
- RunRegistry、AgentState、执行节点、计划/Verifier/预算在进程内存；当前 v1 流断线会取消执行。
- workflow_asset 已持久保存资产；WorkflowDrafts 是有 TTL 的内存容器，QuickJS 的运行位置和子调用结果也未形成恢复日志。
- model_provider、model_call 等已有数据库表，model_call 保存调用元数据和最终 execution 摘要；本课复用并补齐 Run 关联、逐次 Attempt 及必要恢复内容。
- 当前工作区已有 0008 的 MCP/Sandbox 迁移文件及 api/runtime/harness/workflows/tools 拆分源码；数据库实际应用版本需在实施前核验。

## 持久化覆盖

Session、Message、Run、Step/Attempt、Redis 短期增量与 SQL 关键 Outbox、Checkpoint、计划/Todo/Verifier/预算、模型逻辑调用与真实尝试、每 Run 工具目录/策略/参数/观察、Workflow/Skill 源版本与嵌套/并行执行、待保存草稿、产物内容、等待点/决定/动作回执全部有明确存储位置。

新增业务表归 agent/main，Redis 流由 Agent 管理；Web 登录会话和授权仍归 Web。复用现有模型、Workflow、MCP 配置和 Sandbox 分配，原始密钥、活跃连接和进程句柄按各自生命周期管理。数据结构、命名与约束见[数据设计](../design/DATA_DESIGN.md)。

## HITL 与课次边界

M4 提供工具执行策略、审批挂钩、资源清理和 Artifact 合同。本课完成持久提案、人工决定、等待资源释放、批准后原 Run 恢复、拒绝/到期/取消及效果核对，使用受控演示适配器和合成数据。

单实例任务与连接解耦、Message 持久加载和 Run 游标重连归本课。第 20 课复用这些能力，增加队列、Worker、租约、资源治理与自动接管；长期记忆归后续篇章。原素材中将客户端续流放在 M6 的划分由本次需求修订替代。

## 设计与验证状态

本次只更新 PRD、持久化主方案、完整数据设计、Redis 本地部署、代码骨架和 HITL 增量方案及审阅版。迁移规划 0008 → 0009，SQL 真源仍在 Agent 应用中。真实重启、模型/工具计数、数据库升级恢复及浏览器前后视图比较是实施阶段必需验收，当前尚未执行。
