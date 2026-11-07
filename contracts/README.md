# 网络契约

Feature 990 新增 Agent `ObservabilityStatus`、`ModelComparison` DTO 和平台查询路由；Trace 关联使用安全响应头，既有调用与 SSE body 保持兼容。改动源仍为 Agent `schemas.py` 与路由，经 `pnpm contracts` 生成本目录和 Web 类型/运行校验文件。平台只读接口保留服务端权限检查，具体数据口径见 [观测运行说明](../deploy/observability/README.md)。

唯一源为 `apps/agent/src/compass_agent/schemas.py` 与 FastAPI 路由。`openapi.json`、`demo-overview.schema.json` 与 `model-contracts.schema.json` 均由根 `pnpm contracts` 生成，禁止手改。Web 消费副本同时生成到 `apps/web/src/generated`，通过 `pnpm contracts:check` 验证无漂移。

当前包含健康检查、合成数据概览与模型管理/调用契约；模型 Schema bundle 还包含 SSE 事件供 Web 逐帧校验。RunEvent 等运行编排契约随后续功能实现。生成过程串行运行 Python 导出与 openapi-typescript，无数据库或网络服务依赖。TypeScript 类型配合 JSON Schema 运行时校验使用。


模型调用接口的 `ChatCallInput` 支持静态 `provider_id` 或 `main_model`（fixed/auto）二选一。整轮分类与推理使用 `RoutedCallResponse` 和 `RoutedModelEvent`，两级分类仅 `category_id` / `scene_id`；Provider诊断保留 `CallResponse` / `ModelEvent`。全部Schema和Web类型继续由Agent DTO与路由生成。


Feature 991 的 ExecutionSummary / AttemptSummary 由 Agent DTO 生成，分别嵌入调用结果及历史查询；旧记录 execution 为 null。ShowcaseCatalog / ShowcaseInput / ShowcaseEvent / ShowcaseReport 用于管理员固定模拟演示，场景只接受枚举和 call_id。两类模型 final 与模拟 final 均需完整 EOF 后提交；派生文件继续由 `pnpm contracts` 管理。
