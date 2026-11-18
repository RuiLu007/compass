# Compass Web 任务失败配置盘点

2026-11-16 · Feature 984。核对当前工作区的 Web、Agent、LLM Runtime、工具限制和启动模板。数值按源码默认、部署模板和本地配置文件分别说明。未采集真实运行中的用户、Provider、数据库或进程配置，所以本表用于定位潜在失败，不能归因到某一次失败。

## 先识别失败发生在哪一层

| 用户现象                                                  | 首查                                                                       |
| --------------------------------------------------------- | -------------------------------------------------------------------------- |
| 点发送即收到权限不足，未拿到 Run ID                       | 登录、当前工作区、`module.analysis`、`models.invoke`、来源校验。           |
| 点发送收到「当前任务已满」或 429                          | Run 受理积压上限和库存中的未结束任务。                                     |
| 已拿到 Run ID，持续显示「等待执行」                       | 租户或全局执行槽、现有 Worker 消费与租约、Outbox 是否推进。                |
| 分析已经开始，随后提示模型不可用、步骤/预算上限或工具失败 | 模型路由、Provider、单 Run 预算、模型并发门禁及工具配置。                  |
| 断流、暂停、待核对或等待确认                              | 查询原 Run 和 Timeline。区分浏览器重连、服务恢复受阻、人工等待和真实失败。 |

## 当前代码已生效的配置与约束

### 1. 接口、身份与运行就绪

| 触发点                         | 配置或边界                                                                                                                                                                 | 常见表现                                                                                                               |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Web 提交、查询、运行中权限检查 | 有效登录、活动用户和工作区、可信租户范围、`module.analysis` + `models.invoke`。写入接口还校验 Origin/CSRF。运行时通过 Web 回调复核权限，用户或工作区状态变化可暂停原 Run。 | 401/403、越权资源 404、运行中停止或安全隐藏结果。                                                                      |
| BFF → Agent、Agent → Web       | `AGENT_API_URL`、两侧匹配的 `COMPASS_AGENT_SERVICE_TOKEN`、`COMPASS_WEB_AUTHZ_URL`。BFF 非流式请求超时 15 秒，SSE 请求周期 65 秒。                                         | 错地址、服务凭据不匹配、超时或 JSON Schema 不兼容时，Web 可统一显示服务暂不可用。SSE 周期结束走重连，需用原 Run 核对。 |
| 数据库、持久化及 Redis         | Agent 主库地址、权限与实际迁移版本；Redis 地址、凭据及持久流；`COMPASS_RUNTIME_STATE_KEY`；Provider 和数据连接加密另用 `COMPASS_MODEL_ENCRYPTION_KEY`。                    | 接单 503、重启后快照无法读、活跃流缺失后恢复受阻。密钥已配置只说明输入存在，仍需核对各进程使用同一有效密钥。           |
| 输入及会话并发                 | 问题至多 8,000 字符；上下文另有字节限制。同 Session 上一 Run 或终态 Outbox 尚未收口，返回 `session_busy`。重复请求需保持 requestId 与输入一致，控制操作受版本约束。        | 422、409、已结束但列表暂仍不可再发、重复提交冲突。                                                                     |
| 现有人工确认                   | 业务动作批准当前需要可审批租户管理员；常规等待 TTL 15 分钟；确认依赖 Wait 版本、摘要和状态。                                                                               | 等待人工、403、过期或争用 409。确认被受理和动作提交是不同阶段。                                                        |

