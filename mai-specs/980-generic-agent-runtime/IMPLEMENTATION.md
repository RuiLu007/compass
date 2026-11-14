# 实施与验证

## 实际变更

- 删除 `compass_agent/analysis/` 和未使用的空目录。
- Harness 集中 AgentDefinition、系统提示词、有限上下文和通用模型/工具循环；支持空工具集及自定义 RegisteredTool。
- RunService 默认通用配置，产品 API 显式提供 `compass_definition()`；Retail 查询、计划、核验和处理器归入内置工具包。
- 意图/模型路由接受类型化任务提示，确定性 Workflow 选择归 routing；Workflow 子任务执行归 workflows。
- 移除固定课堂问题、日期和六次调用的提示词规定；工作流日期由请求提供。测试用例显式携带样例输入。
- 保留 HTTP 路径、DTO、数据库结构、工具安全策略、持久化与 Worker 接续契约。
- 崩溃测试按被测 Run 统计模型 Attempt，排除后台会话标题生成的独立调用。

## 当前验证

- Agent 单元测试：357 项通过。
- 结构契约补充回归：38 项通过（最终状态端口拆分后）。
- Worker：强制绕过缓存执行，27 项通过。
- Agent lint、类型检查和构建通过，Wheel 模块布局验证通过；契约生成一致性检查通过。
- PostgreSQL、Redis、OpenSandbox/NFS 集成回归：89 项通过，2 项跳过；实际耗时 576.82 秒，退出码 0。覆盖动态 Workflow、真实 SQL、Skill、脚本修复、人工确认、进程强杀、Checkpoint 回放、配额与双 Worker。
- 跳过项为 Sandbox 控制面重启和 NAS 断连测试；其 opt-in 环境变量未启用，未计入通过。
- HTML 在 1280px 与 390px 下检查，页面无横向溢出；Mermaid 架构图已渲染，并保留内联主图和可展开图源。

## 验证边界

集成测试使用本地专用 `compass_agent_test_*` 和 `compass_retail_test_*` 数据库与临时账号，凭据只在私有环境传入。生产供应商账号调用和 Web 浏览器全流程未重复验收，网络契约保持一致。

系统提示词和路由输入指纹有变化；部署前应结束旧版本在途 Run。本次验证覆盖当前版本的暂停、恢复与进程重启，旧版本在途 Checkpoint 跨版本续接未验收；已终态历史读取继续使用原数据契约。

## 验收结论

AC1～AC5 已完成本次约定范围的验证；外部契约检查无漂移。固定课堂预设已移出生产源码，非 Retail Agent 可只注入工具与提示词运行，空配置可直接回答。默认产品能力由 API 显式注册。

集成范围为 test_hybrid_workflow_integration、test_tool_runtime_integration、test_durable_runtime_integration、test_durable_runtime_faults、test_durable_case_matrix、test_durable_boundaries、test_run_scheduler_integration、test_run_scheduler_limits、test_run_scheduler_agent_restart、test_run_scheduler_process。通过私有环境显式提供测试 PostgreSQL、Redis 和 Sandbox 配置执行，测试临时库与账号按夹具清理。

新增模块导入、工具边界和打包布局已自检；桌面和窄屏方案阅读版无页面横向溢出。未提交或推送 Git，保留工作区既有修改。
