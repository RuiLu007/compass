# Agent

从原 `apps/agent-api` 迁入，定位为 Agent Runtime 与推理相关业务应用。FastAPI + SQLAlchemy + psycopg + Alembic，Python 包名 `compass-agent`，导入入口 `compass_agent`。`package.json` 仅将 Python 命令暴露给 Turbo；Python 依赖以 `pyproject.toml` / 根 `uv.lock` 为准。

从工程根运行 `pnpm dev -F agent`，统一加载根 `.env`。本目录 `turbo.json` 为构建和静态检查声明 Python 工具链缓存指纹；开发、迁移及 seed 不缓存。

- `/healthz`：进程存活，无数据库依赖。
- `/readyz`：数据库连接、演示、模型、数据连接和工作流资产表可用性，未迁移至 0015、Runtime 密钥或 Redis 不可用、运行协议或共享策略失配时返回 503。
- `/v1/demo/overview`：固定合成数据聚合；默认关闭，启用后仍只读。空数据或数据库故障返回 503。

Feature 993 增加进程内 LLM Runtime、平台模型 Provider 配置、能力测试和 Chat 调用。`models/` 拥有配置与元记录，`llm/` 统一参数、适配器、结果和错误；`api/models.py` 提供内部 HTTP 入口。账户、租户与权限由 Web / BFF 拥有，每次调用先完成短事务鉴权，再携服务令牌及可信上下文调用 Agent。Agent 校验上下文与 Provider 状态，不访问 Web 业务表。Feature 989 在此基础上增加进程内 Run / Step、顺序 ReAct、固定 Retail 查询、SSE 进度、CSV 与在途终止；第 16～18 课补充计划预算、Workflow、统一工具调度、MCP 与独立 Sandbox；第 19 课接入持久 Session/Run、Redis 投影与人工确认，见 [运行说明](../../docs/lesson-19-durable-runtime.md)。

数据库迁移唯一源为 `migrations/`，受管入口和 SQL 交付见 [agent/main](db/main/README.md)。首次运行 `pnpm db:provision -F agent`，再执行 `pnpm db:migrate -F agent`。供应、迁移与运行凭据分离；`pnpm db:seed -F agent` 使用运行账号幂等写入三天虚构数据。

单元测试使用内存 SQLite 验证聚合、seed 幂等、开关和错误行为；PostgreSQL 差异通过真实迁移、seed 和根 `pnpm smoke` 验收。迁移与运行初始化分开，模块导入和 OpenAPI 导出不会连接数据库。

## 模型接入

在根 `.env` 设置 `COMPASS_AGENT_SERVICE_TOKEN`（Web 与 Agent 一致）、`COMPASS_MODEL_ENCRYPTION_KEY`（Fernet 密钥）和 `COMPASS_MODEL_ALLOWED_ORIGINS`（逗号分隔的精确 HTTPS origin）。可分别用 `uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'` 与 `uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` 在本机生成前两项，妥善保存。密钥留空时模型入口返回不可用，既有工程演示保持独立。

模型凭据从平台管理页面录入，数据库只保存密文。加密密钥须与数据库备份独立保存；直接替换加密密钥会使旧配置无法解密。本期没有在线密钥轮换工具。

目录读取锁定版本 LiteLLM 的本地清单，并补充课程重点模型及 System One 决策预设。DeepSeek API 标识为 `deepseek-v4-pro`、`deepseek-flash`；别称可使用课程名称。阿里云决策服务配置工作区 Base URL `https://<工作区>.<地域>.maas.aliyuncs.com/compatible-mode/v1`；JEV 官方使用 `https://api.typesafe.ai/v1`。通用 `SystemOneDecisionAdapter` 追加 `/systemone`，两家均需 Bearer API Key，并将对应 origin 加入允许列表。目录含 `aliyun/decision-model-preview` 与 `typesafe/jev-latest` 预设，也可手工使用 `aliyun/<model>`、`typesafe/<model>`、`systemone/<model>`，API 模型名无白名单。支持 Choice/Noul/Score、结构化指令及判据、实际输入/输出用量；图片与 Box 实验能力暂未开放。升级前备份 Agent 库与加密密钥，运行 `pnpm db:migrate -F agent` 到 0003 后启动新版应用，旧阿里云配置自动保留。平台测试和 Chat 复用 LiteLLM 支持的生成模型协议，决策模型在管理端测试。各租户管理员须显式授予 `models.invoke`，旧权限模板不会自动获得该动作。

