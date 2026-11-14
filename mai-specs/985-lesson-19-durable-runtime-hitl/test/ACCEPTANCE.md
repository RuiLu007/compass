# 第 19 课验收记录

[测试计划](TEST_PLAN.md) · [PRD 验收标准](../spec/PRD.md#8-验收标准) · [返回需求简报](../BRIEF.md)

## 最终结论

2026-11-12：第 19 课 AC-01～AC-19 全部验收通过，单元测试、集成测试、端到端业务、真实进程故障、源码构建和五应用容器验收均完成，当前无待验业务项。最后补充的真实 HTTP 410→浏览器自动 Message 重载及可重复客户端回归均已通过。

最终 `pnpm check` 19/19 任务、`pnpm build` 7/7 任务通过。Agent 单测 340 项、Web 单测 119 项、Web PostgreSQL 集成 22 项通过。Agent 集成先执行全组 90 项（89 通过、1 项夹具失败），修复后以定向复验和新增真实并行进程测试完成当前 91 个案例的通过覆盖；通用 E2E 全组 56 项先有 54 通过、2 项测试等待失败，两项修复后定向复验通过，56 个案例全部闭环；另新增 1 项活跃流丢失客户端 E2E 通过，当前 57 项均有通过结果。加真实 Tool 2 项与 Workflow 1 项，业务浏览器用例合计 60 项；另有 16 项持久矩阵 Web validator/reducer 验证。各次命令和结果分开登记如下。

真实业务专项验证了原图表与已清理 Sandbox 的重启审批、一次 MCP 效果、实际模型历史隔离、Web 撤权、模型未知用量、Redis/SQL 故障与并行子结果交付窗口。最新 16 类持久矩阵在 Web 同 Schema/reducer 下全部通过；真实 Tool 2 项、Workflow 1 项及 Langfuse 观测通过。模型响应使用确定性 fixture，其余按下表执行真实组件。

根工具测试共 61 项，48 通过、13 跳过；跳过原因是可选 formatter hook 资产缺失，单独保留为工具层环境边界。本课业务验收没有跳过项。历史失败及修复过程保留在后文，最终状态以本节和最终闭环记录为准。

工作区包含第 15～18 课原有未提交基线，本轮验收保留这些修改；本报告只维护测试结果。

## 当前验收环境

| 对象        | 当前设置与边界                                                                                                                                                                      |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web / Agent | 最终隔离 Web 8134、Agent 8135；旧测试 8130/8132 已由主任务停用。原用户 Web 8100 保留。                                                                                              |
| 数据与配置  | 最终 Agent 库 `compass_agent_test_l19_e2e_final`，Web 使用独立测试库；配置由 `work/lesson-19/run-final-env.py` 注入，私有 `final-env.json` 不公开。会重置库的集成使用其他隔离环境。 |
| 真实依赖    | PostgreSQL 55432、本课 Redis、Retail 只读小样、OpenSandbox 8112/NAS、MCP 8129。AC-03 的 SQL、Python、文件和 MCP 效果均经过真实组件。                                                |
| 模型        | Tool/Workflow 功能场景使用确定性模型 fixture；模型派发与可靠执行专项使用 ProviderFixture。模型选择器沿用测试能力响应。                                                              |
| 操作职责    | 主任务管理进程、容器和数据库；独立测试执行浏览器业务操作及只读数据库/资源核对。AC-03 的 Agent SIGKILL 和重启由主任务执行。                                                          |

## 最终执行汇总

| 范围                    | 最终状态                     | 实际执行与复验记录                                                                                                                                                                                                                                                                                                                                                                                                  |
| ----------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 综合检查                | 通过                         | `pnpm check` 19/19 任务；Agent 单测 340、Web 单测 119。`check-delivery.log`。                                                                                                                                                                                                                                                                                                                                       |
| 根工具测试              | 48 通过、13 跳过             | 61 项；13 项依赖可选 formatter hook 资产，当前资产缺失。跳过单列，不计入通过；本课业务测试无跳过。                                                                                                                                                                                                                                                                                                                  |
| 全应用构建              | 通过                         | 最新 `pnpm build` 7/7 任务。`build-final-complete.log`。                                                                                                                                                                                                                                                                                                                                                            |
| Web PostgreSQL          | 通过                         | 3 文件、22 项。`web-integration-final.log`。                                                                                                                                                                                                                                                                                                                                                                        |
| Agent 全组与定向复验    | 当前 91 个案例全部有通过结果 | 全组 90 项：89 通过、1 失败，`all-integration-current.log`；失败为过期测试夹具违反 `expires_at >= created_at`。修正 PostgreSQL 时间夹具后，相关 3 项通过（45.71 秒），`boundary-expiry-process-retest.log`；新增真实 parallel process 案例使当前集合为 91 项。最新专项 6 通过、85 未选（30.96 秒），`integration-last-regressions.log`，覆盖观测、legacy、控制版本/竞争和并行退出。未选项沿用本轮全组对应通过结果。 |
| 通用 E2E 全组与定向复验 | 当前 57 个案例全部有通过结果 | 全组 56 项：54 通过、2 失败，`general-e2e-final.log`；foundation 总预算/外点击落点修复后，2 项定向通过（3.6 分钟），`foundation-final.log`。新增 410 客户端回归 1/1（5.7 秒），`composer-410-final.log`。当前含 Composer 18 项、模型 8 项及观测/权限/路由/可靠执行/导航。                                                                                                                                           |
| 真实工具 E2E            | 通过                         | 2 项、54.3 秒；Retail/Python/PNG、MCP 配置/审批/未知取消。`tool-e2e-final.log`。                                                                                                                                                                                                                                                                                                                                    |
| 真实 Workflow E2E       | 通过                         | 1 项、26.5 秒；动态生成/保存/管理/复用、预算拒绝、Skill 周报。`hybrid-e2e-final.log`。                                                                                                                                                                                                                                                                                                                              |
| 前序案例持久矩阵        | 通过                         | 最新完整 Agent 执行导出的 16 类真实业务帧，Web 同 Schema/reducer 16/16（1.9 秒），`durable-history-matrix-current.log`；另 AC-03 真实 HTTP 帧 1/1，`durable-history-ac03.log`。包括 Runtime 重建、终态删流、完整业务视图与调用计数核对。                                                                                                                                                                            |
| 真实重启与权限专项      | 通过                         | AC-03 原 Wait/Run/PNG、诊断 Attempt 不增加、MCP 一次效果、原生 PNG/CSV 字节；AC-12 六 Run 的模型实际历史与重置；AC-18 真实 Web 撤权与后继零派发。                                                                                                                                                                                                                                                                   |
| 边界与迁移              | 通过                         | 迁移 20 项、原生/SQL 历史回填、Artifact 写入/损坏、批准竞争、独立事件 HTTP、跨 Run 草稿、目录变更、Retry/Fallback；并行交付窗口真进程退出 1/1（23.58 秒），`parallel-process-crash-final.log`。                                                                                                                                                                                                                     |
| Redis / SQL / 模型故障  | 通过                         | AOF SIGKILL/OOM/所有者断开、效果提交后退出、模型派发后/部分文本后退出、SQL Artifact 写失败/投影回滚/文本频率均通过，已纳入本轮全组及定向复验。                                                                                                                                                                                                                                                                      |
| 最新五应用容器          | 通过                         | 最新镜像启动并 healthy，`container-up-complete.log`；smoke 退出 0，覆盖 Web readiness/身份、Web→BFF→API→PostgreSQL、Memory 脚手架、Home/资产，`container-smoke-complete.log`。                                                                                                                                                                                                                                      |
| Worker 与 seed          | 通过                         | Worker SIGTERM 正常退出 0、无 OOM，`worker-shutdown.log`；重复 seed 保持 3 天、397 单、实付合计 4000000。                                                                                                                                                                                                                                                                                                           |
| UI 桌面/窄屏/键盘       | 通过                         | 真实会话、审批、侧栏、过程折叠和 390 px；375/768/1440 px、短视口、焦点、键盘及导航交互均有通过结果。                                                                                                                                                                                                                                                                                                                |

## 当前 AC 状态与剩余验收

AC-01～AC-19 全部通过，剩余验收项为零。

| AC    | 状态          | 完成覆盖                                                                                                                                                       |
| ----- | ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AC-01 | 已完成 / 通过 | 持久 Run/Step/Attempt/Checkpoint 关联、真实模型 Retry、已记录事件重复交付不重复推进。                                                                          |
| AC-02 | 已完成 / 通过 | 控制版本竞争、批准/取消原子竞争、终态保护、状态与事件一致；Web 冲突按当前版本重试。                                                                            |
| AC-03 | 已完成 / 通过 | 真查询和 Python PNG、原 Sandbox 清理、等待时 SIGKILL、原 Wait/Run 批准接续；原文件 ID/字节/来源一致，诊断不重复。                                              |
| AC-04 | 已完成 / 通过 | 拒绝/到期/重复/错配/跨作用域、独立受信事件 HTTP、执行前撤权阻断新增效果。                                                                                      |
| AC-05 | 已完成 / 通过 | Pause/Resume/Cancel 保留原 Run 与过程；Checkpoint 损坏/不兼容拒绝；准备阶段和运行中取消收口。                                                                  |
| AC-06 | 已完成 / 通过 | 效果提交后真进程退出，原 Action ID 补回执，效果一次；未知效果保留待核对状态。                                                                                  |
| AC-07 | 已完成 / 通过 | 空库/旧库迁移、SQL 一致性、权限/恢复、历史 ModelAttempt 回填及既有数据保留。                                                                                   |
| AC-08 | 已完成 / 通过 | Artifact 同 ID 复用/冲突、hash/损坏、跨用户拒绝、事务写失败回滚、缺失依赖阻断及原生 PNG/CSV 字节。                                                             |
| AC-09 | 已完成 / 通过 | 首问/成功/失败刷新、唯一 Session/Run、侧栏地址、请求幂等和后台执行。                                                                                           |
| AC-10 | 已完成 / 通过 | 16 类真实业务 SSE/Message 经相同 Schema/reducer 完整比较，终态删流后恢复且调用数不增加。                                                                       |
| AC-11 | 已完成 / 通过 | Message 与增量接续、重复/乱序/断线恢复、App 切换；真实活跃流丢失 410 后浏览器自动重载 Message，保留问题/部分回答/过程，无重复订阅与提交；终态流过期 409 重载。 |
| AC-12 | 已完成 / 通过 | 实际模型输入核对两轮后刷新追问、重置边界、其他 Session/用户排除与跨用户 404。                                                                                  |
| AC-13 | 已完成 / 通过 | 真实 Retry/Fallback 与 Run/Step 关联、派发后/部分文本后 SIGKILL、未知用量占额与预算持久保留。                                                                  |
| AC-14 | 已完成 / 通过 | Todo/计划版本、Verifier 拒绝/修复/成功、预算停止原因和恢复观察/预算一致。                                                                                      |
| AC-15 | 已完成 / 通过 | Workflow/Skill/SubAgent 源码/版本/关系、同源草稿资产去重、saved/expired 卡刷新；并行交付前真进程退出后子调用不重复。                                           |
| AC-16 | 已完成 / 通过 | SQL/MCP/Python 成功、失败与未知结局持久化；目录变化阻断重放，原调用/资源回执/文件保留。                                                                        |
| AC-17 | 已完成 / 通过 | Redis 写入、SQL 投影、Artifact/关键事务故障及发送前退出；确认增量补投影、Outbox 补发、原 Checkpoint 保留。                                                     |
| AC-18 | 已完成 / 通过 | 分页、非法/重复/乱序/过期游标、Message/SSE 真实撤权与旧连接关闭；桌面/390 px/键盘通过。                                                                        |
| AC-19 | 已完成 / 通过 | 本地 Redis AOF 保卷重启、OOM、Outbox 中断、投影滞后、活跃流丢失、终态流过期及文本批量 SQL 计数。                                                               |

## 历史发现修复状态

以下章节按执行时点保留首轮失败与中间状态。首问 URL、历史动画、执行树顺序、取消收口、审批节点、BFF 过期回执、游标重载、真实撤权、模型切换与取消版本冲突均已修复并通过相应复验。最终全组中的 2 项 foundation 等待问题及 1 项 PostgreSQL 时间夹具问题也已闭环；最终 410 分支亦已通过真实链路与确定性客户端复验，当前无未修复业务缺陷或待验项。

## 后续结果记录格式

每次执行记录日期、源码基线与受影响未提交文件、测试入口、环境标识、退出结果或浏览器观察。业务案例附 Session/Run、Artifact/Action 标识、刷新比较、进程外调用计数及日志/截图位置。失败修复后保留原记录，并追加对应复验结果。

原始日志和测试凭据留在忽略目录；本文件只登记安全摘要和路径。

## 第二次静态准备检查

2026-11-12，主任务提供 `work/lesson-19/test-env.json`，并报告已将 `compass_agent_test_l19` 迁移至 0009。本次仅核对环境中的地址、库名、开关及现有代码接缝，未执行 UI、模型、迁移或容器故障。

- Web 8130、Agent 8132、MCP 8129、Redis 56379 检查时均无本机监听冲突；Web origin 和 Agent 授权复查地址匹配 8130，BFF 上游匹配 8132。
- 需要在测试子进程覆盖或补齐的项：默认 Retail seed 仍指向开发库；观测开关启用；L17 浏览器开关和 Sandbox 故障专项开关缺失。已回传主任务，未直接改私有配置。
- 现有 Workflow/Tool fixture 通过 `get_agent_executor` 注入 FakeModels；物理模型 Attempt 专项需使用真实 ModelService/ReliableExecutor 的 ProviderFixture 路径。旧 MCP `write` 会主动报错，获批执行需本课受控效果夹具。
- 已将受理、Outbox 部分发布、Redis 确认、SQL 投影、模型派发、QuickJS 子结果交付和效果提交窗口的注入位置加入测试计划。当前代码仍在实现，实际函数和执行入口在开测前再次核对。

业务验收状态继续为未执行。

## 首轮 Agent 检查

2026-11-12，在 Compass 根目录执行以下命令。源码为 `9b62205` 加当前第 15～19 课未提交实现，主任务同期继续开发；以下结果对应各命令读取的当时快照，修复后需复验。未加载私有测试环境、未执行 PostgreSQL 集成、未启动共享服务或修改业务数据。普通测试使用项目现有隔离夹具。

| 命令                      | 实际结果                                                                                                          | 原始日志                       |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| `pnpm typecheck -F agent` | 退出 1；60 errors、0 warnings。Turbo 输入摘要 `1174d27914b762d2`。                                                | `work/lesson-19/typecheck.log` |
| `pnpm lint -F agent`      | 退出 1；67 项，含 59 项 E501、7 项 I001、1 项 F401；Ruff format 检查尚未执行。Turbo 输入摘要 `a6a6b918b67400c7`。 | `work/lesson-19/lint.log`      |
| `pnpm test -F agent`      | 退出 1；336 项中 334 通过、2 失败、10 条依赖警告；pytest 51.02 秒。Turbo 输入摘要 `45a2a72bb73ea404`。            | `work/lesson-19/unit.log`      |

### 单测失败定位

| 用例                                                                         | 实际观察                                                                                                                                      | 修复核对点                                                                |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `test_api.py::test_readiness_fails_without_database`                         | 进入 TestClient 时抛 `OperationalError`；`main.py:50` 的 lifespan 创建 DurableRuntime 时直接调用已模拟失败的 `get_engine()`，未到 `/readyz`。 | 数据库故障时进程健康与就绪边界；服务应按最终设计明确报告依赖未就绪。      |
| `test_api.py::test_readiness_accepts_current_empty_schema_without_model_key` | `/readyz` 实际 503，断言期望 200；夹具仍写入 0008，当前 readyz 检查 0009。                                                                    | 同步测试所代表的当前迁移版本，并独立覆盖持久执行要求的 Redis/所有者就绪。 |

其余现有模型、路由、可靠执行、受控自主、工具、执行树、Workflow Runner 和 v1 契约测试在本轮通过。新增持久化业务仍需专属单测/集成及端到端验收。

### 类型与格式分类

| 文件                         | 类型错误数 | 主要问题                                                           |
| ---------------------------- | ---------- | ------------------------------------------------------------------ |
| `api/sessions.py`            | 2          | Redis Stream ID 为 `bytes / str / None`，传给要求 str 的解析函数。 |
| `harness/executor.py`        | 1          | 普通 str 传入 `RunToolError` literal。                             |
| `models/attempts.py`         | 23         | SQLAlchemy 查询结果可能为 None，读写字段前缺少收窄。               |
| `runtime/durable_service.py` | 4          | 可空所有者连接/Wait；`flush_progress` 赋值和 await 的类型不符。    |
| `runtime/journal.py`         | 7          | `flush_progress` 被推断为 None；可空 Run/Checkpoint 未收窄。       |
| `runtime/redis_stream.py`    | 8          | Redis 返回值的可空、bytes/str 联合未转换到公开 ID 合同。           |
| `runtime/store.py`           | 15         | 状态 literal、TenantScope 及可空 Run/Message/Session 未收窄。      |

Lint 主要集中在 0009 迁移 SQL、模型/运行实体、database 工具和新接缝的超长行与导入排序；`models/attempts.py` 的 `dataclasses.asdict` 为未使用导入。全部位置保留在完整日志。本测试子任务未修改生产代码、测试代码或校验标准。

## 首轮 Web 检查

2026-11-12，在 Compass 根目录执行 Web Lint 和普通 Vitest。主任务已接入 v2 页面与 BFF 初稿；本轮没有启动 E2E、访问数据库或修改源码。以下结果对应当时工作区输入，新 v2 场景继续按测试计划补验。

| 命令               | 实际结果                                                                                   | 原始日志                      |
| ------------------ | ------------------------------------------------------------------------------------------ | ----------------------------- |
| `pnpm lint -F web` | 退出 1；2 errors、1 warning。Turbo 输入摘要 `31d0dbd42adcadcb`。                           | `work/lesson-19/web-lint.log` |
| `pnpm test -F web` | 退出 0；15 个测试文件、111 项全部通过；Vitest 1.41 秒。Turbo 输入摘要 `bb990dcbf332c282`。 | `work/lesson-19/web-unit.log` |

Lint 位置：

| 文件与位置                                  | 规则                                | 实际观察                                                                            |
| ------------------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------- |
| `src/client/durable-analysis.ts:2`          | `@typescript-eslint/no-unused-vars` | `components` 类型导入未使用；项目执行零 warning 门禁。                              |
| `src/client/durable-analysis.ts:42`         | `react-hooks/set-state-in-effect`   | effect 主体直接同步 `setState(null)`，产生级联渲染。                                |
| `src/components/durable-run-actions.tsx:20` | `react-hooks/purity`                | render 中用 `Date.now()` 计算审批到期，需通过可更新时钟状态保持渲染纯度与到期刷新。 |

现有 BFF、身份、权限、v1 流与客户端、模型、MCP、执行树测试本轮通过。完整失败输出已回传主任务，由主任务修复并复验。

## 实现中定向只读核对

2026-11-12，主任务通知 Web 8130 已启动、Agent 8132 待启动。本轮没有重跑全量检查，没有执行数据库集成、UI、服务启停或业务写入。只读健康请求返回 HTTP 200，响应为 `compass-web` 的 `ok`。

| 入口                                                                  | 实际结果                                                                                                                                            | 原始日志                                   |
| --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| `uv run --locked pytest apps/agent/tests/test_durable_protocol.py -q` | 退出 0，4 passed，0.32 秒。游标整数比较、私有续接绑定、完整批次可见性、消息 revision。                                                              | `work/lesson-19/durable-protocol-unit.log` |
| 内存载入 Web `agent-response.ts` 的定向探针                           | 9 项通过：大整数游标顺序、快照+增量、重包幂等、旧快照保留更新、revision 错误原子性、3 类非法输入、每 7 字节拆分的 UTF-8 SSE。未新增或修改测试代码。 | `work/lesson-19/v2-readonly-probes.log`    |
| Python `AgentResponse.model_validate` 同类载荷核对                    | 正常 delta 接受、snapshot 中的 message.delta 拒绝；delta `0-0`、`18446744073709551616-0` 接受，后两项与 Web validator 不一致。                      | `work/lesson-19/v2-schema-parity.log`      |

### 待修复接口接缝

以下为本轮代码快照观察，已回传主任务；修复后补相应请求级复验。

| 接缝         | 当前观察与业务影响                                                                                                   | 关联用例 |
| ------------ | -------------------------------------------------------------------------------------------------------------------- | -------- |
| 审批过期响应 | Agent decisions 返回 HTTP 409 + ControlReceipt；BFF 非 2xx 分支仅接受 ModelErrorResponse，过期回执预计被转换为 503。 | TC-11    |
| 活跃流丢失   | 客户端订阅异常仅对 409 重载 Message；410 进入 1.5 秒重连循环，没有加载最新受阻状态。                                 | TC-14    |
| 游标合同     | Python DTO 容许零增量游标和超出 uint64 的数值，Web 拒绝。需要保持公开 Schema、服务端和客户端校验一致。               | TC-05    |

### 当前业务验收覆盖

AC-01～AC-19 均已映射至测试计划；本轮定向结果覆盖协议局部行为。新增 Runtime 集成文件静态核对为 6 个展开用例，使用同进程 Runtime 重建、FakeModels 与允许授权替身，执行状态待主任务回传。完整测试仍需落实以下业务结果：

1. 真实进程终止与重启：审批等待、并行子调用、效果提交后回执前、模型部分输出及 Outbox/投影各窗口；用独立账本计数。
2. PostgreSQL/Redis：旧库升级和恢复、约束与权限；Redis 保卷重启、写满、活跃流丢失、TTL 和批次投影；所有者失效后的派发和写入约束。
3. 真实 BFF/浏览器：首问和失败刷新、14 个第 15～18 课案例的实时/历史逐字段差分与原文件；追问/上下文重置；真实撤权与控制竞争；分页、断线、桌面、390 px、键盘。
4. 业务专属边界：物理模型 Attempt/未知预算、Verifier/计划恢复、草稿两 Run 去重/过期、MCP 目录变化/未知副作用、Artifact 冲突/损坏/保存失败。

本轮没有将未执行场景计入通过，也没有调整需求、生产实现或测试标准。

## 第一批真实浏览器验收

2026-11-12，使用 Chromium、Web 8130、Agent 8132（`tests.workflow_e2e_app`）和本课隔离环境。浏览器登录真实 teaching.admin。业务调用经过真实 Web/BFF/Agent/PostgreSQL/Redis/Retail/QuickJS；模型沿用确定性 Workflow fixture。实际可用模型列表为空，选择器采用已有 `hybrid-workflow.spec.ts` 的 Retail Pro 能力响应夹具。仅该能力响应被替换，Session、Run、审批、历史和 SSE 均为真实请求。未直接写库或重启服务。

### 已执行结果

| 场景                      | 结果                     | 本轮观察                                                                                                                                                     |
| ------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 渠道动态分析首次受理      | 首轮失败，修复复验通过   | POST 202 后带 `session` 的 URL 曾提示“页面参数无效”；主任务补页面参数后，原会话直接刷新恢复。                                                                |
| 渠道动态分析业务结果      | 通过                     | POS 从 600.00 到 280.00，变化 -320.00；商品贡献 -210.00/-70.00/-40.00；独立复核、并行 SubAgent、源码保存邀请可读。                                           |
| 渠道 SSE 与 Message       | 数据值通过，显示顺序失败 | 从 0 重放真实 SSE 共 7 帧/110 包；相同 Web validator/reducer 得到 44 个对象，逐字段值与 Message 一致。进一步核对执行树排列发现顺序差异，见后表。             |
| 新建对话与侧栏返回        | 通过                     | 空白页未创建 Session；新问题受理后新增唯一会话。侧栏链接按 Enter 返回原 Session，地址与 `aria-current` 选中项一致。                                          |
| 预算批准                  | 通过                     | Wait 刷新后仍可批准；Tab 到拒绝、Shift+Tab 返回批准，Enter 提交返回 200，原 Run completed，回答“已批准并完成本次预算调整”。                                  |
| 批准后刷新                | 通过                     | 刷新无新增 POST；Message 快照逐字段不变，原 Wait/Action/Decision ID 保留，显示“已批准执行”。                                                                 |
| 预算拒绝                  | 通过                     | Enter 拒绝返回 200，原 Run completed，保留只读分析并显示“已拒绝执行”；刷新前后快照相同。                                                                     |
| 桌面/390 px/过程键盘      | 部分通过                 | 1440 px 与 390 px 页面可操作；390 px document/main scrollWidth 均等于 390；键盘展开执行过程；已保存审批焦点和窄屏截图。                                      |
| 浏览器运行错误            | 通过                     | 本批操作未捕获 pageerror。曾出现一次会话列表加载失败提示，随后真实列表 GET 200、刷新消失；未捕获对应 4xx/5xx，保留为暂态观察。                               |
| Python 游标修复           | 通过                     | 正常游标及 uint64 最大值接受，delta 0-0 和 uint64+1 拒绝；日志 `work/lesson-19/v2-schema-parity-retest.log`。                                                |
| 会话分页与游标边界        | 通过                     | 4 个 Session 用 limit=2 返回两页，无重复或遗漏；列表/Message 非法游标、SSE 非法或超前游标均返回 422 cursor_invalid。                                         |
| 工作区/无调用权限用户读取 | 通过                     | 当前账号使用其他工作区读取返回 404；真实 teaching.analyst 登录成功后，列表/Message/SSE 均由 BFF 返回 403，无业务内容。具有调用权限的其他用户所有权隔离另验。 |

### 待修复的浏览器发现

| 发现               | 可重复观察                                                                                             | 位置/关联                                                                                                                                                              |
| ------------------ | ------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 历史仍逐字输出     | 完成的渠道会话每次刷新，首次可见回答仅“基准期 [2025”，同时显示“正在输出回答…”，随后补齐。              | TC-16，`browser-history-typing.png`。                                                                                                                                  |
| 执行树持久顺序变化 | 真实 SSE 根顺序为第 1→2→3 轮；历史界面为第 1→3→2 轮。Workflow 的串行渠道分析在历史中排到并行分析之后。 | TC-02/AC-10，`turnFromRun` 用 `Object.values(view.objects)` 生成节点数组；数据库 JSONB 对象键读取顺序与原始插入顺序不同。`browser-execution-order.json` 保存两种顺序。 |

原首问导航缺陷已修复并通过真实页面复验。BFF 过期和 410 丢流接缝由主任务报告已修复，实际故障请求仍待专项执行。

### 测试对象与记录

| 用途         | Session / Run                                                                   |
| ------------ | ------------------------------------------------------------------------------- |
| 渠道动态分析 | `edc60068-59ed-4883-870f-bfd1304b424c` / `fd4f0fb7-3ccf-45be-a835-8ab4cacedac7` |
| 预算批准     | `fc58cbdb-5c0b-48da-be20-8c063f637f94` / `539a0e1b-7a12-4feb-a625-fef6cc225d2c` |
| 预算拒绝     | `2a83d683-255a-4e03-a9ab-c851e0857bc8` / `8d030d93-4806-426e-ac03-9ca83ac5844c` |
| 等待真实重启 | `67b29f6f-c3f5-4a57-af30-99a616cc0345` / `019b2456-aae9-4a59-b519-3d272bb6c159` |

JSON 记录均位于 `work/lesson-19/`：`browser-first-submit.json`、`browser-first-history.json`、`browser-first-replay.json`、`browser-execution-order.json`、`browser-budget-approve.json`、`browser-budget-reject.json`、`browser-session-summary.json`。截图包括 `browser-first-submit.png`、`browser-history-typing.png`、`browser-budget-390.png`、`browser-budget-approved.png`、`browser-budget-rejected.png`、`browser-channel-390.png`。

分页与隔离读取记录为 `browser-read-boundaries.json`、`browser-cross-user.json`。

另一个预算 Wait 已保存重启前快照 `browser-wait-before-restart.json`，等待主任务下一次受管重启后核对原身份、有效期和接续。该对象当前未决定，期限为 2026-11-11 19:27:33 UTC。进程外工具/模型计数和原图表尚未在本批浏览器场景核验。

## 真实进程重启与界面修复复验

2026-11-12，主任务对受管 Agent 执行真实进程切换：原 PID 46420 因已有 SSE 未退出而被 SIGKILL，新 PID 49503 在原 advisory lock 释放后接管。独立只读监听记录见 `work/lesson-19/agent-restart-listener.log`。本测试子任务在同一浏览器会话中继续验收，没有执行服务启停。

| 检查             | 结果         | 实际观察                                                                                                                                                       |
| ---------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 重启后原等待点   | 通过         | Session `67b29f6f-c3f5-4a57-af30-99a616cc0345` 的完整 Message 与重启前逐字段相同；Run 仍 waiting/version 12，cleanup confirmed。                               |
| 等待身份与有效期 | 通过         | Wait `a37e225d-cb45-5b96-8f52-3e479768a25a`、Action `6064461b-aabc-5cfc-a38f-a0a6d8960615`、proposalDigest、绝对 expiresAt 均不变。                            |
| 重启后批准       | 通过         | 原页面刷新后按 Enter 批准，真实 BFF 返回 200；原 Run 进入 completed/version 15；原 Wait approved/revision 2，回答保留原分析并显示预算调整完成。                |
| 历史动画修复     | 通过         | 原渠道会话连续两次刷新，首次可见回答已包含全部商品贡献和独立复核；“正在输出回答…”数量均为 0。                                                                  |
| 执行树顺序修复   | 通过         | 原渠道会话根节点恢复第 1→2→3 轮；Workflow 子项恢复渠道分析→并行分析，商品贡献与口径复核位置正确。                                                              |
| 审批后过程状态   | 失败，已回传 | 顶层 Run completed、审批卡 approved，但持久 execution.node 的“准备预算调整提案”“等待人工决策”仍为 waiting，页面显示待处理。拒绝/到期分支的同类收口需同步核验。 |

重启和批准快照保存在 `browser-wait-after-restart.json`，截图 `browser-restart-approved.png`；动画/顺序记录为 `browser-history-fixes.json/.png`；新的节点状态问题为 `browser-budget-process-stale.json/.png`。

本轮真实重启覆盖预算等待和原 Run 接续。原 Sandbox 清理后图表下载、进程外模型/工具次数，以及效果提交与 Run 回执之间的真实终止窗口仍按 TC-09～TC-12 专项验收。

## Composer v2 测试迁移与执行

2026-11-12，主任务明确授予 `apps/web/tests/e2e/analysis-composer.spec.ts` 及新建同目录夹具的独占维护责任。本测试子任务将旧 POST Run/v1 流迁移至 v2 会话提交、Message/SSE 和 Artifact 下载，保留原 12 个用例的业务目的，另拆分补齐焦点收口和后台继续，总计 14 项，无跳过项。生产代码仍由主任务修改。

### 最终执行

命令：`python work/lesson-19/run-env.py env WEB_TEST_EXTERNAL_SERVER=true WEB_TEST_STORAGE_STATE=<本轮私有登录状态路径> pnpm test:e2e -F web -- analysis-composer.spec.ts --reporter=list --output=../../work/lesson-19/composer-playwright-r3`。

| 检查                              | 实际结果                                               | 日志                                       |
| --------------------------------- | ------------------------------------------------------ | ------------------------------------------ |
| Composer 全部 E2E                 | 退出 0，14 passed，40.2 秒，Turbo `5aa769db0b40fbba`。 | `work/lesson-19/composer-e2e-r3.log`       |
| 两个测试文件 ESLint               | 退出 0，零 warning。                                   | `work/lesson-19/composer-fixture-lint.log` |
| Web `tsc --noEmit --pretty false` | 退出 0。                                               | `work/lesson-19/composer-types.log`        |
| 测试代码/验收文档格式及差异       | Prettier 与定向 `git diff --check` 通过。              | 本轮执行输出。                             |

通过范围包括输入法与 Enter/Shift+Enter、模型选择、受理后的停靠/进度、完整 Markdown 与安全渲染、独立历史滚动、新会话、刷新追问的服务端历史、CSV 文件名/完整字节、取消确认前后的工具状态、未知清理保持受阻、取消与完成竞争、Message 暂不可用时不显示未确认结果及手动恢复、过程收口的键盘焦点、离开会话不取消后台任务、10 附件上限/删除/重新选择、375/768/1440 px 和 300/350 px 短视口、计划进度/浮层/键盘/完成隐藏。

### 修复与复验经过

首轮 6 通过/8 失败，日志 `composer-e2e.log`。其中真实 UI 缺陷为：v2 Run canceled 且 cleanup confirmed 后，fallback ToolCard 仍显示“结果待确认”。主任务修复 `turnFromRun` 的已取消步骤收口后，同一断言通过。其他调整为夹具层的持久下载、Markdown 列表断言、恢复提示区域及新增 Workflow 区域后的整个底部停靠容器定位。

第二轮 9 通过/5 失败，日志 `composer-e2e-r2.log`；4 项在重复登录达到真实限流后收到 429，1 项为原生下载绕过 Playwright routing。最终使用真实既有登录状态、同 worker cookie 复用和已授权的下载夹具方式后，全组通过。登录状态未输出、未提交，未改应用限流或数据库。

下载隔离实验确认：原生 `a[download]` 的请求没有命中 page/context route，Mock Run 在真实 Agent 中无对应记录。此组先校验产品原 download 属性与 Artifact URL，再移除测试 DOM 属性，使用模拟服务器 Content-Disposition 下载并读取完整 CSV 字节。该处理已由主任务明确确认；真实 Agent 的原生 PNG/CSV 下载另行记录。真实 Web/身份与受控 Agent 协议响应的边界保留在测试计划。

## AC-03：原图与审批等待经历真实进程终止

2026-11-12，Web 8134、Agent 8135 使用真实 PostgreSQL/Redis/Retail/OpenSandbox/NAS/MCP；ToolModels fixture 提供确定性的模型响应。输入为“先生成图表后审批 MCP 写操作；对比两个时段的渠道商品实付金额，保存原 PNG 图表后等待人工决定”。真实流程依次完成 Retail 查询、Python 图表及 MCP 写操作提案。本测试子任务执行真实浏览器提交/刷新/键盘批准、原生下载和只读数据库/资源检查；主任务执行 Agent SIGKILL。

| 业务对象           | 标识                                                                                                                 |
| ------------------ | -------------------------------------------------------------------------------------------------------------------- |
| Session / Run      | `3379db8f-51c8-4bb3-9f4e-9ca253b062c2` / `cf963a68-5d98-4e01-ab77-407c4d4aad02`                                      |
| Wait / Action      | `88786f3a-6cbd-5cd0-a51c-ef63e238645a` / `b96aceb5-c665-5ac7-92a7-05e276ad2347`                                      |
| Proposal           | `2ede8ecf-eb4b-58d4-bb7b-7adb5e93b116`，revision 1；到期时间 `2026-11-11T20:10:59.530630+00:00`                      |
| 原 PNG             | Artifact `8388f47b-b721-549a-ac29-aabca4a5a2a2`；来源 Step `a875f7db-1dc9-5b5c-b3e3-268118edc008`、toolCall `python` |
| Sandbox Allocation | `8562aff6-7ae0-408f-8834-b9060a86085a`                                                                               |
| 实际进程切换       | 原 Agent PID 71366 被 SIGKILL，新 PID 72501 ready 后继续验收。                                                       |

| 检查               | 结果                                                                                                                                                                 |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 重启前释放计算资源 | Allocation retained、sandbox_id 为空；按 allocation 标签查询 OpenSandbox 所有状态，匹配计算容器数为 0。重启后仍为 0。                                                |
| 原等待点恢复       | 重启前后完整 Message 逐字段相同；Session、Run、Wait、提案摘要、Action、绝对有效期均保持原值。批准前外部 MCP write-count 始终为 1。                                   |
| 原 Run 接续        | 刷新后用 Enter 批准，BFF 返回 200；同一 Run 最终 completed/version 24，cleanup confirmed、recovery ready。批准后再刷新，完整 Message 逐字段相同。                    |
| 无重复诊断         | 查询与 Python 的诊断 Attempt 始终为 2；两条记录的 ID、输出摘要、开始/完成时间前后逐字段相同。原模型路由及前三轮完成记录保持不变。                                    |
| 一次外部效果       | MCP 进程外 write-count 为 1→2，最终刷新后仍为 2；数据库 `root/physical:remote` 仅 1 个 completed Attempt。                                                           |
| 图表字节与来源     | PNG 10517 字节，SHA-256 `986371222d69f89668611146e7d2a8c557ba838332586f754cfc4a3e8f983735`；重启前、恢复后、批准后、刷新后及原生下载字节相同；Artifact ID/来源未变。 |
| CSV 原生下载       | 实际文件名 `analysis.csv`，369 字节，SHA-256 `9c8ea212392b7117ce2c0b2fe2a528886366dfba19a4cb91b72297a6f7e7edf1`；原生下载、HTTP 与数据库内容相同。                   |
| 执行过程收口       | 7 个公开 execution.node 均 completed，9 个数据库 Step 均 completed；审批 gate 为独立 control Step，MCP 物理调用保留独立 Attempt。                                    |
| 页面稳定性         | 原生下载未改 DOM download 属性；刷新无新增效果；pageerror 为 0，过程区可用键盘展开。                                                                                 |

记录位于 `work/lesson-19/`：`ac03-before.json`、`ac03-after-restart.json`、`ac03-after.json`、`ac03-database-before.json`、`ac03-database-after.json`；只读检查脚本 `ac03-inspect.py` 与对应日志；原文件 `ac03-original.png`、`ac03-delivered.csv`；截图 `ac03-waiting-desktop.png`、`ac03-completed-desktop.png`。重启日志为 `ac03-restarted-agent.log`。

本场景完成后已通知主任务释放 8135 后端模式，后续 Sandbox/NAS 故障测试期间不再提交新的 Sandbox 任务。

随后按主任务要求，通过管理员 HTTP 将本场景启用的 MCP 连接恢复为原先 disabled 状态，返回 200、revision 4。通用 ProviderFixture 的模型会选择已注册工具；此前复用了这个仍启用的写工具，导致两个路由用例进入审批等待，复验前已消除此测试环境干扰。

## AC-12：真实追问输入、上下文重置与会话隔离

2026-11-12，最终 Web 8134/Agent 8135，最新修复后的 Agent 进程；真实 ProviderFixture 模型 HTTP 接缝，其余 Web/身份/权限/Runtime/PostgreSQL/Redis 完整运行。通过 Web 平台 CRUD 新建本次专用 Provider，测试问题使用唯一合成标记。管理员和分析员分别完成独立会话；管理员同一会话完成两轮后，页面刷新并从真实输入框发送第三轮；随后 HTTP 保存上下文重置，再提交第四轮。

只读检查脚本在 `SET TRANSACTION READ ONLY` 中读取已关联 Run/Step 的 `model_call.request_content`，使用 Runtime 版本/用途/Run 绑定解密，逐条检查实际模型用户和助手消息。私有输入仅在内存核对，记录只含匹配状态、角色数量与标识。

| 顺序              | Run                                    | 实际用户/助手消息数 | 结果                                    |
| ----------------- | -------------------------------------- | ------------------- | --------------------------------------- |
| 会话 A 首轮       | `43575152-7d7a-42f5-a1c5-0b658fd820e0` | 1 / 0               | 只含本轮问题。                          |
| 同用户会话 B      | `ec68abb0-51e7-4a45-a1ed-fa5e52f4e544` | 1 / 0               | 排除 A 和其他用户标记。                 |
| 分析员会话 C      | `8d401058-a3af-486a-b785-3bb4df795982` | 1 / 0               | 排除管理员两个会话标记。                |
| 会话 A 第二轮     | `ca38605c-3a20-4ca1-a794-4ed07b748765` | 2 / 1               | 首轮问答和本轮问题精确匹配。            |
| 会话 A 刷新后追问 | `a34f4fbd-81a1-47e4-a67f-7d2b43934bbc` | 3 / 2               | 前两轮完整问答及本轮问题，顺序正确。    |
| 会话 A 重置后     | `554cd0cf-5c97-4faf-9b26-e9a5fe21e1ff` | 1 / 0               | 仅本轮问题，原三轮及 B/C 标记全部排除。 |

会话 A 为 `c292149b-9177-45b4-8322-1241a3cbf410`；重置回执 status reset/version 23。分析员尝试读取会话 A 的 Message 返回 404。全部 6 个真实主模型请求逐条匹配通过，页面 pageerror 为 0。记录 `work/lesson-19/ac12-18-web.json`；独立入口 `ac12-18-web.mjs`、`ac12-18-inspect.py`。脚本执行后的分析员原权限已通过 HTTP 恢复。

## AC-18：真实撤权首轮

管理员通过真实权限 PUT 移除分析员的 models.invoke，已登录分析员的新 Message、SSE 和 submit 请求均返回 403；撤权前建立的 SSE 已结束。此时模型已经实际派发完成，后续两个 MCP 工具 Attempt 保持 prepared，未进行物理派发。

发现需要修复的收口状态：Run `24acb9b5-3e48-49e2-8f78-8638482e3ec0` 保持 cancelling/version 11、reason cleanup_unconfirmed，两个工具 Step 仍 running。对应工具 Attempt 均 prepared，两个模型 Attempt completed。已回传主任务；本项保留失败，待修复后复验同一业务断言。只读结果 `work/lesson-19/ac18-revoked-state.json`，执行日志 `ac12-18-web.log`。本次自建分析员 MCP 已停用，原权限已恢复。

### 修复复验通过

主任务修复权限 gate 的暂停信号透传并重启后，以同一脚本重复完整业务过程。Run `74649fdd-ee6b-44cd-9191-ee0c63388696`、Session `1a696654-924e-40d7-9f3f-96eac021c3b9` 达到 paused/version 10、reason forbidden。撤权后的 Message、新 SSE、submit 均为 403，既有 SSE 在 5177 ms 后断开。两个 MCP 物理 Attempt 保持 prepared、没有派发，模型 Attempt 数始终为 2；恢复调用权限后可重新读取原暂停原因，后台没有自动续跑。

本轮同样复验 AC-12 的 6 个实际模型输入，全部通过。脚本退出 0，日志 `work/lesson-19/ac12-18-web-retest.log` 输出 historyPassed/authorizationPassed 均为 true；完整结果 `ac12-18-web.json`。finally 已恢复原权限并停用本轮自建只读 MCP，errors 为空。

## 前序业务矩阵与 Web 完整视图复验

2026-11-12，Agent 侧完成 16 类真实 Retail/QuickJS/Sandbox 业务路径，记录 SSE 与 Message，重建 Runtime 并清理每个终态短期流后再次比较持久内容、原文件与 Attempt 数。`test_durable_case_matrix.py` 1 项含 16 案例通过，59.78 秒；记录 `matrix-expanded.log`、`durable-matrix.json`。

独立 Web 入口 `durable-history.spec.ts` 对同一导出文件调用生产 validator/reducer，16/16 通过，2.9 秒。每个增量帧重复应用保持完全相同状态；最终完整业务视图与 Message snapshot 相同；两次历史重载及旧帧再交付不新增消息、节点或文件。比较保留所有业务版本、ID、脚本、计划、Verifier、产物与终态，只排除传输游标和分页指针。AC-03 真实 HTTP 导出的 5 帧/67 包另通过同一 Web 测试，1/1，631 ms。

首轮矩阵因帧分组先出现 message 再出现 run、以及裸事件省略 null 与实际 DTO 不同而失败；导出路径已按真实 AgentResponse 进行组帧和 DTO 序列化。最终输入与运行时 HTTP 契约一致，Web 测试未放宽比较。

## 模型与真实观测 v2 复验

2026-11-12，模型/观测浏览器入口迁移为 Session submit 和 Message/SSE v2，原模型管理、能力测试、可用性、键盘/响应式、权限与观测断言保留。真实 Langfuse 使用本课 `test/browser-990` 环境；观测 1/1 通过，13.4 秒，核对完成/取消 Run 的 trace 头、实际 modelCallId 与观测聚合、权限边界。

模型原 8 项分批通过：首批 5 项；两种 App 切换场景通过，保持原文档/DOM、同 Session 的三个 Run 和完整三轮历史；客户端提交没有 history。Chat 最终复验 1/1，42.9 秒，覆盖模型禁用时保留草稿与回 auto、错误安全文案、早期取消、生成时撤权后清除内容。

取消复验曾连续三次 409：Message 投影仍为 version 4，数据库运行表已推进，立即重载重复使用旧版本。修复后冲突响应提供运行表 currentVersion，暂停/取消用新版本重试；兼容旧响应仍读取 Message。另修复准备阶段零物理派发取消的清理收口。模型切换的 context-reset 为异步过程，测试等待选择真正生效后再禁用；产品在重置期间禁止发送，另有独立 Composer 回归通过。

原日志 `models-e2e-v2.log`、`models-e2e-v2-retest.log`、`models-chat-final.log` 保留失败现场；当前结果见 `models-e2e-v2-final.log`、`models-chat-current-version.log`、`observability-e2e-v2.log`。最终通用 E2E 全组仍由主任务顺序运行，避免 teaching 权限互扰。

## Composer 最后三项定向回归

2026-11-12，原 14 项用例保留，新增 3 项全部通过。测试使用真实 Web 登录/页面与独立 v2 Agent 协议夹具，Agent 的物理执行另由真实业务矩阵覆盖。

| 新增场景                   | 结果                                                                                                                                                                                                                                                 |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 取消遇版本冲突             | 第一请求 409 后从 Message 读取 version 2，以新 requestId 重试同 Run；只提交一个分析 Run，最终 canceled/confirmed，工具卡和刷新后原 Run 都显示已终止。该用例保留无 currentVersion 的兼容响应路径；新 currentVersion 路径由真实 Chat 与 Web 单测覆盖。 |
| 切换模型等待 context-reset | 重置请求由夹具屏障延迟，选择器/输入禁用且发送入口不可用，无新增分析提交；放行后草稿保留，下一次提交明确使用所选 fixed provider。                                                                                                                     |
| saved/expired 保存卡刷新   | 已保存卡恢复自定义名称且不重新获取草稿；已过 expiresAt 的邀请显示失效并隐藏操作；服务端返回 410 时显示失效提示，无确认保存副作用；分析结果始终保留。                                                                                                 |

首轮新断言误将 Markdown 有序列表标号当成 textContent，已按实际 DOM 文本修正；另一次与模型撤权并行导致固定测试 revision 失效，独占后复验通过。最终保存卡 1/1，34.8 秒，见 `composer-workflow-final.log`。模型切换 1/1，6.2 秒，见 `composer-additional-retest.log`；取消冲突 1/1，10.8 秒，见 `composer-additional.log`。

所属五个 E2E 文件 ESLint 零 warning，文档内部路径和 `git diff --check` 通过。所有共享环境测试已交回主任务，随后统一运行完整通用 56 项，避免教学权限变更影响其他浏览器上下文。

## 最新工程门禁与边界测试归档

2026-11-12，主任务回传以下当前版本结果，独立测试核对对应日志结尾：

| 入口                 | 实际结果                                                                          | 记录                        |
| -------------------- | --------------------------------------------------------------------------------- | --------------------------- |
| `pnpm check`         | 19/19 任务通过；Agent 340 单测、Web 119 单测                                      | `check-current.log`         |
| `pnpm build`         | 7/7 任务通过                                                                      | `build-final.log`           |
| Web PostgreSQL 集成  | 22 项通过                                                                         | `web-integration-final.log` |
| 持久边界测试         | 7 项通过，90.68 秒                                                                | `boundary-tests-final.log`  |
| 最新五应用容器 smoke | Web readiness/身份边界、BFF→Agent→PostgreSQL、Memory 脚手架和 Home/生成资产均通过 | `container-smoke-final.log` |
| Worker 正常退出      | SIGTERM 后 exit 0，OOM false；停止后由主任务恢复启动                              | `worker-shutdown.log`       |
| 重复 seed            | 保持 3 天、397 单、实付合计 4000000，重复执行稳定                                 | 主任务当前容器验收记录      |

边界 7 项覆盖批准与取消/到期原子竞争、受信事件 HTTP 的独立身份与仅消费一次、两 Run 同源草稿复用和过期拒绝、目录变化在重复数据库调用前阻断、实际模型重试及 classifier Fallback 的原 Run/Step/Checkpoint 关联。并行子结果交付窗口另补真实进程退出测试，完成后单独登记。

当前待收尾：完整 Agent 90 项与通用 56 项 E2E。主任务统一操作共享环境，本子任务仅维护报告。

## TC-08 并行子结果交付窗口真实进程退出

2026-11-12，独立边界测试启动真实 Agent 子进程，在并行子结果提交后、交付 JS 前终止进程，并启动新进程恢复原 Run。专项 1/1 通过，23.58 秒，记录 `parallel-process-crash-final.log`。

重启前后外部审计列表逐条相同：子模型调用 6 次、Retail 查询 3 次，全部已完成调用保持原计数和内容；原 Attempt、host 交付顺序、脚本源码与 clock 记录保持。恢复使用同一 Run，完成后草稿重复保存返回同一 asset。该项完成 TC-08 的真实进程窗口验证，先前同进程 RecoveryStop 用例保留为不同层级覆盖。

## 通用目录遍历超时定位

完整通用组中的 foundation 首项在 120 秒总预算耗尽。只读分析 trace：60 次完整文档导航中已完成 59 次耗时合计 111.68 秒；进入 `/guide` 时，距首次导航已 120.05 秒，前一页 `/settings/memory` 的保护根、侧栏与正文断言全部通过。主任务按目录页数设置 `pages.length * 3000 + 45000` 总预算，单次断言仍为 15 秒，业务断言保持；待其余通用用例结束后单独复验此项。

## 最终闭环记录

2026-11-12，验收完成。最终工程门禁为 `check-delivery.log` 的 19/19 和 `build-final-complete.log` 的 7/7；最新五应用启动与 smoke 均成功，容器 smoke 进程退出 0。根工具测试 48 通过、13 项因可选 formatter hook 资产缺失跳过；本课业务验收无跳过。

Agent 全组执行 `all-integration-current.log` 得到 89 通过、1 项过期夹具失败。原夹具用应用墙钟回推 expires_at，违反数据库要求的 expires_at/created_at 顺序；改为一致的 PostgreSQL 时间关系后，过期相关与并行进程三项复验通过，另跑最新 6 项关键回归通过。当前 91 个案例的通过覆盖由全组与定向结果共同组成。

通用 E2E 全组 `general-e2e-final.log` 得到 54 通过、2 项 foundation 超时。目录遍历按实际页面数量设置总预算，保持每个断言 15 秒；浮动导航外点击改为可见内容区域，保留关闭行为验证。`foundation-final.log` 两项复验通过，56 个案例完整闭环。Composer、模型、观测、权限、路由和可靠执行均在该完整组有通过结果。

最新完整 Agent 输出的 16 类矩阵再次执行生产 Web validator/reducer，16/16 通过（1.9 秒），记录 `durable-history-matrix-current.log`。单项通过次数按案例去重，定向重复执行不累加为新增案例。

本记录关闭 AC-01～AC-19；原始失败、修复定位、模型 fixture 边界和实际跳过均已保留。无待验业务项目。

## 最后补验：真实活跃流丢失 410 与客户端恢复

2026-11-12，两层专项均通过，完成 AC-11/18 的最后链路闭环。合同区分：终态短期流删除返回 `409 history_reload_required`；活跃流丢失返回 `410 stream_data_lost`，持久 Run 转为 `paused / recoveryStatus=blocked`，客户端重新读取 Message 后停止续流。

真实专项通过 Web/BFF/Agent/PostgreSQL/Redis 执行。Run `cdd468a8-7177-4a8b-aa57-d81d87feca38` 的活跃 Redis 流由授权测试脚本删除 1 个键，真实 GET events 返回一次 410；浏览器自动 Message 读取共 2 次，恢复同一 Run 的已提交问题、部分回答和过程，显示增量记录不可用，继续按钮 disabled。2.3 秒观察期内 events 总数仍为 1，无额外 POST 和 pageerror。`stream-loss-browser.log` 输出 passed=true，详情与截图为 `stream-loss-browser.json`、`stream-loss-browser.png`；脚本 `stream-loss-browser.mjs` 与 `stream-loss-fault.py`。首轮仅有侧栏/正文同文案导致定位器严格匹配失败，收窄到问题节点后通过。

可重复客户端回归 `analysis-composer.spec.ts` 新增 1 项：首次 SSE 用确定性协议夹具返回 410，随后 Message 返回 paused/blocked、原问题、部分回答和成功查询。生产 Web 自动重载、显示受阻说明、禁用继续；推进浏览器时钟 6.5 秒后仍只有 1 次订阅、1 次原始提交、0 次取消，无再次 Message 重载。1/1 通过，5.7 秒，`composer-410-final.log`。该项替代 Agent 的协议响应，真实 Redis/HTTP 行为由前述专项验证。首轮工具状态断言由‘已完成’修正为组件既有文案‘成功’，全部业务断言保留。

当前通用 E2E 案例集合为 57 项：本轮全组中的 54 项、修复后 foundation 2 项和新增客户端 1 项，逐项均有通过记录。所有 AC 完成，无待验业务项目。
