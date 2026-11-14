# 网络契约

Feature 990 新增 Agent `ObservabilityStatus`、`ModelComparison` DTO 和平台查询路由；Trace 关联使用安全响应头，既有调用与 SSE body 保持兼容。改动源仍为 Agent `schemas.py` 与路由，经 `pnpm contracts` 生成本目录和 Web 类型/运行校验文件。平台只读接口保留服务端权限检查，具体数据口径见 [观测运行说明](../deploy/observability/README.md)。

唯一源为 `apps/agent/src/compass_agent/schemas.py` 与 FastAPI 路由。`openapi.json`、`demo-overview.schema.json`、`model-contracts.schema.json`、`run-stream.schema.json` 与 `run-stream-limits.ts` 均由根 `pnpm contracts` 生成，禁止手改。Web 消费副本同时生成到 `apps/web/src/generated`，通过 `pnpm contracts:check` 验证无漂移。

当前包含健康检查、合成数据概览、模型管理/调用、工作区数据连接管理契约，以及 `compass.run` v1 的 RunPacket、取消回执和有限流常量。Run 专用 Schema 供 BFF 与浏览器逐帧校验，OpenAPI 生成类型供编译期使用。生成过程串行运行 Python 导出与 openapi-typescript，无数据库或网络服务依赖。

`/v1/data/connections` 提供当前工作区的 PostgreSQL/MySQL 连接列表和创建。按 ID 编辑和测试，每条记录带独立版本。请求及安全响应由 `DataConnectionSave`、`DataConnectionList`、`DataConnectionView` 和 `RetailConnectionTest` 定义。密码为写入字段，响应仅包含配置状态。旧的 `/v1/data/retail-connection` 继续操作默认 Retail 记录。

`POST /v1/analysis/runs` 是唯一业务推理入口，请求通过 `selection` 选择固定 Provider 或自动路由。旧 `POST /v1/models/calls` 已下线。Provider 诊断保留 `CallResponse` / `ModelEvent`，调用记录继续通过 `GET /v1/models/calls/{call_id}` 查询。

Feature 991 的 ExecutionSummary / AttemptSummary 由 Agent DTO 生成，分别嵌入调用结果及历史查询；旧记录 execution 为 null。ShowcaseCatalog / ShowcaseInput / ShowcaseEvent / ShowcaseReport 用于管理员固定模拟演示，场景只接受枚举和 call_id。诊断、模拟和分析 Run 的流式终态均需干净 EOF 后提交；派生文件继续由 `pnpm contracts` 管理。

`compass.execution.node@1` 是 Run 的可选扩展，使用 `RunExecutionNode` 完整快照统一表达分析、旁白、工具、Workflow、Skill、SubAgent 和执行分组。工具来源、执行环境、父子关系和依赖独立表达；BFF 与浏览器共享 `ExecutionSequence` 的身份、版本、关系及展示策略校验。保留原 Step / Tool 事件用于生命周期与文件来源检查，保存邀请继续沿用旧扩展。字段与课次接入边界见[统一执行过程设计](../mai-specs/987-lesson-17-hybrid-workflow/design/TECH_DESIGN.md#7-统一执行过程节点)。