每个生成调用执行一次 LiteLLM SDK 推理调用，关闭重试与 fallback；认证及协议交互由 SDK 处理，超时上限 180 秒；取消与错误保留安全元记录，不保存对话正文。工具测试只接收手工填写的结果，Thinking 只显示开关与强度元信息。复杂路由、重试、Fallback、代理服务和 Agent Loop 未包含在本期。

V2 按 [Runtime 目录说明](src/compass_agent/llm/README.md)和[模型应用服务说明](src/compass_agent/models/README.md)划分生命周期：Runtime 管模型执行、绝对期限、取消和资源关闭；ModelService 管配置快照、凭据与调用记录；HTTP 层管 SSE 队列和事件编码。两类适配器实现同一类型化入口，Runtime 可通过 FakeAdapter 独立验证。数据库已升级至 0003，公共 HTTP DTO 与事件名保持兼容。

验证入口：`pnpm lint -F agent`、`pnpm typecheck -F agent`、`pnpm test -F agent`、`pnpm test:integration -F agent`。最后一项需要独立 PostgreSQL 配置，见数据库说明。`tests/e2e_app.py` 是显式测试宿主，以真实 API/数据库配合受控上游 HTTP 验证 Web 调用链；生产入口使用 `compass_agent.main:app`。真实供应商验收另外使用已授权账号与密钥，状态见 [993 实施记录](../../mai-specs/993-lesson-11-llm-runtime/IMPLEMENTATION.md)。

## 离线业务数据准备

完整零售业务数据由独立的 [tools/data-seed](../../tools/data-seed/README.md) 生成、校验和发布，使用 `pnpm data:seed`。工具及其零售迁移、测试已移出 Agent；Agent 包和运行镜像只携带自身代码。

`AGENT_API_URL` 为 Web 的内部 HTTP 接口地址配置，Compose 内服务地址为 `http://agent:8102`。统一 Dispatcher、个人 MCP 与本地 Sandbox 控制见下文第 18 课；Memory Tools 按后续切片接入。

