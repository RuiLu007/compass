# Feature 989 实施与验证

2026-11-10 初版实施记录。有效需求为 [PRD](spec/PRD.md) 的 AC-01～AC-15，实施依据为
[TECH_DESIGN](design/TECH_DESIGN.md) 及其补充设计。本次已完成 Agent、BFF、Web Chat、生成契约、
真实 Retail PostgreSQL 验收入口和课堂材料；仓库内自动化门禁全部通过。真实发布库、真实模型、
浏览器和容器链路因当前环境缺少专用凭据与运行服务，保留为发布前环境验收，不计作已通过。

## 入口收口与复验

- Web 的 `POST /api/web/v1/analysis/runs` 和 Agent 的 `POST /v1/analysis/runs` 对应同一业务推理入口。两层旧单次推理 POST 均已移除，生成契约随源更新。平台 Provider 诊断和模型调用记录查询继续可用。
- 工具轮续接保留助手消息中的空字符串，确保工具调用轮与封存的消息摘要一致。分析 Chat 在已选 Provider 失效时刷新可用列表，并恢复本次问题草稿。
- 分析 Chat 从 Run 的安全响应头读取观测编号。各模型 Step 的调用信息可复制本次 Run 的观测编号与当前 Step 的模型调用编号。
- 隔离环境已完成单元、PostgreSQL 集成、真实 DeepSeek 浏览器分析与 CSV 下载，以及浏览器触发的真实 Retail SQL 停止和后继查询。详细场景与限制见[测试验收](test/TEST_PLAN.md)。
- 续验覆盖旧课程包在空 Retail 库的全新发布、五应用在独立 Compose 项目的空库部署及真实 Langfuse。PostgreSQL 健康检查改为 TCP 探测，避免初始化临时实例触发后续迁移。

## Chat 阅读体验调整

分析 Chat 用简短旁白提示运行进度，工具卡按执行顺序显示查询动作、状态、时段和结果。模型与工具的调用编号可从流事件取得，观测编号可从响应头取得。成功终态经过完整流校验后，用 React Markdown 和 GFM 渲染回答，追问仍使用回答原文。

Web 的 lint、类型检查、100 项单元测试和生产构建通过。浏览器复验覆盖 Markdown 语义、窄屏滚动、工具顺序、过程折叠与焦点、停止反馈和 CSV 下载；观测用例通过受控模型与独立 PostgreSQL 核对完成和取消的调用关联。

## 交付

| 任务 | 状态 | 实际结果 |
| --- | --- | --- |
| T1 · 模型接缝 | complete | 路由选择与生成解耦；Provider binding version 重验；Agent 分发模式保留未知/无效工具给执行器回填；模型取消携带清理回执；Run 观测禁用正文并将生成阶段记为 `inference`。 |
| T2 · Retail | complete | 单一 `retail_sales` Schema、固定参数化 SQL、日期/形状/重复键前置拒绝、独占异步只读连接、实际发布角色与 RLS 核对、`cancel_safe` 有界清理、类型化失败、商品 CSV。 |
| T3 · Run 与契约 | complete | 进程内 RunRegistry、有限 ReAct、Run/Step/Tool 状态、8 个活动 Run、180 秒期限、唯一终态、断流/停止/关机收口、`compass.run` v1 SSE 与独立取消 API。 |
| T4 · BFF | complete | 复用会话、工作区、`module.analysis`、`models.invoke` 和 CSRF；授权事务结束后请求 Agent；双侧严格 SSE/Schema/顺序/EOF/文件校验、重新编号、超时与上游中止。 |
| T5 · Chat | complete | 分轮工具卡、模型调用关联、最终回答独立渲染、一次自动折叠与焦点回收、CSV 下载、成功历史、提前停止、重复停止、断流和清理未确认状态。 |
| T6 · 课堂与发布 | complete（材料） | 环境示例、Compose 透传、Agent/Web/部署说明、[ADR 0002](../../docs/adr/0002-agent-execution-kernel.md) 与 [课堂复现](../../docs/lesson-15-agent-run.md) 已交付；真实课堂演示待外部环境。 |
| T7 · 验证门禁 | complete（仓库） | 单元、BFF/协议、客户端竞态、E2E 夹具和真实 Retail 专项入口已补齐；全仓 check/build 通过。真实 PG、模型、浏览器与容器验收待执行。 |