主要入口为 [Web BFF](../../../apps/web/src/server/http/durable-analysis-handler.ts)、[Web 授权](../../../apps/web/src/server/composition.ts#L120)、[Run 受理](../../../apps/agent/src/compass_agent/runtime/store.py#L306)。

### 2. Run 排队、进程容量和恢复

| 控制层               | 当前默认及规则                                                                                                                                                                                                                                   | 常见表现                                                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| Run 积压准入         | `COMPASS_RUNTIME_MAX_BACKLOG=12`、`COMPASS_RUNTIME_TENANT_BACKLOG=4`。按全局及租户计数，通常含排队、已租、回收和非阻塞 parked。当前源码排除已被标记 blocked 的 parked。可按租户 UUID 用 `COMPASS_RUNTIME_TENANT_OVERRIDES` 覆盖 active/backlog。 | 受理前 `runtime_backpressure` 429。当前默认 Retry-After 为 1 秒，实际等待由槽和队列变化决定。                            |
| Run 执行槽           | `COMPASS_RUNTIME_MAX_ACTIVE=2`、`COMPASS_RUNTIME_TENANT_ACTIVE=1`。未确认释放的 reclaiming 继续占用。Agent 还有进程内 RunRegistry 默认 8 活跃控制实例的上界。                                                                                    | 任务已受理，显示排队；等待或暂停确认释放后才可用下一槽。                                                                 |
| 当前 Worker 和执行权 | 当前实现仍需 Worker 以正确 ID/token 经内部 HTTP 领取推进。Run Lease 默认 15 秒。Agent 仍受整库执行所有者锁和共享依赖可用性约束。                                                                                                                 | Worker 缺失或凭据错时可出现已接单无人推进。租约超时、运行者退出或旧 epoch 会触发恢复、暂停、待核对，必要时挡住重复动作。 |
| Runtime 执行期限     | 常规持久 Run 默认活动执行预算 600 秒，可由请求 `constraints.timeoutSeconds` 缩短。排队和人工等待有各自语义。模型调用、数据库查询、工具还有各自较短的截止。                                                                                       | `run_timeout`、`model_timeout` 或工具超时；放宽一个期限不会改变其他层限制。                                              |
| 回收与流状态         | 未确认的模型/工具副作用、未释放的 Sandbox、Checkpoint 或当前工具目录绑定失配、Redis 数据回退，均可触发安全阻断。                                                                                                                                 | 需要人工核对或重载历史。上层 202 和显示在队列中都不保证可立即领取。                                                      |

来源见 [RunScheduler](../../../apps/agent/src/compass_agent/runtime/run_scheduler.py#L70)、[配置默认](../../../apps/agent/src/compass_agent/settings.py)、[持久运行说明](../../../docs/lesson-20-production-runtime.md)。

### 3. 模型授权、路由、配置与预算

| 控制层                | 配置或边界                                                                                                                                                                                                                                                                                                                                | 常见表现                                                                                                                                            |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Chat 模型是否可执行   | 账号的 `models.invoke`、Provider 的启用/Chat 可见、适配器类型、模型能力、当前绑定版本、端点白名单 `COMPASS_MODEL_ALLOWED_ORIGINS`、正确 API Key 和上游授权。工具任务还需要 tools 能力。                                                                                                                                                   | `forbidden`、`model_unavailable`、`capability_not_supported`、`endpoint_not_allowed`、上游 `model_authentication_failed`。                          |
| 分类和主模型路由      | `COMPASS_ROUTING_MODE` 以及 JEV/Flash/Pro Provider 绑定。未显式绑定时解析数据库里的启用候选。固定主模型仍经过分类；受控执行当前使用 Flash 分类，自动主模型按场景选 Flash 或 Pro。                                                                                                                                                         | 缺分类模型或绑定会在任务路由阶段失败，即使用户选择的主模型有效。自动选择还可能落到已隐藏或不具备所需能力的 Provider。                               |
| 单进程模型容量        | `COMPASS_MODEL_MAX_CONCURRENCY=8`、`COMPASS_MODEL_MAX_INFLIGHT=32`。前者限制实际尝试，后者含排队。清理未确认的连接会隔离占槽。上游仍有自己的供应商并发、限流和额度。                                                                                                                                                                      | `runtime_overloaded`、等待超时、供应商 `model_rate_limited`，与 Run 入队 429 分开归因。                                                             |
| 单 Run Token 与费用   | `COMPASS_CONTROLLED_AUTONOMY_ENABLED` 启用后，`COMPASS_AUTONOMY_TOKEN_BUDGET`、`COMPASS_AUTONOMY_COST_BUDGET_MICROUSD` 与 `COMPASS_AUTONOMY_MODEL_PROFILES` 生效。档案需唯一匹配 Provider ID、绑定版本、SDK 模型和 API Base，同时满足当前实现支持的适配器与生成参数。派发前按声明的输入上界加本次输出额度预留；未结算尝试按保守上界处理。 | `model_budget_unavailable`、`token_budget`、`cost_budget`、`usage_bound_violation`。厂商账单尚有余额也可能触发本地预算。                            |
| 单 Run 步骤和工具次数 | 当前常规上限 100 步，每步最多 4 个工具。受控执行默认最大工具次数按二者乘积计算。调用方的 `constraints.maxSteps` 可缩短。受控执行对相同查询、连续失败和无进展还有停机策略。                                                                                                                                                                | `step_limit`、`max_tool_calls`、`repeated_query`、`consecutive_failures`、`no_progress`。受控查询规则包括同一键最多两次、连续失败三次、无进展五次。 |
| 上下文和生成上限      | 可见问题+历史总量 32 KiB；模型输入序列上限 512 条/192 KiB，此外还受 Provider 上下文窗口、输入与输出 Token 上限、生成配置和结构化响应约束。应用常规模型输入默认 `max_tokens=8192`，受控路径为 2048，具体仍由调用和配置影响。                                                                                                               | `context_limit`、`input_token_limit_exceeded`、`output_token_limit_exceeded`、`output_truncated`、厂商协议不兼容。                                  |

源码 Settings 与根样例中的 Token/成本默认是 10,000,000 / 10,000,000 微美元。Compose 缺少环境覆盖时的后备值是 **160,000 Token / 500,000 微美元**，因此本地原生启动和容器部署可能不同。当前根 `.env` 文件启用受控执行，写入 10,000,000 Token、10,000,000 微美元并有 3 条预算档案。未核对在线进程的环境合并方式、Provider 数据库记录及这 3 条档案与实际路由结果是否匹配。

来源见 [模型仓储](../../../apps/agent/src/compass_agent/models/repository.py#L425)、[路由服务](../../../apps/agent/src/compass_agent/routing/service.py#L115)、[模型容量门禁](../../../apps/agent/src/compass_agent/llm/admission.py)、[预算与停止策略](../../../apps/agent/src/compass_agent/harness/autonomy.py)、[部署模板](../../../deploy/compose/compose.yaml#L108)。

### 4. 工具、数据和结果交付

| 控制层         | 配置或边界                                                                                                                                                                                                                                        | 常见表现                                                                                                         |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 工具目录与批次 | 声明最多 16 个、合计 64 KiB，单 Schema/厂商输入还有上限；必须在当前 Run 可见且通过参数校验。`COMPASS_TOOL_BATCH_MODE` 默认 serial，parallel 拒绝非并行安全工具。`COMPASS_TOOL_FAILURE_MODE` 默认 stop，某些前置失败将停止同一批次。               | `tool_catalog_limit`、`invalid_parameters`、`parallel_not_supported`、`tool_setup_failed`。                      |
| SQL 与数据权限 | 工作区须有可用且授权的数据连接，`COMPASS_RETAIL_ALLOWED_ENDPOINTS` / `COMPASS_DATA_ALLOWED_ENDPOINTS`、只读账户、表和字段可见、连接版本一致。Retail 的租户/区域与成本权限规则继续生效。PostgreSQL 查询工具限制只读 SQL、结果行数/字节和执行时间。 | 连接缺失、作用域或字段拒绝、过期连接、查询超时、结果超限。授权过滤导致合法空结果时，用户也需要看到可辨识的说明。 |
| Sandbox        | `COMPASS_SANDBOX_ENABLED`、控制面地址和凭据、镜像、挂载卷以及 `COMPASS_SANDBOX_HOST_ID/SLOTS`。默认关闭，槽数默认 16。开启时还受 Sandbox 与上游控制面容量。                                                                                       | 工具未出现、`sandbox_capacity`、`sandbox_storage_capacity`、动作超时或清理未确认而停住。                         |
| MCP 与工作流   | MCP 地址白名单、连接启用/可访问、所需连接、发现与调用期限、每连接并发及 Schema 版本。普通可选连接失败可跳过，必须连接缺失或已保存绑定变化可停止 Run。现有 Run 服务还拒绝已退役的执行契约。                                                        | 工具缺失、初始化失败、重放被阻断、`workflow_unavailable` 或待核对未知外部效果。                                  |
| 消息、流和产物 | Run 流和公开投影存在内容上限，当前 4 MiB 级；独立的答案、文件、工具结果等也有上限。SSE 重连依赖原 Redis 游标与正在投影的 SQL。                                                                                                                    | 已执行但结果不完整、`run_content_limit`、游标失效，需要回读 Message 或观察恢复状态。                             |

来源见 [工具目录](../../../apps/agent/src/compass_agent/tools/registry.py)、[SQL 工具](../../../apps/agent/src/compass_agent/tools/builtin/database_query/__init__.py)、[MCP 加载](../../../apps/agent/src/compass_agent/tools/mcp/service.py)、[Sandbox 分配](../../../apps/agent/src/compass_agent/tools/sandbox/allocations.py)、[公开流](../../../apps/agent/src/compass_agent/runtime/redis_stream.py)。

## 第 20 课方案将新增的失败或等待状态

| 阶段                 | 触发条件                                                                                                                                                          | 用户侧应看到                                                      |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| 保存计划与 Chat 操作 | 缺 `module.automations`、计划归属校验失败、Cron/时区无效，固定模型无效，计划名歧义，计划版本冲突，未确认或确认过期。Chat 管理另依赖本次对话本身能启动模型和工具。 | 对应字段、权限或确认提示。模型不可用时仍可走授权的 Web 管理入口。 |
| 计划到期             | Scheduler 停机、失租、配置不符，夏令时不存在的本地时刻，漏跑合并、原交付或 Run 尚未结束，计划修改或停用。                                                         | 区分还未扫描、跳过、正在交付。停机积压与业务跳过分别展示。        |
| 交付前或交付中       | 发起人/工作区权限变化、Scheduler 服务凭据错误、令牌过期，Agent 429 或暂不可用，触发达到次数或时间期限。当前设计初值最多 8 次、自发现后 30 分钟。                  | 暂时受限时展示重试时间；永久拒绝展示原因；响应未知时保留待核对。  |
| 已交付 Run           | 仍经过与即时提交相同的队列、模型、预算、工具和恢复链路。计划保存后 Provider 或连接可能发生变化。                                                                  | 保留已受理 Run ID，单列执行结果及失败阶段，按权限链接原会话。     |

## 先查的配置和展示问题

1. **模型权限和预算档案分开查。** 先核对当前用户的 `models.invoke` 与模型是否启用且可见，再核对实际分类和主模型是否均有匹配的预算档案。当前本地配置文件已经启用受控执行。档案条数与余额都不能代替逐个路由绑定核对。
2. **把任务满分成 Run 积压、Run 执行槽、模型容量和上游限流。** 当前 Web BFF 对 429 给通用任务满提示，403 也常为通用权限不足。模型调用失败还可归并成通用的模型失败。建议本课明确哪些稳定原因码和动作须留给普通用户，租户用量及敏感细节继续走已授权诊断。
3. **失败条目保留阶段和恢复动作。** 交付记录可显示是否已受理、下次重试及原因。Run 显示排队、待人审、待核对、最终失败和 SSE 断开。用户按阶段选择改权限、换模型、缩小任务、重试或回到原会话。无需以统一放宽上限作为处理方式。