本期顺序执行内核位于 `src/compass_agent/runtime/`；已交付的单次模型调用仍位于 `src/compass_agent/llm/runtime.py`。持久任务、状态、恢复与人工确认由 runtime 内部模块分工管理；模块职责和目录规则见 [LLM Runtime 说明](src/compass_agent/llm/README.md#目录演进)。

## 第 15 课 Agent Run

`POST /v1/analysis/runs` 返回 `compass.run` v1 SSE，首包在任何外部派发前提供 Run ID；`POST /v1/analysis/runs/{run_id}/cancel` 受理同一所有者的停止请求。执行内核位于 `src/compass_agent/runtime/`，最多 20 个模型 Step、每步 4 条顺序工具调用，Run 总期限 180 秒。原模型管理、诊断和单次 Chat API 保持原契约。

默认工具为 `todo_write`、授权连接可用时的 `database_query` 和沙箱可用时的 `python_analysis`，另按个人配置发现 MCP。模型通过表、字段和只读 SQL 自主查数，按需执行 Python。数据连接仓储位于 `data_connections/`，保留已有配置、凭据、租户限制和默认连接初始化。普通 PostgreSQL 连接通过 `COMPASS_DATA_ALLOWED_ENDPOINTS` 配置允许端点，历史 Retail 连接沿用专用端点和只读身份。当前 MySQL 仅支持配置与连通性测试。
开发配置、课堂问题、对照数字、终止与真实 PostgreSQL 验收见 [课堂复现](../../docs/lesson-15-agent-run.md)。执行内核选择与后续边界见 [ADR 0002](../../docs/adr/0002-agent-execution-kernel.md)。

## 供应商协议与连接配置

生成模型复用当前锁定的 LiteLLM Core 协议和供应商注册表，目录包含 SDK 的 Chat / Completion / Responses 桥接模型，支持手工 `provider/model`。DeepSeek 的两款型号和阿里云决策模型是课程重点联调对象；接入与 Chat 没有型号白名单。SDK 负责原生协议、参数映射、认证、响应和流事件归一，Compass 负责配置/权限、期限/取消、可见结果边界及元记录。

Base URL 可留空使用 SDK 默认端点；自定义端点须在 `COMPASS_MODEL_ALLOWED_ORIGINS` 中配置 HTTPS origin，路径按供应商接口填写。API Key 可留空，使用供应商原生认证或 SDK 服务端凭据。添加窗口的供应商连接参数 JSON 直接承接 SDK 连接字段，例如 `api_version`、`aws_region_name`、`aws_access_key_id`、`aws_secret_access_key`、`vertex_project`、`vertex_location`、内联 `vertex_credentials`；已声明 boto3、google-auth、azure-identity 原生认证依赖，以及 Responses 桥接所需的 mcp 2.x；其他供应商可选依赖按 SDK 文档安装。执行参数、SDK 客户端/回调、模型绑定及重试字段由应用维护。

连接参数与 Key 使用已有 Fernet envelope 加密保存，旧密文兼容；接口仅返回是否配置。编辑留空保留，连接参数 `{}` 清空。SDK 自有连接池由 SDK 管理，Runtime 关闭自己持有的流。原生非流式响应由 SDK 缓冲，应用校验归一后结果和续接大小；旧 DeepSeek 分支保留锁定 SDK 版本的 HTTP 守卫，原生协议不套用 OpenAI 的流结束格式。

## 两级意图与模型路由

`POST /v1/analysis/runs` 是业务推理入口，根据场景与任务特征选模，再通过 Agent Runtime 推进模型和工具。请求通过 `selection` 选择固定 Provider 或自动路由，服务端通过 `run.routed` 返回实际模型、场景和工具可用状态。旧 `POST /v1/models/calls` 已下线，Provider 诊断和 `GET /v1/models/calls/{call_id}` 继续可用。

业务问题使用同一通用工具装配。Agent 在执行时检查当前工作区的授权连接，以及实际选中的模型是否支持工具调用。连接在 Web 的「数据连接」管理，模型工具能力在「平台模型管理」查看。纯闲聊仍引导回经营分析，明确的经营问题可执行已授权查询。

[路由模块说明](src/compass_agent/routing/README.md) 登记角色配置、字典派生和次数/时限。`SC-39` 仍使用 Flash。路由模块沿用既有模型与调用表。

## 第 13 课可靠执行

主生成最多两次尝试，分类/诊断/决策各一次；整轮 Chat 共用 180 秒父期限。进程默认 K=8、N=32。已输出片段的中断不自动重试，失败片段不进入后续上下文；取槽后到期正常形成超时摘要，关闭未确认时隔离执行槽。

平台管理员的 `/v1/models/reliability/scenarios` 和 `/v1/models/reliability/runs` 提供固定模拟演示，运行真实 SDK/Runtime/执行器，无真实供应商出站或业务表写入。入口由 BFF 平台权限及可信上下文保护；运行每进程最多一个、最多 20 条调用/20 秒。详见 [演示模块](src/compass_agent/reliability/README.md)。可靠性摘要由 0004 引入，当前数据库版本为 0013，升级和恢复见 [agent/main](db/main/README.md)。

## 第 14 课调用观测

Agent 使用 OTel 1.45.2 显式关联请求、分类、路由、逻辑调用、排队、尝试和收尾；观测为可选的进程内异步路径。服务鉴权后提取 W3C carrier，多实例通过唯一 instance_id 和同一项目/Trace 关联，Provider 请求不透传内部上下文。状态与固定模型聚合接口执行平台 manage 权限，普通用户只通过响应头获取本轮编号。

模块、采集边界和测试见 [observability](src/compass_agent/observability/README.md)，本地后端、凭据和持久卷见 [运行说明](../../deploy/observability/README.md)。调用记录由 0009 扩展持久尝试与加密内容；观测样本不承担正式账单或强审计职责。

## 第 16 课受控分析

分析 Run 支持进程内 Todo、逐次模型预算和停止策略。启用与课堂演示步骤见 [第 16 课运行说明](../../docs/lesson-16-controlled-autonomy.md)。

通用 Todo、数据库查询与 Python 分别位于 `tools/builtin/` 下各自的同名目录。`harness/AgentExecutor` 统一推进模型循环、调用工具和回填消息；`runtime/RunService` 装配路由、工具、运行状态与结果检查。可选核验钩子由调用方注册。最终分析正文来自模型回答，工具结果和下载产物独立发布。

服务端装配 `RunService` 时，可用 `verifiers` 注册全局检查，用 `task_verifiers` 按展示任务或场景 ID 注册检查。代码调用 `run(..., verifiers=(check,))` 可追加单次检查。钩子接收候选结果的副本和可信调用上下文，可抛出 `VerificationRejected` 拒绝。钩子应只读且可重复执行，持久 Run 恢复时可再次核验。默认钩子列表为空。

## 第 17 课混合工作流

第 17 课的两个 Chat 预置分别用于探索并保存、换周期复盘。模型调用 `create_workflow` 提交 JavaScript 编排、函数参数 JSON Schema 和试跑入参。QuickJS 运行脚本，`sub_agent` 在独立进程内使用自己的上下文和授权只读查询。用户确认后源码与工具契约写入个人资产库，新的 Run 按当前主体加载可用资产，由模型决定是否调用并生成新参数。

在「插件 → Workflow」查看源码、改名、启停或导出。旧零售契约资产保留管理和导出。课程场景资料见 [Showcase 目录](../../examples/showcases/README.md)。

切换版本前运行 `uv run --env-file .env --package compass-agent python apps/agent/scripts/check_runtime_upgrade.py`。有受影响的非终态 Run 时，该只读检查退出 1；先在旧版本完成或通过现有控制接口明确取消，再切换。该脚本不修改 Run 或用户资产。

## 执行过程公开投影

`runtime/execution_trace.py` 持有本次 Run 的统一执行节点，将主循环、现有工具、Workflow、Skill 和临时 SubAgent 的安全状态发布为 `compass.execution.node@1`。来源、环境和并行关系有独立字段，完整参数、私有推理与工具观察仍留在执行侧。QuickJS 任务函数的派发可携并行 groupId；完整工具执行及异步调度上下文在第 18 课完善。当前节点无需新增迁移，节点由第 19 课共享事件协议保存并恢复。

## 第 18 课统一工具执行

运行与配置见 [工具执行说明](../../docs/lesson-18-tool-runtime.md)。`routing/` 管意图、模型和确定性任务分流，`runtime/` 管 Run 生命周期及应用装配，`harness/` 管通用模型循环、上下文、系统提示词和核验钩子，`workflows/` 管理动态编排、执行与资产。[工具目录](src/compass_agent/tools/README.md)区分 `tools/builtin/` 通用工具与 `tools/business/` 业务工具，每个工具独立目录并在 `tools/catalog.py` 统一注册；`tools/mcp/`、`tools/sandbox/` 拥有远端连接和执行后端。

0008 增加个人 MCP 配置与 Sandbox 分配租约。OpenSandbox + NFS-Ganesha 本地启动入口为 `pnpm sandbox up`。工具的环境、副作用、审批和批次策略分别配置；审批与原 Run 接续由第 19 课持久服务处理。

第 20 课多副本消费、Scheduler、0015 迁移与恢复入口见 [运行说明](../../docs/lesson-20-production-runtime.md)。

会话首次受理时保存由首条消息生成的临时标题。Agent 后台用已选模型或 Auto 的 Flash 模型生成短标题，持久化到会话。手动改名优先，已结束会话支持软删除。数据库升级与恢复见 [agent/main](db/main/README.md)。

## 通用 Agent 装配

目录边界见 [Harness](src/compass_agent/harness/README.md) 和 [Runtime](src/compass_agent/runtime/README.md)。当前执行入口统一为 `runtime/service.py`；HTTP 路径中的 `/analysis/` 保持兼容。

`AgentDefinition` 提供系统提示词、`RegisteredTool` 集合和启用的内置能力。Runtime 默认定义为空工具集；产品 HTTP 入口通过 `compass_definition()` 显式装配 Todo、数据库、Python 与 Workflow 能力。任务可以提供自己的提示词与工具，个人 MCP 工具继续通过同一 Registry 加载。单纯使用自定义工具的 Agent 不加载 Retail 连接。

课程案例与手输问题使用同一请求结构和模型循环。新请求拒绝 showcase 和 constraints.verification。已受理的旧请求由仅回读回执的兼容边界处理；旧历史解码不重新启用已移除能力。通用工具策略、审批、预算和持久恢复继续共用 Runtime。