## 关键实现边界

- Agent 在任何分类、模型或 SQL 派发前发送 `run.started`。首帧发送失败、首帧后断连和正常断流都释放 Run 所有权；发送回压不阻塞独立断流监听。
- `RunControl` 在无 `await` 片段内登记阶段、受理停止和提交唯一终态。deadline、用户停止、断流和关机共用清理路径；未取得可信清理回执时只允许 `cleanup_unconfirmed`。
- Retail 工具不接受 SQL、DSN 或数据身份。Web scope 必须与部署绑定精确匹配；每次连接核对 `session_user`、`current_user`、只读事务和事实表 RLS。
- 发布工具中的 `a_east` 是 profile；实际登录角色带发布前缀并以 `_a_east` 结尾。配置保存并核对完整发布角色，而不是硬编码字面 `a_east`。
- CSV 只来自本 Run 最近一次成功、非空的商品查询，最多 200 行 / 128 KiB，UTF-8 BOM、CRLF，并防护公式起始字符。失败或停止终态不发布候选文件。
- BFF 和浏览器各自重新验证流。只有唯一成功终态后紧接干净 EOF 才提交回答、文件和追问历史；取消已受理后出现 `completed` 会被当作协议错误拒绝。
- 当前 Run 注册表位于单 Agent 进程；部署保持单副本。持久恢复、并行工具、统一 Tool Executor 和 Sandbox 仍按后续课次处理。

## 初版验证结果

| 检查 | 结果 |
| --- | --- |
| `pnpm contracts && pnpm contracts:check` | 通过；`openapi.json`、模型 bundle、Run Stream Schema、Web 类型和有限流常量与 Python 源一致。 |
| `pnpm check` | 19/19 任务通过；工具检查 46 通过 / 13 跳过，Agent 239、Web 99、Data Seed 34、Home 21、Memory 1、Worker 2；格式、lint、类型、契约与工作区边界均通过。 |
| `pnpm build` | 7/7 任务通过；Agent/Memory/Worker/Data Seed Python 包及 Home/Web Next.js 生产构建成功。 |
| `apps/agent/tests/test_agent_run.py` | 25 通过；覆盖多轮/同轮多调用、参数拒绝、CSV、预算、首帧、容量、唯一终态、取消竞态、deadline、清理回执、日志与观测阶段。 |
| Web Run 测试 | 客户端专项 15 通过；全量 99 通过。覆盖严格流、文件来源、历史、折叠/焦点、提前停止、重复/完成竞态、非法成功终态和页面清理。 |
| Retail 专项收集 | `test_agent_run_integration.py` 成功收集 2 项：真实 channel→product 金标；锁等待中的查询取消、后端退出和后续正常查询。 |
| 静态差异 | `git diff --check` 通过；Prettier、Ruff、ESLint、Pyright 和 TypeScript 均由全仓门禁覆盖。 |

本机 `.python-version` 指向尚未安装的 3.13.16，而仓库 `.venv` 为 3.13.15；以上根命令显式使用
Node 24.12.0、Corepack pnpm 11.3.0、`UV_PYTHON=<repo>/.venv/bin/python` 和 Turbo loose env
执行。首次未显式指定解释器的尝试被系统 `/usr/local/bin/python` 2.7 拒绝，不计入代码失败。

## 初版 AC 对照

