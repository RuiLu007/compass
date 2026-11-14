# 第 20 课业务测试计划

[返回需求简报](../BRIEF.md) · [故障矩阵](FAILURE_MATRIX.md) · [执行记录](ACCEPTANCE.md)

Profile：`standard`。采用[项目测试模板](../../_templates/TEST_PLAN.tmpl.md)，按实施期测试规划裁剪。有效需求及 AC 来自 [PRD](../spec/PRD.md#9-验收标准)，实现合同为[技术方案](../design/TECH_DESIGN.md)、[代码设计](../design/CODE_DESIGN.md)及[评审修订](../review/design_review.md)。项目未提供 `mai-specs/FEATURE_LAYOUT.json`，沿用 Skill 随包的 `test-plan → test/TEST_PLAN.md` 路径。

2026-11-12 建立。首轮仅规划；随后获授权新增并执行 Worker MockTransport/自建子进程信号测试、Web 单测、可控协议浏览器和真实队列浏览器测试，结果见 [执行记录](ACCEPTANCE.md)。数据库、双租户调度和进程故障由独立验收任务执行后交接。

## 范围与依据

覆盖 AC-01～AC-11：异步受理、所有接续入口排队、Worker 执行权、双进程接管、重复投递、超时和取消竞争、租户治理、Timeline/用量、公开过程与完整回答。复用 M5 的 Session/Run、Checkpoint、HITL、Action、Redis/Outbox/SQL 投影及 AgentResponse v2。

基线为 `9b6220525f257a122c132471688c6ad112ea61d8` 加当前第 15～19 课未提交实现。M5 的[最终验收](../../985-lesson-19-durable-runtime-hitl/test/ACCEPTANCE.md)已记录 0009 和 AC-01～AC-19 完成；M6 设计中的“M5 待实现”是设计当时背景。M6 测试按当前代码接缝适配，并重新验证调度改动影响的前序场景。原未提交内容保留。

本期确定性旁白、一次完整答案是已评审基线。可选模型旁白和分段答案不启用；验收覆盖当前选定的回答提交方式。业务预期取 PRD、明确设计参数和固定数据夹具，执行计数取进程外审计或真实依赖。

## AC 覆盖索引

| PRD AC | 本次业务用例                      | 主要验收落点                                         |
| ------ | --------------------------------- | ---------------------------------------------------- |
| AC-01  | TC-01、TC-02、TC-03               | 无 Worker 受理、持久后中断补交付、全部入口排队       |
| AC-02  | TC-13、TC-14、TC-15               | 同 Run 游标、重连完整性、跨租户与撤权                |
| AC-03  | TC-03、TC-04、TC-20               | 等待释放槽、原 Run 批准一次、M5 旧记录               |
| AC-04  | TC-05、TC-07、TC-08、TC-10        | 真 Worker 终止、新 epoch 接管、旧执行者与清理        |
| AC-05  | TC-05、TC-06、TC-07、TC-09        | 重复 claim/advance、HTTP 丢响应、期限与未知效果      |
| AC-06  | TC-10、TC-11                      | 取消/动作/批准/接管交错与唯一终态                    |
| AC-07  | TC-03、TC-04、TC-12               | 可信租户、并发/积压/全局限额、预算、公平与占用       |
| AC-08  | TC-16、TC-18                      | Timeline 各层关联、Retry/Resume/Replay、观测出口异常 |
| AC-09  | TC-08、TC-12、TC-19、TC-20、TC-21 | 四类故障、量化结果、迁移、部署和 ADR                 |
| AC-10  | TC-14、TC-15                      | 实时/历史同白名单、受限字段、当前权限                |
| AC-11  | TC-13、TC-17、TC-18               | 结构化完成、折叠一次、重复/重连与未完成答案          |

## 已有测试覆盖

以下入口已存在。M5 报告可用于识别已覆盖行为；M6 当前通过结果由后续执行单独登记。

| 业务行为                              | 已有测试或入口                                                                                                                                                                                   | 已有覆盖边界                                                    | 本课增量                                                      |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- | ------------------------------------------------------------- |
| 接单、控制、HITL、Outbox、历史        | [持久运行集成](../../../apps/agent/tests/test_durable_runtime_integration.py)                                                                                                                    | 真 PostgreSQL/Redis；同进程 service 重建与固定执行夹具          | job 原子性、只排队、领取与回收后原行为                        |
| 真进程退出、效果一次、模型未知用量    | [持久故障](../../../apps/agent/tests/test_durable_runtime_faults.py)、[进程应用夹具](../../../apps/agent/tests/durable_process_fixture.py)                                                       | 真实 Agent 进程退出、独立效果/调用计数                          | 两个真实 Worker 进程、租约/boot/epoch、Agent 重启协调         |
| 批准/取消、受信事件、目录与物理 Retry | [持久边界](../../../apps/agent/tests/test_durable_boundaries.py)                                                                                                                                 | 原 Action、权限、目录和模型 Attempt；并行子结果交付窗口         | claim/advance 接入后保留，补全部接续入口配额                  |
| 前序 16 类真实持久业务                | [业务矩阵](../../../apps/agent/tests/test_durable_case_matrix.py)、[案例定义](../../../apps/agent/tests/durable_matrix_cases.py)                                                                 | Retail/QuickJS/Sandbox 等完整 SSE/Message 比较、原产物与调用数  | 在调度路径运行，保留计划、脚本、并行组、审批与文件            |
| Web 协议与历史一致性                  | [持久历史](../../../apps/web/tests/e2e/durable-history.spec.ts)、[AgentResponse](../../../apps/web/tests/agent-response.test.ts)                                                                 | 生产 validator/reducer 整帧比较；需当前导出的真实业务帧         | 加入调度 Timeline、回收状态与完成批次；本入口单独计为协议验证 |
| Chat、断线、控制与回答                | [Composer E2E](../../../apps/web/tests/e2e/analysis-composer.spec.ts)、[v2 UI 夹具](../../../apps/web/tests/e2e/durable-analysis.fixture.ts)                                                     | 真实 Web、可控 Agent 协议夹具；410 重载、取消冲突、完整回答折叠 | queued、重连中切换成功、手动展开、白名单真实载荷              |
| 真实 Workflow/工具和观测              | [Workflow E2E](../../../apps/web/tests/e2e/hybrid-workflow.spec.ts)、[工具 E2E](../../../apps/web/tests/e2e/tool-runtime.spec.ts)、[观测 E2E](../../../apps/web/tests/e2e/observability.spec.ts) | 实际业务依赖、审批、原文件、模型观测；模型使用受控响应          | 调度后层级关联与当前权限保持                                  |
| 数据库与运行角色                      | [数据库集成](../../../apps/agent/tests/test_database_tools_integration.py)                                                                                                                       | 原生/SQL 迁移、schema 比较、角色、备份恢复                      | 0009→0010、两张调度表、worker_advance、旧记录                 |

现有根入口为 `pnpm test:integration -F agent`、`pnpm test:integration -F web`、`pnpm test:e2e -F web`、`pnpm db:sql:check -F agent`。持久集成可经 `pnpm test:integration -F agent -- -k durable` 定向选择。历史协议入口为 `pnpm test:e2e -F web -- durable-history.spec.ts`，须提供当前执行生成的 `DURABLE_MATRIX_FILE`；Agent 矩阵导出使用 `COMPASS_DURABLE_MATRIX_OUTPUT_DIR`。Composer 可经 `pnpm test:e2e -F web -- analysis-composer.spec.ts` 定向执行。

实施者需将新增调度集成加入 Agent `test:integration`，普通 `test` 排除真库文件；Worker 保持独立 `test`。本轮已增加 [Worker 消费测试](../../../apps/worker/tests/test_run_consumer.py)、[进程信号测试](../../../apps/worker/tests/test_worker_process.py)、[Web 协议与权限单测](../../../apps/web/tests/production-runtime.test.ts)、[UI 业务场景](../../../apps/web/tests/e2e/production-runtime.spec.ts)、[独立协议夹具](../../../apps/web/tests/e2e/production-runtime.fixture.ts)及[真实队列 UI](../../../apps/web/tests/e2e/production-runtime-live.spec.ts)。入口分别为 `pnpm test -F worker`、`pnpm test -F web -- tests/production-runtime.test.ts`、`pnpm test:e2e -F web -- production-runtime.spec.ts` 和 `pnpm test:e2e -F web -- production-runtime-live.spec.ts`。Agent 调度集成按实际落盘入口登记。单元、类型、静态与构建由实施流程负责，测试计划不重复逐条列其断言。

## 执行环境与隔离

| 资源           | 需要交接的条件                                                                                                                                                                                                |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 版本与进程     | 冻结本轮待测修改范围；一个 Agent 所有者、两个独立 Worker，记录 PID、workerId、bootId 与启动配置。进程启停/故障由主任务统一操作。                                                                              |
| PostgreSQL     | 专用 `compass_agent_test_*` 与独立 `compass_web_test_*`；Agent 运行/迁移/管理员分别由 `AGENT_TEST_DATABASE_URL`、`AGENT_TEST_MIGRATION_DATABASE_URL`、`AGENT_TEST_ADMIN_URL` 注入。集成临时库与浏览器库分开。 |
| Redis/其他依赖 | 本课隔离 Redis 命名空间或实例，M5 配置与卷保留策略不变。受控 Retail/MCP/OpenSandbox、NAS 与模型入口明确；故障资源独占。                                                                                       |
| 身份           | 两教学租户 A/B 各 4 个独立 Session；各有可调用分析员、租户管理员；另有平台管理员、工作区管理员/普通成员和无权限主体。两个 Worker 使用不同服务凭据。                                                           |
| 浏览器         | 独立浏览器上下文、`WEB_TEST_PORT`、`WEB_TEST_DATABASE_URL`；复用已有 Playwright 与当前 Web/BFF。使用外部进程时设 `WEB_TEST_EXTERNAL_SERVER=true`。共享 teaching 权限变更顺序执行。                            |
| 故障屏障       | 在受理提交、claim 回执、advance 受理/提交、步骤结果提交、动作效果提交、旧 delta 回调与清理确认处提供可观察屏障；故障夹具只在测试入口启用。                                                                    |
| 原始记录       | `work/lesson-20/` 保存每次命令、运行摘要、载荷脱敏结果、PID/Run/Action、计数和截图；密钥与私有环境文件不写入文档或 Git。                                                                                      |

开始执行前由主任务分配实际地址、环境文件与可操作故障资源。本子任务维护本 Feature 的 `test/` 及上述获授权测试文件；运行、迁移、生产实现、BRIEF 和 ROADMAP 由主任务管理。后续复用现有工具，无需新增工具依赖。

所有变更账号权限、服务模式、MCP 启用状态或测试限额的用例保存原配置并在结束后恢复。只清理本次创建的测试对象，失败现场先留存。

## 待验业务用例

### TC-01：无 Worker 受理与同键重试

- 关联需求：AC-01、AC-07。
- 前提与数据：两个 Worker 均未消费，A 有积压容量；新 Session 和已有空闲 Session 各一份。
- 操作：从真实 BFF 提交后关闭连接，查询原 Run；并发重复相同 requestId/输入，再用相同键改变输入。填满 A 积压后重试先前成功请求，再提交新请求。
- 预期：受理 202 后已有唯一 Session/Run/消息/初始投影/命令/job，公开 queued；没有模型或工具物理调用。相同输入取得原回执，冲突 409；满额旧回执仍可读取，新请求 429 且不产生 Run。启动 Worker 后领取原 Run 并完成。
- 验证：真实 HTTP、只读 SQL/用量、进程外物理调用数；记录受理与首次领取时间。

### TC-02：持久接收至首次派发窗口中断

- 关联需求：AC-01、AC-05。
- 前提与数据：真实受理事务提交后、首次交付前有屏障；受理前故障有独立反例。
- 操作：在提交后终止 Agent，保存原 requestId；恢复同库/Redis 与双 Worker；重发原请求。另对提交前中断重试。
- 预期：提交后的同一 Run/job 可补交付，只有一个用户命令回执；提交前事务整体回滚且重试可正常受理。Outbox、关键投影达到就绪后才派发，事件和最终答案可加载。
- 验证：实际进程记录、持久对象数量、调用数、HTTP 回执与同 Run Message。

### TC-03：所有接续入口遵循准入

- 关联需求：AC-01、AC-03、AC-07。
- 前提与数据：新 M6 parked Run；另从 0009 准备无 job 的 created/paused/waiting Run；A 执行槽或积压满额。
- 操作：分别使用 Resume、批准、外部事件及 v1 适配器；对满额旧 Run 先拒绝再释放容量后复用原 requestId 重试；对 M6 parked Run 在本租户积压已满时恢复。
- 预期：全部可执行入口只置 ready，未被 claim 时保持原可恢复状态和 queued 提示，不自行授予 epoch。新 M6 parked job 保留积压位置；旧无 job 的新增占位满额返回 429，原状态、未消费决定和请求键可重试。v1 共用配额，保留其断线取消约定；Web v2 离开页面后继续执行。
- 验证：各入口真实 HTTP、job/Run/Wait/命令差分及调用审计。

### TC-04：人工等待释放执行槽、批准一次

- 关联需求：AC-03、AC-07。
- 前提与数据：A 并发 1，广告预算审批 Run，A 另一就绪任务和 B 正常任务；原结果/文件已提交。
- 操作：首 Run 到达等待，检查资源清理与容量；待第二任务领取后批准首 Run，同时重复回调；待容量释放后继续原 Run。
- 预期：资源确认后首 job parked、不占执行槽且保留积压；后续任务推进。批准产生一个有效决定，原 Run ready 后受配额约束领取，沿用原 Wait/Action 和分析结果，效果一次；原产物仍可下载。
- 验证：Timeline 领取/确认释放区间、usage、Wait/Action、进程外效果计数和真实页面。

### TC-05：claim 回执丢失与 Worker 身份隔离

- 关联需求：AC-04、AC-05。
- 前提与数据：两个真实 Worker，claim 已提交但响应被阻断。
- 操作：同 workerId/boot 重试 claim，再由同 workerId 不同 boot 请求；交换服务凭据、修改正文 workerId、使用 BFF 用户凭据访问内部接口；Worker 凭据尝试用户 API。
- 预期：原 boot 取得相同 grant/epoch，claim_count 不增加；其他 boot 等待旧占用清理。同配置槽最多一条 leased/reclaiming。身份来自凭据，互换/伪造请求拒绝；grant 仅含执行标识和边界，无用户输入、数据源或模型秘密。无任务 204 带可用重试提示。
- 验证：HTTP 回执/拒绝载荷、只读 job 与 claim_count、物理调用数。

### TC-06：advance 双窗口丢响应及旧回执读取

- 关联需求：AC-05、AC-08。
- 前提与数据：边界有持久屏障，工具/模型由独立审计计数。
- 操作：分别丢弃 advance 受理响应和边界提交响应；反复提交同 grant/expectedCheckpoint，包含续租后的 expiresAt；边界完成并释放后读取原命令，再尝试以旧 grant 推进下一边界。
- 预期：同 Run/epoch/checkpoint 对应一个命令、一段执行；running 轮询不新建 Attempt；expiresAt 更新不改变命令身份。结果/Checkpoint/调度/回执原子收口，旧已提交回执只对原身份可读，旧 grant 无后继推进权；HTTP 断开保留原任务。
- 验证：命令/Attempt 数、真实依赖调用、回执与 Checkpoint、原 SSE。

### TC-07：等锁跨 TTL、过期续租与旧回调

- 关联需求：AC-04、AC-05、AC-06。
- 前提与数据：课堂 heartbeat 3 秒、TTL 15 秒；可控行锁和旧 delta 回调屏障。
- 操作：让 heartbeat、advance/业务提交分别等待行锁，锁在租约过期后释放；再用旧 grant 心跳和提交。新 epoch 建立后放行旧结果与 delta；另持续正常心跳记录控制版本。
- 预期：所有执行权检查采用取得锁后的数据库当前时间，过期返回 lease_lost。心跳仅改 expiresAt，不增加 Run.version、Outbox 或 Timeline。旧 epoch 无新业务提交、Checkpoint/成功消息覆盖或公开增量；已合法提交的旧 Outbox 可补发。补记原 Attempt 结局/费用保持其限定语义。
- 验证：数据库时刻、锁等待屏障、HTTP 结果、版本/Outbox 差分和完整消息；记录零迟到推进。

### TC-08：真实 Worker 终止与跨进程接管

- 关联需求：AC-04、AC-09。
- 前提与数据：Worker 1 已持久提交一个真实分析步骤；Worker 2 独立 PID/凭据/boot 可消费。至少包括普通分析与已有可核对动作效果两个场景。
- 操作：终止 Worker 1 的真实进程，记录最后确认心跳；等待 Agent 撤权、清理和 Worker 2 首次合法领取；提交 Worker 1 迟到回报。
- 预期：同一 Run/Action 保持；Worker 2 从有效 Checkpoint 继续，已完成分析与效果复用。Timeline 记录原领取、失效、确认释放/重新就绪及新领取原因；新 epoch 只在清理完成后建立，迟到推进零次，业务效果总数一次。
- 验证：PID 退出、两份 grant、Checkpoint/Attempt/Action、真实效果账本、最终 Message；受控可恢复夹具接管目标 ≤21 秒，按第 3 节设计实际计时。

### TC-09：执行超时与未知远端效果

- 关联需求：AC-05、AC-07、AC-09。
- 前提与数据：可重试无副作用步骤、MCP/模型已派发但远端结局未知、Sandbox 清理确认延迟三类夹具。
- 操作：分别超过配置期限或停止心跳；尝试其他 Worker 领取、重复 tools/call 与 Resume；核对恢复或终止状态。
- 预期：超时有安全原因与原 Attempt 结局，重试只按既有策略产生新 Attempt 并保持预算。未知效果不自动重复物理调用，保守费用/资源占额保留；resources 未确认时 reclaiming 继续占槽，其他有剩余容量的租户可推进。明确暂停、未决审批与核对受阻的状态不自动续跑。
- 验证：受控依赖的调用/清理计数、usage、Run 恢复说明和 Timeline；受阻样本保留在故障统计分母中。

### TC-10：取消与动作发起、批准及接管竞争

- 关联需求：AC-06、AC-03。
- 前提与数据：后继有受控业务写动作；屏障可控制动作派发、效果提交、批准、取消与 claim 次序。
- 操作：逐一运行取消先/动作先、取消先/批准先、取消先/接管先；覆盖未领取和正在执行任务；重复取消、投递及迟到提交。
- 预期：取消受理后后继新业务动作零次；先前合法提交的效果/结果保留可查。在途资源和效果完成核对后唯一合法终态；旧 grant/重复交付/Resume 无法重启终态。未领取无资源的 Run 直接收口；在途 reclaiming 保留占用到确认释放。
- 验证：取消受理与独立动作发起时间、Action 计数、状态/Wait/Timeline 与槽位；每种次序单独登记。

### TC-11：正常退出与 Agent 启动恢复

- 关联需求：AC-04、AC-06、AC-09。
- 前提与数据：同时准备 ready、leased、reclaiming、显式 paused、waiting、done；真实双 Worker 与 Agent。
- 操作：运行中向一个 Worker 发 SIGTERM；另一轮终止/重启 Agent；在恢复底座尚未就绪时尝试 claim/advance。保留一条已受理 running 命令但无本地执行任务。
- 预期：SIGTERM 后停止新 claim，有界收口期间维持允许的心跳；release 只请求释放。Agent 先恢复所有者/Redis/Outbox/投影，再撤销旧 epoch、处理旧命令和资源，完成复核后才开放调度。可安全恢复的中断任务 ready；显式暂停/审批/未知效果保持等待；done 仍可读且无新增执行。
- 验证：进程日志、内部接口、job 与命令状态、资源计数及历史视图；保留正常退出码。

### TC-12：双租户配额、公平、预算与用量

- 关联需求：AC-07、AC-09。
- 前提与数据：两个 Worker；全局并发 2、每租户并发 1、积压 4，全局积压 12；两租户各 4 个独立 Session。固定夹具保持 A 积压。
- 操作：并发受理 8 个任务，A 再持续提交超额请求；B 在空闲槽提交/领取。另以隔离配置或额外教学租户触发全局积压上限；锁住 A 候选 Session 检查跳过。运行 token/成本/步骤预算耗尽及未知模型成本样本。
- 预期：每租户 leased+reclaiming ≤1，全局 ≤2；活跃积压包含 ready/leased/reclaiming/parked，超限 429 无新 Run。满额/争用 A 不阻塞有容量 B，失败领取不改变公平次序；空闲槽无故障 B 首领目标 ≤3 秒。预算耗尽受控停下且恢复保留已用额；未知成本为 null 并保守占额。各状态计数、用量和释放可核对。
- 验证：真实并发 HTTP、按租户 usage/独立 SQL 与 Timeline 积分、首次领取 p50/p95/max、观察窗利用率及未知占额。平台/租户/普通成员的读取权限另见 TC-15。

### TC-13：断线续流、历史交接与短期流失效

- 关联需求：AC-02、AC-11。
- 前提与数据：长任务与包含接管/等待的 Run，保存最后已应用原生 Redis Stream ID。
- 操作：真实断开 SSE，在后台产生事件后重连；在 Message 读取与继续增量之间推进执行；重复已应用帧。分别处理终态流过期、活跃流丢失及 Redis 临时不可用。
- 预期：原 Run 继续，`runId:seq` 保持所属 Run 和两段整数序；历史与实时交接完整、重复幂等。终态过期 409 history_reload_required 后完整 Message 可读；活跃流丢失 410 stream_data_lost 后按 M5 进入恢复受阻，客户端重载并停止无效续流；依赖暂不可用 503，已有 SQL 历史仍可读。60 秒轮转/慢连接结束只影响订阅。
- 验证：真实 HTTP/Redis 与页面；完整 SSE/Message 导出经生产 validator/reducer 比较，核对无额外 submit/cancel/物理调用。

### TC-14：公开字段在实时、历史和重连一致

- 关联需求：AC-10、AC-02。
- 前提与数据：工具输入输出含可展示日期/维度/行数、受限明细和唯一泄漏标记；包含允许/禁止渠道名称的授权主体。
- 操作：记录浏览器真实实时响应、Message、重连及详情展开；变更当前数据权限后重新读取；逐帧运行相同 Schema/reducer。
- 预期：规则旁白和工具摘要遵守当前白名单，敏感标记不出现在响应载荷、DOM 或可复制详情。隐藏 view 仍使用相同 packetId/seq/revision 的安全对象，整帧 cursor 与最后包一致；权限范围改变后清除旧详情并重载。用户流不含 Worker 身份/boot/租约秘密、工具原文或 Checkpoint 密文。
- 验证：载荷结构与标记扫描、真实卡片、事件顺序与 reducer 全帧校验；展示范围来自授权夹具，保留被隐藏字段列表。

### TC-15：跨租户、诊断和用量权限

- 关联需求：AC-02、AC-08、AC-10。
- 前提与数据：原 Run 所属用户、其他租户/工作区用户、租户管理员、平台管理员、普通成员；部分角色没有模型调用权。
- 操作：分别访问 Message/SSE/控制/产物、timeline、runtime/usage，篡改租户参数；对已建立 SSE 撤权并再次订阅。恢复原权限后重新读取。
- 预期：跨作用域读取和控制均为安全错误且无内容。运维 Timeline 要当前 isPlatformAdmin；租户用量要当前 tenantRole=admin 与有效成员，仅当前授权租户可见。工作区管理权不扩大为租户统计权。撤权后新请求立即拒绝，既有连接按 ≤5 秒复查规则结束；恢复后仍遵守原控制和执行状态。
- 验证：真实 Web 权限变更/BFF/Agent、错误载荷、流关闭时间；finally 恢复全部原权限。

### TC-16：跨层 Timeline、模型 Trace 与观测故障

- 关联需求：AC-08、AC-09。
- 前提与数据：实际执行 Workflow→Step→工具/模型的 Run，包含一次物理 Retry 和一次原 Run Resume/Worker 接管。
- 操作：平台管理员读取 Timeline 与模型观测，再将外部观测出口设为测试不可用；继续任务并读取历史。另仅浏览/Replay 历史。
- 预期：Run、Workflow 节点、Step、Attempt、Tool Event、modelCallId/Trace 可关联；Retry 有新 Attempt，Resume 保留 Run，Replay 不派发。Timeline 稳定 ID 去重、含领取/接管/确认释放与终态；逐次 heartbeat 无新条目。出口异常时持久事件/用量可读，诊断不返回正文、原始工具输入输出、秘密与密文。
- 验证：实际模型请求/Trace 与 HTTP Timeline 的关联，物理调用数和已结算/未知成本；恢复原观测出口。

### TC-17：最终回答切换、一次折叠与手动偏好

- 关联需求：AC-11。
- 前提与数据：进度旁白故意含“最终回答”字样；完整答案成功提交前可延迟；桌面与 390 px。
- 操作：旁白期间展开工具详情；在成功批次前后断线/刷新；重复成功帧和快照；手动重新展开过程后再次续流。用键盘操作展开按钮，包含焦点在即将折叠的工具内的情况。
- 预期：普通旁白始终在过程区；整帧应用后同 Run 有 completed 助手消息与 completed 状态才切换。完整答案独立显示且各出现一次，过程默认折叠一次；用户展开选择保持，键盘焦点可到达。加载历史保留完整层级、并行组与源码，不重播已保存正文动画。
- 验证：真实页面及协议夹具边界回归分别登记；截图、键盘/焦点、实际 Frame/Message；遵循 [UI 规范](../../../docs/ui-style-guide.md)。

### TC-18：失败、取消、未确认答案及前序流程回归

- 关联需求：AC-08、AC-11。
- 前提与数据：部分模型输出后失败/取消、成功提交与取消竞争；M5 16 类业务矩阵与原图表/CSV。
- 操作：运行各终态并刷新/历史加载；故意断开确认链路；对矩阵新增调度字段完成同视图比较；重复历史 Replay。
- 预期：incomplete 回答显示其状态，不触发成功切换、不作为成功对话历史传入下一轮。已提交成功答案保留；过程与工具状态和最终状态一致。前序计划/Verifier/源码/Skill/SubAgent/审批/保存卡/文件保持，重复加载无物理调用。
- 验证：原有 Composer 和矩阵入口复跑，保留当轮 SSE/Message、实际模型上下文及原文件摘要；协议验证与真实浏览器结果分别记录。

### TC-19：Failure Matrix 与量化恢复报告

- 关联需求：AC-09、AC-04、AC-07。
- 前提与数据：正常负载及 Worker Crash、重复投递、超时、取消竞态四类已执行记录；独立时钟与效果审计。
- 操作：按 [Failure Matrix](FAILURE_MATRIX.md) 逐项核对触发点、原 Run、原/新执行权、不变量与结果；汇总固定双租户负载、故障和受阻任务；复跑课堂入口。
- 预期：每项均有通过/失败/未执行/受阻的可判定状态。报告含恢复分母/成功/受阻、排队 p50/p95/max、有效槽位利用率和清理隔离占用、资源/成本归集、零迟到推进与效果计数。21 秒/3 秒目标原样报告实际达成情况。部署 ADR 对比单进程、模块化 Runtime+Worker、独立 Scheduler/Queue/Worker Pool 三种形态及适用条件。
- 验证：本轮原始数据可按 Run/Action 复算；指标定义见下节，ADR 和运行文档由实施者交付后核对。

### TC-20：0009→0010、角色与协调恢复

- 关联需求：AC-03、AC-09、NFR-04。
- 前提与数据：owner `agent/main`；空库和含 0009 created/waiting/paused/completed/Artifact/Action 的旧库；原生迁移与派生 SQL；保留加密材料和隔离 Redis。
- 操作：分别空库安装、旧库升级；执行 SQL 一致性与角色权限检查；用运行身份访问新表/命令；恢复备份至独立库和隔离流；读取旧记录并通过批准/Resume 继续可恢复 Run。
- 预期：到达单一 0010 head；新表约束/索引及 worker_advance 合同生效，既有模型/会话/运行/文件/效果保留。升级不自动派发无 job 遗留 Run；满额接续按 TC-03。Worker 无数据库凭据，运行身份不能迁移/提权。恢复后 projected_seq、Outbox、Action 与效果一致，未能核对的活跃流保持受阻；原库和卷保留。
- 验证：复用真实 PostgreSQL 测试，按[数据库规范](../../../docs/database-migrations.md)核对空库/旧库/SQL/权限/恢复；记录备份来源和目标，秘密不公开。

### TC-21：交付入口、双 Worker 容器与最终回归

- 关联需求：AC-09。
- 前提与数据：实现与设计修订完成，新增测试已接入根入口，部署记录可重跑。
- 操作：实施者执行 Agent/Worker/Web lint/typecheck/test、contracts/contracts:check、适用 PostgreSQL 集成与完整 E2E；跨应用执行 `pnpm check`、`pnpm build`、相关 Compose profiles 与 smoke，容器模式双 Worker 各实际领取任务，再验证 Worker 正常退出。
- 预期：检查和真实业务全部按当前源码通过；双 Worker 有独立身份/boot、消费日志与最终业务结果；Worker 无业务库直连。README、配置样例、manifest 与 ADR 和实际入口相符。未执行或跳过项目单独登记。
- 验证：当前运行日志、容器/进程与 Run 标识、部署 smoke、课堂故障复跑结果；检查通过与业务结果分列。

## 度量与结果记录口径

- 接管成功率：需故障接管的全部 Run 为分母，已从合法持久位置完成接续为分子；未知效果、清理受阻单列并保留在总数中。另列无未决效果的受控可恢复子集，目标全部成功。
- 接管延迟：最后确认心跳停止至新合法 claim；记录清理区间、TTL、监督/轮询配置和实际最大值。课堂固定清理上界 2 秒时目标 ≤21 秒，超出直接记录目标未达成。
- 排队时延：Run.created_at 到 job.first_claimed_at，按租户报告样本数、p50/p95/max；空闲槽且无故障 B 样本目标 ≤3 秒。尚未领取的 Run 单列，不丢弃。
- 资源利用率：领取至确认释放区间在观察窗口内的累计时长 ÷（观察窗口 × 有效容量），清理隔离占用单独列出；跨窗口区间按窗口裁切。配额审计计入 leased 与 reclaiming。
- 成本：已结算 token/成本与未知保守占额分别列出；未知成本保留 null。效果只按稳定 Action ID 与进程外受控效果账本计数，费用和效果分别统计。
- 每次执行登记时间、源码/未提交范围、实际命令与 cwd、环境身份、退出码、Run/Session/Action 和观察结果。失败与修复后复验保留各自记录；文档格式检查仅记录在规划交付中。

## 待补齐事项

1. Web 8164、Agent 8165、双 Worker 和专用数据库已交接；服务及故障资源仍由主任务管理，旧 8100/L19 服务保持。
2. 调度集成和真实进程驱动正在执行；等待独立进程任务交接故障矩阵、量化结果及根入口检查结果。
3. 等待本轮 0009→0010 的空库、旧库、SQL 一致性、运行权限和备份恢复结果，核对原 Action、效果、文件及遗留 Run 的恢复。
4. Worker、BFF 权限矩阵、可控协议 UI、两项真实队列 UI、[四项流中权限变化集成](../../../apps/agent/tests/test_runtime_visibility_integration.py)及既有 Workflow 完整 UI 已通过；各类依赖故障和剩余前序回归按当前记录继续归并。
5. 主任务完成全量集成和部署后归并 `ACCEPTANCE.md`；当前本课完整 AC 结论待全部映射收口。
