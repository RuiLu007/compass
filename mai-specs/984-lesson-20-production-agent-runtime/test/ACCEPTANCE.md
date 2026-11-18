# 第 20 课执行记录

[返回需求简报](../BRIEF.md) · [业务测试计划](TEST_PLAN.md) · [故障矩阵](FAILURE_MATRIX.md)

## 双角色版本 · 2026-11-16

按 [PRD AC-01～AC-21](../spec/PRD.md#9-验收标准)验收。使用真实 Chromium、Web/BFF、两个独立 Agent 进程、单实例 Scheduler、PostgreSQL 17、Redis AOF 和两租户。模型供应商和经营数据使用隔离夹具，故障注入控制实际进程、数据库事务、HTTP 回执和 Redis 状态。容器检查另用独立 Compose 项目启动同一套角色。

| 验收条件                | 实际结果                                                                                                                                                                                                                               |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AC-01 接单与执行解耦    | 在消费槽占用时返回 202、可查询排队，在新增执行槽后同一 Run 开始消费。受理事务提交前中断时 Run、job、回执和 Outbox 全部回滚，恢复后只接单一次。                                                                                         |
| AC-02 游标续接          | 另一副本用原游标续流，19 包按序无重复；跨租户请求被拒绝。Redis 流或去重索引丢失、裁剪、回退到旧快照时返回 410，授权 Message 可用于重载。                                                                                               |
| AC-03 人工等待与恢复    | 等待确认时释放执行槽，B 租户可继续推进。重复批准复用回执，批准后同一 Run 回队列，旧审批授权及已存记录可读。                                                                                                                            |
| AC-04 跨 Agent 接续     | 杀死执行者和首个回收者后仍从原 Run／Checkpoint 接续。旧 epoch 写入被拒，已完成步骤复用。                                                                                                                                               |
| AC-05 重复投递与超时    | 提交后丢失 HTTP 回执、会话软删除、重领触发均复用原 Run。执行时间预算在进程更换后继续计量，未知外部动作只发生一次。                                                                                                                     |
| AC-06 取消竞争          | 跨副本取消、暂停由当前持租者处理，已受理取消阻止新动作，已提交效果保留。计划先改版时乐观锁拦截原工具写入。                                                                                                                             |
| AC-07 配额、公平与记账  | 真库校验积压 20／3000 与执行 200／1000，隔离热点租户行锁时 B 仍能领取。接管后的预算保留已结算及未知消耗。                                                                                                                              |
| AC-08 Timeline 与 Trace | 计划、触发、Run、步骤、物理 Attempt 和模型调用可在授权诊断中关联，通用 Workflow 的节点、子 Agent 和工具事件继续走 Run 过程链路。遥测出口故障时可读持久历史和 Timeline。                                                                |
| AC-09 故障验收          | [故障矩阵](FAILURE_MATRIX.md)记录新版注入及实际结果，量化样本见下文。验证空库、升级、权限、备份和四应用依赖关系，[部署 ADR](../../../docs/adr/0005-production-run-scheduler.md)记录选型及恢复顺序。                                    |
| AC-10 过程权限          | 使用实际鉴权回调验证读取、实时复查及重连。历史和实时流采用相同的可见性规则。浏览器复验撤权后加载的快照。                                                                                                                               |
| AC-11 最终回答          | Chromium 覆盖收完整回答后折叠、手动展开、重复帧、重连和未完成终态；从历史重读同一 Run 时只有一份最终回答。                                                                                                                             |
| AC-12 双入口管理        | 普通所有者在 Web 新建、Chat 查询和修改，刷新读取同一 ID 和版本；Chat 创建可由 Web 管理。覆盖搜索筛选、390 px、键盘焦点、拒绝确认、同名澄清、版本冲突、隔离和已删除历史。                                                               |
| AC-13 到期、幂等与重启  | 到期扫描后和受理后分别中断。Scheduler 停机期间即时 Run 继续，替换后恢复同一触发，旧令牌失效。纽约夏令时前进跳过，回退时取较早时刻；漏跑合并，已有未结束 Run 时跳过。                                                                   |
| AC-14 共用配额与授权    | A 租户的即时和定时任务达到同一积压上限，B 继续。退避遵循 Retry-After，持续超限到期后仅核对；真实撤权的触发未新增 Run。专用凭据的数据库与 HTTP 越权测试通过。                                                                           |
| AC-15 变更与到期        | 改期、停用、删除在交付前作废旧触发；交付开始后沿用快照。已关联 Run 可独立取消，已删除计划保留授权历史，定时 Run 不装配计划写工具。                                                                                                     |
| AC-16 结果同步与查询    | 成功、失败、等待确认、取消和待核对可区分。中断后补写遗漏终态，历史可跳原会话，授权诊断可见关联。                                                                                                                                       |
| AC-17 部署与回退        | 双 Agent 容器和单 Scheduler 健康，Scheduler 单独停启期间 Agent 继续就绪。实际从 0014 升级并保留游标和 Checkpoint；轮换旧写凭据后旧角色被隔离。0014 备份在独立环境恢复消费，0015 触发数据留存。                                         |
| AC-18 多副本并行        | 相同配额和依赖下并行数 1→2，第二副本启动保留首条有效租约。不同共享策略的副本拒绝就绪和接单，本地槽位及上游额度可独立配置。                                                                                                             |
| AC-19 跨副本与事件续接  | 每条 Run 只有一个有效 epoch。双故障后从原边界恢复，未知效果保留占用，旧领取、回调与发布世代被拒。完整 Redis 流支持跨副本续接，丢流时安全回到 Message 或待核对。                                                                        |
| AC-20 确认后接管        | 事务开始前、已写入未提交、提交后未回执三个窗口强杀。计划只增长一个版本，工具 Attempt、Action 和 Checkpoint 与写入同时可见。撤权、冲突、混合并行批、16 个工具、64 KiB 和换节点目录冻结通过。                                            |
| AC-21 容量和失败归因    | 合成边界请求与原请求键复用通过。自动选模预览显示有效路由，固定模型检查当前资格和工具能力。浏览器区分准入 429、延期、已受理后的上游限流及固定模型不可用；MCP 连接停用后，无新物理调用。原触发在容量恢复后接单，历史和刷新保持同一 Run。 |

### 量化样本

| 指标           | 实测                                                                                                                                            |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 同配额占用峰值 | 单副本 1／1 个槽；双副本 2／2 个槽。                                                                                                            |
| 恢复时长       | 单次强杀约 13.94 秒；加上首个回收者强杀约 29.15 秒。三个计划写入故障窗口从最后心跳到重新领取为 15.42～16.62 秒。                                |
| 恢复结果       | 可安全接续的后端样本 2／2 完成；第三项外部效果未知，按预期保持受阻。计划写入三个窗口 3／3 完成，仅提交一次。                                    |
| 排队和锁等待   | 7 条受控 Run 从受理到首次领取 p50 约 0.825 秒、p95 约 13.352 秒，包含等待副本启动。3000 条积压且最早 48 条存在行锁时，其他租户领取约 0.645 秒。 |
| Outbox 增量    | 单 Run 162 个公开 revision 以 9 批交付；最老未发布记录约 0.364 秒。                                                                             |

### 回归与复跑

- 当前应用 lint、类型和单测通过，Agent 356 项、Web 151 项、Memory 1 项、数据工具 34 项。Web PostgreSQL 集成 22 项通过。后端双副本、调度与旧库对照独立矩阵 30 项分别通过。
- Web／Chat 独立浏览器验收 15 项、既有 Chat 协议回归 14 项、真实链路 smoke 2 项通过。另复跑 CI 真队列浏览器入口，3 项通过，覆盖 SSE 与历史一致、普通分析和预览保存后键盘删除。更多执行日志位于本地 `work/984-acceptance/`（忽略目录）。
- 验证本地应用构建、迁移 SQL 及网络契约生成一致性。空库、旧库升级、错误版本拒绝、原生迁移与派生 SQL 对照、恢复密文与权限均通过。
- 使用隔离 Compose 项目运行部署、状态、日志、下线和重复迁移及 seed。验证 Web → BFF → Agent → PostgreSQL 的三天合成数据以及 Memory readiness。OpenSandbox／NFS、隔离数据库与 Redis 的定向复验覆盖初轮失败项，Sandbox 启动或清理超时样本在降低并发后原断言通过。
- 纳入 CI 的真进程用例从根目录运行 `PYTHONPATH=apps/agent/tests/replica_e2e uv run --locked pytest -q apps/agent/tests/replica_e2e/runtime_e2e.py`。浏览器真队列回归入口为 `production-runtime-live.spec.ts`，本地部署入口见[运行说明](../../../docs/lesson-20-production-runtime.md)。

### 本地验收边界

本次采用停旧角色、隔离写凭据、升级并启动新角色的切换。回退演练使用升级前的 0014 PostgreSQL／Redis 备份及旧构建。新部署包只提供内部消费模式。根 `pnpm check`、`pnpm build` 的本地验证覆盖本次受影响应用及独立根检查。Home 全量任务涉及本次 Feature 之外的课程素材，开发一键启动复验覆盖命令与启动合同。容器验收在本机隔离环境执行，远端 CI、真实供应商及生产发布待验证。

## 历史验收 · 双 Worker 基线

以下记录对应 2026-11-12 的双 Worker 环境。

## 当前范围

2026-11-12：AC-01～AC-11 本地实现与验收全部通过。Worker 消费者 27/27、Web 新增单测 26/26、可控 UI 9/9、真实队列 UI 2/2、Workflow 回归 1/1、权限/观测组合 5/5、实际进程与预算/超时验收均通过；其他本轮完整集成结果见下表。

基线为 `9b6220525f257a122c132471688c6ad112ea61d8` 加当前第 15～20 课未提交实现。主任务负责生产代码；独立测试负责本目录、`apps/worker/tests/test_run_consumer.py`、`apps/worker/tests/test_worker_process.py` 及本轮新增的四个 Web 测试文件。

## 当前 AC 归并

M6 调度、真实 Worker 进程和 UI 的结果与本轮 M5 持久底座回归共同核对；表中明确实际验证层次。可选模型旁白及分段答案未启用，按已评审的规则旁白、一次完整答案方案验收。

| AC                       | 当前结果                               | 本轮验证映射                                                                                                                                    |
| ------------------------ | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| AC-01 接单与执行解耦     | 通过                                   | M6 原子受理/回滚、所有入口仅排队、启动未完成命令恢复；真实 submit 202 和同 Run 消费；M5 Outbox/投影丢回执持久交付                               |
| AC-02 游标续接与隔离     | 通过                                   | 真实两 Run SSE/Message 完整比较与重复帧；queued 重连 UI；M5 游标/流丢失/跨作用域拒绝；当前权限关闭和重连                                        |
| AC-03 等待释放与异步批准 | 通过                                   | 真实预算 parked 释放槽位、批准后原 Run 二次领取；M6 parked 保留积压和旧 M5 无 job 准入；重复批准/效果一次、0009 升级读取                        |
| AC-04 真实 Worker 接管   | 通过                                   | 三次 SIGKILL、原 Checkpoint/Attempt 接续、迟到执行拒绝；M6 已提交效果监督核对与 M5 实际效果提交后进程退出回归                                   |
| AC-05 重复与超时         | 通过                                   | M6 claim/advance HTTP 重试与旧回执、真实 PG 等锁跨 TTL；模型物理超时/保守费用；未知效果实际一次、无自动重放                                     |
| AC-06 取消竞争           | 通过                                   | M6 领取前/后取消及后继派发拒绝；本轮 M5 批准与取消/到期真实并发、效果窗口与唯一终态回归；保留已提交效果                                         |
| AC-07 租户治理           | 通过                                   | 两租户各 4 轮、A 第 5 次 429、租户 1/全局 2、B 首领 2.111228 秒；真实工具预算停止及下一 Run 领取；当前租户用量/保守费用                         |
| AC-08 Timeline/Trace     | 通过                                   | M6 Run/Step/Attempt/modelCall/命令关联和授权；M5 真实 ModelService Retry/跨重启 Trace；FM-22 出口失败时真实完成与持久读取                       |
| AC-09 M6 验收交付        | 通过 | Failure Matrix 已登记当前结果，3/3 安全恢复、整体 3/4、排队和利用率可复算；迁移/构建/check/Tool、五应用容器 smoke 与双容器 Worker 消费通过；三形态 ADR 已交付 |
| AC-10 当前权限过程输出   | 通过                                   | 原始秘密移出 Outbox、同对象安全投影；Web 26 单测与 UI 过滤；真实权限路由、四项实际 5 秒流复查与重读                                             |
| AC-11 最终回答与折叠     | 通过                                   | 9/9 可控 UI、2/2 真实队列 UI、完整 Workflow 回归；结构化成功门禁、重复/重连、手动展开、失败/取消与不完整历史                                    |

## Worker 隔离测试首轮

在 Compass 根目录运行，所有 HTTP 由 `httpx.MockTransport` 提供受控异步响应。子进程仅运行本次自建测试入口并注入同一 MockTransport，使用独立合成 token/workerId；未访问现有服务、数据库或既有进程，未执行 Web/Agent 业务。

| 命令                       | 实际结果                                                                                           | 记录                                             |
| -------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `pnpm lint -F worker`      | 通过，7 个文件格式已符合规则                                                                       | `work/lesson-20/worker-acceptance-lint.log`      |
| `pnpm typecheck -F worker` | 首轮失败，生产与测试各 1 处 `self.grant` / `consumer.grant` 的 None 收窄问题；测试问题已修复待复验 | `work/lesson-20/worker-acceptance-typecheck.log` |
| `pnpm test -F worker`      | 26 项中 24 通过、2 失败，0.45 秒；失败为续租响应改变 runId/epoch 后仍继续推进                      | `work/lesson-20/worker-acceptance-test.log`      |

覆盖超时后同 Checkpoint 重试、expiresAt 更新保持原边界、心跳鉴权失败/lease_lost 中止在途请求、网络不可用到保守截止停止推进、慢 claim 不延长租期、不同 worker/boot 拒绝、advance lease_lost 停止、停止信号边界排空与单次 release、空队列停止、Checkpoint 单调整数、token 日志隐藏及每实例新 boot。真实 SIGINT/SIGTERM 两项均通过，测试期间没有网络连接。

### 待修复：续租执行身份变更

用例 `test_heartbeat_cannot_replace_active_execution_identity[runId]` 和 `[epoch]` 失败。MockTransport 在心跳回执中保持 workerId/bootId，却返回不同 runId 或 epoch；消费者接受了该回执，随后用变化后的执行身份继续发送 advance。当前 `accept_grant` 核对 workerId/bootId 与 UUID 格式，缺少续租与现有执行身份的一致性校验。

期望为拒绝续租回执并停止原消费循环。生产修复交主任务处理，失败断言保持。此结果覆盖 Worker 客户端身份校验，不替代 Agent 服务端拒绝旧执行权或真实双 Worker 接管验收。

首轮 typecheck 同时发现生产 `drive` 从可能为空的 grant 读取 checkpoint；测试用例自己的同类问题已增加显式收窄。修复后的当前结果另行追加。

## Worker 第二轮：续租越过本地截止

主任务修复续租 runId/epoch 绑定和 `drive` 的非空收窄后，独立复跑 `pnpm lint -F worker` 与 `pnpm typecheck -F worker` 均通过，类型检查 0 错误/0 warning。原两个身份变更用例通过。

新增受控单调时钟测试：初次 grant 的本地保守截止为 114.5，heartbeat 在 101 发出，到 115 才成功返回。实现接受回执后把截止更新为 115.5，随后发送了第二个 advance。用例 `test_heartbeat_success_after_local_deadline_does_not_revive_lease` 因未抛出 LeaseLost 失败；预期是在旧本地截止到达后停止原轮次，新到的成功回执不能恢复原执行许可。已回传主任务。

本轮 `pnpm test -F worker` 共 27 项，25 通过、2 失败，0.67 秒；除上述生产问题，另一项为新增 queue idle 断言的测试屏障不足：SIGTERM 在 idle 日志出现前发出。测试夹具已改为读取到真实 queue idle 日志再发信号，保留消费与退出断言；定向两种信号测试 2/2 通过，0.50 秒。

第二轮原始记录为 `work/lesson-20/worker-acceptance-lint-retest.log`、`worker-acceptance-typecheck-retest.log`、`worker-acceptance-test-retest.log`。全部仍使用 MockTransport，自建子进程未访问网络。

## Worker 本切片最终复验

主任务在接受成功续租回执前检查旧本地截止，随后本子任务完整复跑 Worker 三项。`pnpm lint -F worker` 通过；`pnpm typecheck -F worker` 为 0 错误/0 warning；`pnpm test -F worker` 为 **27/27 通过**，0.52 秒。原身份变更、迟到心跳及信号消费断言全部保留。

记录为 `work/lesson-20/worker-acceptance-lint-final.log`、`worker-acceptance-typecheck-final.log`、`worker-acceptance-test-final.log`。该切片关闭 Worker 客户端已发现问题；服务端租约、资源和效果核对仍按真实故障矩阵执行。

主任务随后修复正常 release 与 heartbeat 的退出竞争，再次执行当前 Worker 全套单测 **27/27 通过**，0.24 秒，见 `work/lesson-20/worker-test-current.log`。

## Web 单测与浏览器准备

新增 `apps/web/tests/production-runtime.test.ts`、`apps/web/tests/e2e/production-runtime.fixture.ts` 和 `apps/web/tests/e2e/production-runtime.spec.ts`。前端协议和 BFF 用当前生成 Schema/生产 reducer/实际处理器验证；浏览器场景复用真实 Web 身份与产品页面，Agent v2 响应通过每个浏览器独立的协议夹具控制。

14 个单测覆盖 queued 的 created/paused/waiting 状态、完整助手消息与 Run 共同成功门禁、缺失助手或 incomplete 的成功抑制、完整帧与重复去重、失败/取消不进入成功历史、权限变化后的安全快照替换、内部执行字段拒绝、Resume 的 429 不自动重试、BFF 安全背压/可信范围及拒绝越权。

首轮 429 夹具遗漏 ModelErrorResponse 的 requestId，另使用了不允许明文 HTTP 的非本机 origin；均修正为真实协议字段和合法 HTTPS 测试 origin，生产校验保持。当前 `pnpm test -F web -- tests/production-runtime.test.ts` **14/14 通过**，66 ms，见 `work/lesson-20/web-runtime-unit-retest.log`；新增三个测试文件 ESLint 零 warning，Web `tsc --noEmit --incremental false` 通过。

主任务随后提供 Web 8164 / Agent 8165 和独立数据库，尚未启动 Workers。当前仅执行新 spec 的 9 个 UI 场景，命令使用 `work/lesson-20/run-env.py` 注入已有隔离环境、`WEB_TEST_EXTERNAL_SERVER=true`，不启动或停止服务；输出限定 `work/lesson-20/runtime-ui-results`。真实队列浏览器等待主任务通知 Workers 就绪。

### 浏览器首次运行受阻

执行 `python3 work/lesson-20/run-env.py pnpm test:e2e -F web -- production-runtime.spec.ts --reporter=line --output=../../work/lesson-20/runtime-ui-results`，命令退出 1，5.541 秒。9 项均在首次 `GET /api/web/v1/session` 收到 `ECONNREFUSED 127.0.0.1:8164`，尚未进入场景操作；归类为环境受阻。本子任务未尝试启停服务，已通知主任务恢复隔离 Web。

原始记录 `work/lesson-20/runtime-ui-first.log`，首次 trace/截图保存在该次输出目录。业务预期与断言保持，服务恢复后复跑。

## Web 权限增量复验

在原 14 项基础上新增 12 项，使用实际 BFF 处理器和 `createModelAuthorizer`，仅 Identity/Access 存储使用固定夹具。

- Timeline GET 必须请求平台作用域的 `runtime_diagnostics`；usage GET 必须请求当前租户/工作区的 `runtime_usage`。校验上游可信 purpose/scope、只读路由及拒绝授权后零上游请求。
- 普通成员即使具有 `module.admin.runs` 权限仍拒绝 usage；平台管理员缺少有效租户 membership 仍拒绝 usage。
- 租户管理员可读本租户 usage，拒绝平台 diagnostics；平台管理员可读 diagnostics，无需查询租户 membership/workspace。
- 归档工作区或不属于当前租户的工作区拒绝 usage。

`pnpm test -F web -- tests/production-runtime.test.ts` **26/26 通过**，73 ms；记录为 `work/lesson-20/web-runtime-auth-unit.log`。新增三个测试文件再次通过 ESLint 零 warning 和 Web `tsc --noEmit --incremental false`。这些测试覆盖 BFF 与权限决策契约；真实角色变更、既有 SSE 的 5 秒权限复查仍由 TC-15 验证。

## 浏览器隔离复验

主任务确认 Web 8164 / Agent 8165 就绪后，使用同一外部服务入口复跑，输出为 `work/lesson-20/runtime-ui-retest-results`。完整轮 **8 项通过、1 项失败**，耗时 1 分 40.814 秒；记录为 `work/lesson-20/runtime-ui-retest.log`。

| 场景                                                   | 当前结果     |
| ------------------------------------------------------ | ------------ |
| 1440 px queued 刷新仍沿用原 Run，随后完成              | 通过         |
| 390 px queued 与完成，无横向溢出                       | 通过         |
| 429 保留输入、不自动重试，手动重试复用请求键           | 通过         |
| 旁白不触发回答切换，完整成功帧去重、默认折叠与焦点回移 | 通过         |
| 完成前手动展开，成功后保持用户选择                     | 通过         |
| queued 断线重连、完成与刷新，无再次提交                | 通过         |
| 当前安全快照替换旧工具详情，响应和 DOM 清除旧明细      | 通过         |
| failed 的 incomplete 分析保留，刷新后无成功回答        | 通过         |
| canceled 的 incomplete 分析保留，刷新后无成功回答      | 定向复验通过 |

完整轮 canceled 项在共同 submit 步骤遇到真实 `/api/web/v1/session` 探测超时，页面收起受保护内容；trace 记录该请求耗时 5043.724 ms、`net::ERR_ABORTED`，尚未进入 canceled 状态操作。保留原断言，随后以 `--grep 'canceled leaves'` 定向复跑，**1/1 通过**，11.3 秒；记录为 `work/lesson-20/runtime-ui-canceled-retest.log`，输出为 `work/lesson-20/runtime-ui-canceled-retest-results`。此前一次带起始锚点的 grep 未匹配 Playwright 完整标题，未运行测试，见 `runtime-ui-canceled.log`。

服务及构建稳定后再次执行完整 `production-runtime.spec.ts`，**单次 9/9 通过**，任务耗时 1 分 53.956 秒，记录为 `work/lesson-20/runtime-ui-final.log`，输出为 `work/lesson-20/runtime-ui-final-results`。本结果为该组当前最终结果。

上述 UI 使用真实页面和 Web 登录，Agent 事件由每个浏览器上下文的独立夹具控制，覆盖 UI 对协议状态的处理。真实排队、配额、当前权限过滤和故障恢复仍按 TEST_PLAN 与 FAILURE_MATRIX 等待 Worker 环境交接；本轮未启停既有服务或修改数据库。

## 真实队列浏览器验收

主任务交接 Web 8164、Agent 8165、两真实 Worker 及隔离 PostgreSQL/Redis 后，新增 `apps/web/tests/e2e/production-runtime-live.spec.ts`。仅模型列表固定为现有受控供应商，分析受理、控制、SSE、Message、Retail 查询、Worker 消费及用量/诊断全部经过真实 BFF/Agent。页面以 teaching.admin 登录；平台诊断用独立请求上下文的 teaching.platform，未调整账号权限或共享 Workflow 资产。

执行入口为 `python3 work/lesson-20/run-env.py pnpm test:e2e -F web -- production-runtime-live.spec.ts --reporter=line --output=../../work/lesson-20/runtime-live-third-results`。最终 **2/2 通过**，31.6 秒，见 `work/lesson-20/runtime-live-third.log`。

| 场景                               | 实际结果                                                                                                                                                                                             | AC/用例映射                    |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| 周报受理后刷新、完成后历史重读     | 受理 202；同 Run 经 queued→claimed→done，claimCount 为 0→1→1；刷新后仅一份完成回答，过程默认折叠；页面只发一次 submit                                                                                | AC-01/02/08/11；TC-01/13/16/17 |
| 预算提案等待、刷新、批准           | queued→claimed→parked，waiting 时 occupied=0、parked 与 backlog 各比基线增加 1；刷新保留原提案；批准后同 Run queued→claimed→done，最终 claimCount=2，只有一个 decision 命令，backlog/parked 恢复基线 | AC-03/07/11；TC-03/04/12/17    |
| 两轮实际 SSE/Message 和只读 Replay | 从原 Run 的 0-0 读取真实 SSE，用生产 validator/reducer 与完整历史比较一致；重复帧幂等；完成后刷新及重放前后 Timeline 条目完全相同，无新执行命令                                                      | AC-02/08/10/11；TC-13/14/16/18 |
| 真实管理权限                       | 租户管理员可读当前租户 usage、读 Timeline 返回 403；独立平台管理员读 Timeline 返回 200；公开 Message 无 workerId/bootId/epoch/privateCheckpoint 字段                                                 | AC-08/10；TC-14/15             |

周报 Run `9ab380fd-c9de-437f-9477-184693b6462c` / Session `9c5935e4-1e74-4c4a-8240-95be15391b28`，完整快照 45 包、真实 SSE 6 帧、10 个命令（submit 1、worker_advance 9）。预算 Run `21dfd5cf-ea68-44d9-b5c8-3384f9b818a4` / Session `c7d04413-b523-43bc-88b4-4be38ff9f0a6`，完整快照 32 包、真实 SSE 4 帧、8 个命令（submit 1、worker_advance 6、decision 1）。两轮最终 occupied=0，已有旧测试 paused Run 的 backlog=1 保留。结束后通过相同只读 API 导出 `work/lesson-20/runtime-live-current-business.json`，包含当前两轮实际 SSE、Message、Timeline、用量。测试入口也保存后续每轮的 `runtime-business-result.json`。

首轮 `runtime-live-first.log` 的两项失败已分别处理：测试 helper 按 snapshot 包物理顺序误判调度次序，改按业务 ordinal 排序；真实 usage 上游 404/BFF 503 由主任务定位为 diagnostics Router 未挂载并修复。第二轮 `runtime-live-retest.log` 已完成周报和预算批准，随后因测试用租户管理员访问平台诊断遇到正确的 403；测试增加独立平台身份并保留租户拒绝断言。各次业务预期保持，原失败记录保留。

四个新增 Web 测试文件的 ESLint 零 warning，Web `tsc --noEmit --incremental false` 通过。该轮覆盖单租户正常消费和审批；双租户公平、积压上限及真实进程故障由后续独立报告追加。

## 独立进程故障与量化验收

独立进程任务运行 `apps/agent/tests/test_run_scheduler_process.py`，**1/1 通过**，137.27 秒。记录 `work/lesson-20/process-test.log`，完整数据 `work/lesson-20/process-acceptance.json`。使用真实 PostgreSQL、Redis、HTTP Agent 与两个独立 compass-worker 进程；受控执行器只实现本地 sum/double 两步和独立 HTTP 效果服务，未调用外部模型。Ruff、格式、Pyright 和 diff 检查通过。

正常负载为两租户各 4 个独立 Session，8 轮全部完成；A 的第 5 次受理返回 429 且无额外 Run。1523 次采样中全局占用峰值 2、每租户峰值 1。B 首次领取耗时 **2.111228 秒**，达到 ≤3 秒目标。按最近秩口径，A 的排队 n=4、p50=6.055861 秒、p95/max=16.166829 秒；B 的 n=4、p50=6.048310 秒、p95/max=16.183484 秒。等待已有任务释放槽位的后继样本保留在统计中。

| 实际 SIGKILL Run                     | 被终止 PID | 最后心跳至新领取 | 实际恢复                                       |
| ------------------------------------ | ---------- | ---------------- | ---------------------------------------------- |
| c08c2439-8ed4-45aa-830a-bc522dfe873e | 49061      | 18.605460 秒     | worker-b→worker-a、新 epoch、Checkpoint 1 接续 |
| 4a6a8a8c-85b0-47ed-b80d-5c7d35dc519c | 49060      | 17.564396 秒     | worker-a→worker-b、新 epoch、Checkpoint 1 接续 |
| 4a95eea9-2903-4c60-9df2-277e82de9fa0 | 51181      | 15.717994 秒     | worker-b→worker-a、新 epoch、Checkpoint 1 接续 |

三个 Run 均保持原已提交 Attempt，实际 sum/double 各执行一次，最终 Message 为 380。旧 heartbeat/advance 返回 409，迟到推进 0。TTL=15 秒、heartbeat=3 秒；接管 n=3，p50=17.564396 秒，p95/max=18.605460 秒，达到 ≤21 秒目标。

未知效果 Run `9e8c3f7b-ad21-4585-b40b-e5ad14025f48` 的独立 HTTP 效果已提交一次；回执丢失后保留 dispatched Attempt 和空 output，Run 为 blocked、job 为 reclaiming、epoch 已撤销、claimCount=1，没有再次派发。A 的后继保留 ready/claimCount=0，B 可使用另一槽并完成。usage 中 A occupied=1；该夹具没有模型调用，模型 token/费用值保持 0。

需恢复样本共 4：安全恢复 3、未知效果保守受阻 1；安全子集成功率 3/3，全部样本恢复率 3/4。48 个调度事件覆盖观察窗口 126.483661 秒；容量分母 252.967322 slot·s，占用 130.553087 slot·s，利用率 **51.6087%**，其中 reclaiming 占用 9.606331 slot·s。未知样本保留在恢复分母和资源占用统计中。

## 主任务交接的当前门禁

| 本轮入口                           | 当前结果                                                                            | 记录                                                    |
| ---------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Agent 调度集成                     | 30 项通过，116.48 秒                                                                | `work/lesson-20/scheduler-integration-final.log`        |
| Agent 数据库集成                   | 22 项通过，115.40 秒                                                                | `work/lesson-20/database-integration-final.log`         |
| `pnpm check`                       | 19/19 任务通过，含 Agent 345、Web 145、Worker 27 单测                               | `work/lesson-20/check-final.log`                       |
| `pnpm build`                       | 7/7 任务通过                                                                        | `work/lesson-20/build-current.log`                        |
| M5 durable core                    | 18/18 通过，47.29 秒                                                                | `work/lesson-20/durable-core-final.log`                 |
| M5 durable faults/cases/boundaries | 13/13 通过，147.43 秒                                                               | `work/lesson-20/durable-faults-final.log`               |
| M6 readiness gate 定向             | 1/1 通过，5.23 秒                                                                   | `work/lesson-20/scheduler-readiness-final.log`          |
| Tool 独立真实集成                  | 19/19 通过，388.17 秒；本轮 16 个 host allocation 全部 released/clean，remoteLive=0 | `work/lesson-20/tool-integration-isolated.log`          |
| Model/Workflow 集成                | 19/19 通过，8.33 秒                                                                 | `work/lesson-20/models-workflows-integration-final.log` |

M5 durable 的当前通过结果覆盖真实 Redis AOF SIGKILL/OOM/owner connection loss、效果提交后真实进程退出、模型进程中断、16 类业务矩阵、批准与取消/到期竞争、可信外部事件去重、真实 ModelService Retry/Trace 跨重启及 QuickJS host 进程强杀。M5 进程夹具显式使用原执行路径；M6 的 Worker/队列由新调度集成、真实进程和浏览器独立验证。历史迁移 52 份 hash 保持，CI 已接入双 Worker 与新 E2E；本轮未远端执行 CI。

M6 readiness 测试验证依赖未就绪时旧 committed 回执仍可只读访问，新边界返回 503 且无新增 command。Tool 真实集成结束后 NAS/控制面仍 running，保留旧残余，未暂停或清理既有资源。最终 check 19/19、build 7/7、五应用容器和双 Worker 消费验收通过。Agent 集成按隔离环境分组完成 130 项；首轮全量失败/中断记录保留。

## 实时撤权与展示版本变更

新增 `apps/agent/tests/test_runtime_visibility_integration.py`，使用每项独立 PostgreSQL 数据库、真实 Redis Streams、真实 `sessions.events` 的 StreamingResponse 迭代器及 `messages` 入口；当前授权 callback 提供可控变化。测试保留生产 5 秒复查节拍，未修改时钟或权限期限。

| 活动流中的当前授权变化             | 实际关闭耗时 | 结果                                                |
| ---------------------------------- | ------------ | --------------------------------------------------- |
| allowed→forbidden                  | 5.081 秒     | 正常结束，SSE 重连与 Message 新读取均拒绝           |
| 授权服务不可用                     | 5.075 秒     | 正常结束，后续请求按当前不可用返回                  |
| details 保持允许、仅 revision 变化 | 5.087 秒     | 原连接结束，新连接/历史仍呈现当前允许内容           |
| revision 变化且 details 收紧       | 5.093 秒     | 原连接结束；新 SSE 与 Message 同步隐藏结果 DTO 明细 |

四项均满足关闭耗时 ≤5.5 秒。重新读取保持原 packetId、seq、对象 ID、revision 与游标；Run.version、projected_seq 和数据库保存的完整投影不因权限检查改变。受限输出不含 rowCount，原始工具秘密在进入 Outbox 前已去除。

执行 `python3 work/lesson-20/run-env.py uv run --locked --package compass-agent pytest apps/agent/tests/test_runtime_visibility_integration.py -q -s`，**4/4 通过**，26.02 秒，见 `work/lesson-20/runtime-visibility-integration-retest.log`。该文件 Ruff/格式/Pyright 通过；主任务已加入 Agent 集成入口和普通单测排除列表。首轮夹具遗漏 claim 前初始 Outbox 发布，并在 JSON 字段检查中遗漏 `by_alias=True`，记录保留在 `runtime-visibility-integration.log`；修正夹具后原权限、计时和身份断言全部保留。

本轮覆盖 FM-20 与 TC-14/15。真实 Web 当前角色判定由 BFF 权限单测和真实管理路由验证；流关闭实验从当前授权回执变化开始计时。

## 既有 Workflow 全链路回归

执行前通过真实 BFF 确认 Workflow 资产 `items=[]`，符合原用例前提。首次 `hybrid-workflow.spec.ts` 在通用 15 秒等待处超时；当时页面仍为 running，商品贡献/口径复核正在推进。随后只读 Message 确认同 Run `46765252-ef6c-42e5-8181-4c9b9b700760` 已 completed、version=33、cleanup=confirmed，未出现权限暂停，见 `work/lesson-20/hybrid-m6.log`。

针对 M6 多边界异步执行，将该既有测试的最终回答等待设为 45 秒、整个跨五轮测试设为 180 秒，业务断言保留。复跑 **1/1 通过**，51.3 秒，见 `work/lesson-20/hybrid-m6-retest.log`。实际完成生成并保存 JS、源码查看、改名与启停、390 px 自动换行和键盘交互、换时段复用、自然语言自动选择原 Workflow、固定预算提案拒绝及 Skill 周报，满足 TC-18 的主要前序 Workflow UI 回归。

用例结束后保留其创建的「渠道每周核对」v1 已启用资产。重复此用例需要重新满足其空资产前提；本子任务未删除已创建资产或清理主任务的旧 Run。

## M6 执行预算与模型超时

独立任务新增 `apps/agent/tests/test_run_scheduler_limits.py`，**2/2 通过**，10.69 秒；记录为 `work/lesson-20/limits-test.log`，实际数据为 `work/lesson-20/limits-acceptance.json`。Ruff、格式、Pyright 与 diff 检查通过；运行出现 1 条第三方 Pydantic ReadOnly warning。

- 工具预算：真实 RunService/Retail SQL，Run `d2cc4f9f-0460-4819-b64e-bc6eaffe3dcc` 跨 Checkpoint 1～4、4 个唯一 AgentAttempt，仅 1 次物理 SQL 和 2 次受控模型调用。工具预算用量 1/1 后以 `budget_exhausted/max_tool_calls` 收口为 failed；已完成查询 partial 保留。job=done、occupied=0，后继 Run `3c2525b9-92c5-4fbe-adc4-3cf9a37a3acf` 完成。
- 模型超时：真实 ModelService 与受控 Provider，Run `800a9192-f5b2-413d-b4cb-081cec722747` 的上游延迟 3 秒、attempt_timeout=1 秒、maxAttempts=2。实际请求 1 次，物理 Attempt 为 incomplete/model_timeout，sent/outcome_unknown/cleanup_confirmed；actualCost=null。用量保留 unknownAttempts=1、reservedTokens=192、reservedCostMicrousd=768；job=done、occupied=0，后继 Run `dec2b744-55e2-448f-8b1c-bdab4a0dea3d` 完成。

对应 AC-05/07、TC-09/12、FM-10/11；保守费用与已确认释放的执行槽分别核对。

## 观测出口失败期间的持久读取

在 `test_runtime_visibility_integration.py` 增加 FM-22 组合实验，使用真实 M6 队列、PostgreSQL、Redis 与一项可观察的物理计算。计算在途时强制同一 Observer 出口失败，随后放行并完成原 Run。实际 exporter_failures=3、物理计算=1、Timeline=8 条，Trace ID 在多边界和终态读取中保持；SSE、完整 Message 和平台 Timeline 继续可读。重读/Replay 后 command、Attempt 与物理调用数均不增加；错误用途与撤权主体均被拒绝。

全文件最终 **5/5 通过**，32.45 秒，见 `work/lesson-20/runtime-visibility-observability-final.log`。该轮四个权限变化场景的实际关闭耗时为 5.155、5.196、5.097、5.131 秒，全部 ≤5.5 秒。观测出口故障为有意注入并在日志记录 3 次失败，instrumentation_errors=0。


## Agent 强杀重启与 Worker 在途正常退出

`test_run_scheduler_agent_restart.py` 最终 **1/1 通过**，46.31 秒，记录为 `work/lesson-20/agent-restart-test.log` 和 `agent-restart-acceptance.json`。Agent 在 Checkpoint 1 已提交、下一边界 advance 命令为 running 时被 SIGKILL，重启使用原数据库，两个原 Worker PID 保留。持有该测试库 owner 锁期间，claim 与 advance 均返回 503、无新物理派发；释放后旧 epoch 撤销，原命令收口为 interrupted/agent_restarted，新 epoch 从原 Checkpoint 接续。kill 至新领取 3.548236 秒，owner 解锁至新领取 1.375225 秒；原 Attempt/hash 保留，sum/double 各一次，最终回答 380。显式 paused/parked 的另一 Run 及其完整 Message 保持一致。

同一环境额外测试真实在途 SIGTERM：信号在 sum 开始后 0.008 秒、完成前 0.993 秒发出，Worker 在 1.371713 秒内以退出码 0 结束，release 恰一次且 epoch 清空，退出后没有新 claim。另一 Worker 从 Checkpoint 1 接续并完成 380，sum/double 各一次。这两个独立样本单列，原三次 Worker SIGKILL 指标分母保持不变。测试自建进程已正常清理。


## 容器与最终验收结论

隔离项目 `compass-l20-acceptance` 顺序完成 memory、worker、worker-2、agent、migrate、web、web-migrate、home 八个镜像目标，Compose 启动退出码 0。Web/Home/Agent/Memory 分别使用 8170/8171/8172/8173，PostgreSQL/Redis 使用 55440/56391；原开发服务与第 19 课容器保持。记录为 `container-sequential.log`、`container-build-*.log`、`container-up.log`。首次并行构建因本机 Python 启动超时失败，顺序构建全部通过，未放宽应用检查。

使用实际生产镜像完成 Web 登录门禁、Web→BFF→Agent→三天 PostgreSQL 样本、Memory readiness、Home 页面及派生素材 smoke，退出码 0；记录 `container-smoke.log`。另用同一 Agent/Worker 镜像、独立测试库和受控两步计算入口运行两个容器 Worker：worker-1、worker-2 各领取一条不同租户 Run，两条均生成完成回答 380；Worker 环境均无数据库配置，正常停止退出码均为 0。该消费实验使用生产队列、租约、Journal、Redis 与 HTTP Worker，计算和授权由测试入口控制。完整结果 `container-worker-acceptance.json`，操作记录 `container-worker-test.log`。测试库重复迁移返回 already_applied，重复 seed 保持三天样本。

最终 `pnpm check` **19/19**、`pnpm build` **7/7** 通过，分别见 `check-final.log` 与 `build-current.log`。应用单测共 **573 项**：Agent 345、Web 145、Worker 27、Home 21、Memory 1、data-seed 34；工具入口测试另为 48 通过、13 条条件跳过，跳过未计入通过。当前 Agent 集成入口收集 **130 项**，本轮分组全部执行通过；Web PostgreSQL **22 项**通过。浏览器 **12 项**通过（可控协议 9、真实队列 2、真实 Workflow 长链路 1）。受控供应商、权限 callback 与本地计算的使用范围已在各节单列。

52 份历史迁移摘要保持，SQL 生成与恢复一致性通过；74 个实现文档本地链接和测试文档 47 个本地链接有效，格式与差异检查通过。CI 已更新双 Worker 和本课测试入口，本次结果均来自本机执行。BRIEF Phase 与 ROADMAP 保持原值。测试结束后停止本次隔离服务并保留数据库、卷及 `work/lesson-20/` 私有记录，未清理旧库或旧 NAS 数据。