| AC | 代码与自动化证据 | 发布前环境验收 |
| --- | --- | --- |
| 01 | 有限 ReAct、真实 SQL 实现、channel→product 专项金标、Web 多轮夹具 | 用发布 Retail、可用 Tools 模型和浏览器跑完整两轮并核对数字 |
| 02 | Agent 用例核对同轮两个 ToolCall 的顺序、原 ID、观察消息和续接绑定 | — |
| 03 | 未知工具/无效参数零连接、空数据/查询错误分型、只读绑定测试 | 用真实受限角色注入一次数据库失败 |
| 04 | 模型失败、不完整输出、分发异常和停止后零后续派发用例 | — |
| 05 | Run/Step/调用 ID、隔离状态、安全枚举日志与哨兵值用例 | 课堂按 Run ID 对照实际结构化日志 |
| 06 | `pnpm check` 回归既有模型、路由、管理与观测路径 | 使用课堂 Provider 回归真实调用诊断 |
| 07 | ADR、架构、配置、对照 SQL、正常/故障/终止说明齐全 | 完成课堂浏览器演示 |
| 08 | BFF 授权、可信上下文、越权零 Agent 调用、不合规事件拒绝与 `no-store` 用例 | 用课堂账号/未授权账号复验 |
| 09 | Web 99 项含卡片顺序、原卡更新、折叠、键盘焦点、零工具和成功历史 | 运行 Playwright 页面旅程 |
| 10 | Agent CSV 字节/行数/来源/公式安全，BFF 与客户端文件校验及 URL 清理 | 浏览器下载真实本轮 CSV 并比对发布库 |
| 11 | 双侧断流、非法 EOF、失败终态、上游 abort、未提交回答/文件/历史用例 | 切断真实浏览器连接并观察 Agent 资源 |
| 12 | 受控模型取消、提前停止只发一次、槽位/流/未提交结果回收用例 | 在真实模型生成期间从 Chat 停止 |
| 13 | 真实 PG 专项已实现：表锁制造活动查询，以观察连接核对 PID 退出并执行后续正常查询 | 提供两个 `AGENT_RETAIL_TEST_*` URL 后实际执行 |
| 14 | Agent/BFF/客户端覆盖跨主体、重复停止、停止先受理、完成先提交及唯一终态 | — |
| 15 | 清理超时/异常形成 `cleanup_unconfirmed`，未确认连接不复用，后续健康资源用例 | 在受控真实后端复验故障注入 |

## 初版未执行的环境验收

当前进程中 `AGENT_RETAIL_TEST_DATABASE_URL`、`AGENT_RETAIL_TEST_ADMIN_URL`、
`WEB_TEST_DATABASE_URL`、`AGENT_TEST_DATABASE_URL` 和 `COMPASS_RETAIL_RUN_READONLY_DSN` 均未设置，
因此没有执行以下项目：

1. `pnpm test:integration -F agent` 的真实 Agent / Retail PostgreSQL 与模型集成；
2. `pnpm test:e2e -F web` 的真实 BFF、浏览器、模型和下载旅程；
3. `pnpm smoke` 与课堂正常/模型停止/Retail 停止/故障演示。

本机 Docker daemon 未运行。Docker Compose 为 v2.22.0，其 `config` 在仓库既有的可选
`env_file` 对象语法处报 `services.web.env_file.0 must be a string`，早于本期新增的两个简单环境变量；
因此未把 Compose 解析或容器运行记为通过。需在支持该既有语法的 Compose 版本和可用 daemon 上复验。

## 初版自检结论

- 需求涉及的代码、生成契约、跨应用接入、自动化入口和课堂材料均已实现，无占位逻辑或调试残留。
- 两轮查询、停止与清理、严格流、文件和历史的核心分支有仓库内自动化；后端独立复核无剩余 P0/P1/P2。
- 实现没有降低 AC；依赖真实发布源、Provider、浏览器和容器的项目明确保持待验。
- 未更新 `BRIEF.md` Phase 或 `ROADMAP.md`，由后续编排阶段统一维护。

