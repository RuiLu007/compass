# 第 20 课故障矩阵

[返回需求简报](../BRIEF.md) · [业务测试计划](TEST_PLAN.md) · [有效 PRD](../spec/PRD.md)

2026-11-12，故障验收执行版。以下触发点和不变量依据已评审 [TECH_DESIGN](../design/TECH_DESIGN.md) 与 [CODE_DESIGN](../design/CODE_DESIGN.md)。FM-00～FM-23 均已完成对应层次的当前验证；真实 Worker 强杀、Agent 强杀、在途 SIGTERM、未确认效果、权限变化与观测失败分别登记。实际入口、测试夹具范围、首轮问题和最终复验见 [执行记录](ACCEPTANCE.md)。

## 共同验收不变量

- 一个已受理 Run 只保留一个 job；只有当前有效 workerId/bootId/epoch、未过期 Lease 与合法状态可推进。
- 重复 submit/claim/advance 和网络丢响应保持原回执语义；已经提交的分析结果和可核对 Action 效果复用。
- 取消受理后新业务动作数为 0；旧 epoch 的新 Checkpoint、成功结果及公开增量提交数为 0。
- leased/reclaiming 都占执行槽，实际资源/远端效果确认后才释放；未知效果/成本保留恢复说明与保守占额。
- 公开 Message 与 SSE 使用同一权限过滤、完整业务投影与 AgentResponse v2；原 Run 的历史可读且重复加载不派发。

## 正常基线与故障样本

| ID    | 类别与关联                         | 触发点 / 实际操作计划                                                                | 必须核对的结果                                                                 | 记录要求                                             | 当前状态 / 实际结果                              |
| ----- | ---------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ | ---------------------------------------------------- | ------------------------------------------------ |
| FM-00 | 正常基线；TC-01/04/12/17           | 两租户各 4 个 Session、双真实 Worker；含普通分析和审批任务                           | 配额、成功答案、等待释放、再入队；排队与利用率基线                             | 两 Worker PID/boot、全部 Run、usage、Timeline、回答  | 通过：双租户 8 轮；审批真实 UI 通过              |
| FM-01 | 持久接收中断；TC-02                | Session/Run/job/命令/Outbox 事务提交后、首次领取前终止 Agent；另在事务提交前注入失败 | 提交后原 Run 可补交付；提交前完整回滚；重复 requestId 返回原结果或正常首次受理 | 提交屏障、重启 PID、记录数和外部调用数               | 通过：M6 原子受理/启动复核，M5 持久交付故障      |
| FM-02 | 重复投递；TC-05                    | 并发 claim 同一就绪 Run、同一槽；丢弃已提交 claim 回执并重试                         | 同 boot 原 grant/epoch/claim_count；唯一有效领取和槽位占用                     | 原/重试 grant、claim_count、调用审计                 | 集成通过：并发领取、同 boot 原回执               |
| FM-03 | 进程身份更换；TC-05                | 原 workerId 的新 boot 尝试 claim/heartbeat/advance；互换凭据或伪造正文身份           | 原占用先清理；boot/凭据/Run 归属不匹配拒绝，用户/Worker 凭据分离               | 身份摘要、状态码、安全载荷、job                      | 通过：HTTP 凭据、boot 和槽位约束                 |
| FM-04 | advance 受理丢响应；TC-06          | running 命令提交后 HTTP 断开；持续重发相同边界，途中正常续租                         | 一个内部命令/执行段；expiresAt 不改变身份；HTTP 断开保留任务                   | commandId、expectedCheckpoint、Attempt/实际调用数    | 通过：M6 HTTP 同边界轮询，M5 实际断连            |
| FM-05 | advance 提交丢响应；TC-06          | 结果/Checkpoint/回执已提交后丢弃响应；释放 grant 后再读旧命令并尝试新边界            | 原提交回执可读；已提交结果复用；旧 grant 无新推进权                            | 原回执、Checkpoint、Outbox、后继拒绝                 | 集成通过：旧提交回执与后继推进拒绝               |
| FM-06 | Lease 超时；TC-07                  | heartbeat、派发、提交等锁跨越 TTL，锁释放后继续；另发过期 heartbeat                  | 锁后真实数据库时刻裁决，lease_lost；无复活、无迟到新写入                       | 锁/时刻/expiry、接口回执、版本/事件差分              | 通过：真实 PG 等锁跨 TTL，三个入口               |
| FM-07 | Worker Crash；TC-08                | 真实 Worker 1 在已提交步骤后被终止；Worker 2 继续消费                                | 同 Run 新 epoch，从 Checkpoint 接续；已完成调用不重复，受控接管 ≤21 秒         | 退出 PID、最后心跳、清理及新 claim 时刻、效果/调用数 | 通过：3 次 SIGKILL，最大 18.605460 秒            |
| FM-08 | Crash 与动作效果窗口；TC-08/10     | 真实效果已提交、原执行回执尚未完成时停止 Worker/执行驱动                             | 稳定 Action ID 核对并复用效果；实际效果总数一次                                | 外部账本、Action/Attempt、两 grant、终态             | 通过：M6 效果核对、未知阻断，M5 实际效果后退出   |
| FM-09 | 旧结果/delta 迟到；TC-07           | 保持旧回调到新 epoch 建立后放行                                                      | 新 Checkpoint/消息/增量保持；旧已提交 Outbox 可补发，有限结局核对单列          | 回调 epoch、完整 SSE/Message、Checkpoint、费用       | 通过：旧 epoch 边界/PG 迟到 progress 拒绝        |
| FM-10 | 执行超时；TC-09                    | 无副作用模型/工具夹具超过配置期限，覆盖允许重试与预算不足                            | 有明确超时原因；Retry 按策略产生新 Attempt；预算与占用连续，终态受控           | 配置期限、每 Attempt、StopPolicy、usage              | 通过：真实模型 1 秒超时及工具预算耗尽            |
| FM-11 | 未知远端效果；TC-09                | MCP 写已派发但远端响应丢失，随后 Lease 到期                                          | 不自动重放 tools/call；恢复受阻、原 Action 与未知成本占额保留                  | 进程外效果/调用数、recovery reason、预算             | 通过：独立 HTTP 效果一次，保守阻断               |
| FM-12 | 清理未确认；TC-09/10               | Sandbox/工具清理无法确认并触发到期或取消                                             | reclaiming 保留槽位；新 claim 不越过实际容量；可用其他槽正常服务               | 清理回执、资源状态、usage、另一租户 Run              | 通过：未确认清理集成与真实未知效果占额           |
| FM-13 | 取消与动作竞态；TC-10              | 取消先/动作发起先、效果已提交后取消分别执行                                          | 受理取消后零新动作；先前合法效果保留；唯一终态与确认释放                       | 取消和动作时间、Action 账本、Run/Timeline            | 通过：M6 零后继派发，M5 效果/取消竞争            |
| FM-14 | 取消与接管竞态；TC-10              | 取消先/claim 先两序；迟到旧 grant 与重复投递                                         | 取消收口优先，终态不复活，旧 epoch 零新推进                                    | 两顺序屏障、grant、状态/占用差分                     | 通过：M6 领取前后取消先后，旧 grant 拒绝         |
| FM-15 | 批准与取消/重复竞态；TC-03/04/10   | 有效批准和取消交错、重复批准、满额时批准旧 M5 无 job Run                             | 一次有效决定；取消后无新效果；满额原 Wait/请求键可重试，全部接续排队           | Wait/Action/命令、429/回执、job、外部效果            | 通过：M6 入队/背压，M5 批准与取消/到期并发       |
| FM-16 | Agent Crash 与启动复核；TC-11      | 存在 leased/reclaiming/running 命令时真实重启 Agent                                  | 恢复底座前关 claim/advance；旧 epoch 撤销；安全任务再入队，显式暂停/等待保留   | 所有者/流/投影就绪、旧命令结局、各类 Run             | 通过：M6 Agent 强杀重启、原 Checkpoint/暂停记录保留          |
| FM-17 | Worker SIGTERM；TC-11              | 运行中正常终止 Worker，覆盖短步骤与清理延迟                                          | 停止新 claim；有界收口、允许的心跳与 release 意图；退出后清理占用正确          | 进程退出码、请求日志、释放区间/资源计数              | 通过：真实在途 SIGTERM 1.371713 秒正常退出、单次 release               |
| FM-18 | 容量与公平；TC-12                  | A 满执行/积压并持续超额、A 候选 Session 持锁，B 空闲容量；另触发全局上限             | 429 无新 Run；A≤1/全局≤2 在途；B 不被争用候选阻塞，无故障首领 ≤3 秒            | 各租户队列/用量、p50/p95/max、拒绝计数               | 通过：双租户、配额、跳锁与 B 2.111228 秒         |
| FM-19 | SSE 断线与流失效；TC-13            | 真断线续游标、终态流过期、活跃流丢失、Redis 暂不可用                                 | 按序无重复；409 重载、410 恢复受阻、503 安全反馈；SQL 历史可读，无新增执行     | 实际状态码/游标、完整帧与快照、浏览器请求次数        | 通过：实际断连、AOF/流丢失/Redis 故障与重放      |
| FM-20 | 撤权/展示范围变化；TC-14/15        | 注入受限标记，实时与历史读取；流中撤权并重连                                         | 载荷/DOM 无受限字段；安全占位保留 seq/revision；当前权限阻断/清除旧详情        | 脱敏扫描结果、帧校验、旧连接关闭时刻、角色矩阵       | 通过：4 项真库流复查、权限矩阵及 UI 过滤         |
| FM-21 | 回答前后断线、失败和取消；TC-17/18 | 旁白含“最终回答”；成功批次前后断线、重复帧、手动展开；部分答案失败/取消              | 仅结构化成功切换、自动折叠一次、用户偏好保持；未提交答案为 incomplete          | 真实 UI/焦点、Frame/Message、下一轮模型历史          | 通过：9 UI、2 真实完成、M5 竞争与历史            |
| FM-22 | 观测出口不可用；TC-16              | 实际模型/Workflow Run 执行中关闭测试观测出口，重复历史查询                           | 持久 Timeline/状态/用量可读；Retry/Resume/Replay 关联明确，历史无新调用        | modelCallId/Trace、Timeline、调用数、安全字段        | 通过：运行在途出口失败，完成后历史/Timeline 可读 |
| FM-23 | 迁移与恢复；TC-20                  | 空库、0009 有数据旧库原生/SQL 升级，独立库+隔离 Redis 备份恢复                       | 单一 head、原数据/Action/文件保留；旧无 job 不自动派发，恢复一致且原库/卷保留  | schema/角色/SQL manifest、原记录及恢复读取           | 通过：数据库 22 项及 M6 native/SQL 升级          |

## 实际结果追加格式

每次故障执行追加：日期与源码范围、隔离环境、实际入口及退出码、故障触发屏障、原/新进程与 Worker 身份、Session/Run/Action、实际不变量观察、量化值、原始记录路径和当前结果。敏感凭据只登记引用。

恢复指标按[测试计划度量口径](TEST_PLAN.md#度量与结果记录口径)计算。可恢复样本、未知效果受阻样本、清理占用样本分组展示，恢复总分母保留所有需接管 Run。一次故障的重复执行不重复增加样本数；明确使用不同 Run 的独立实验可单独计数。

四类课堂必测为 Worker Crash（FM-07/08）、重复投递（FM-02/04/05）、超时（FM-06/10/11/12）、取消竞态（FM-13/14/15）。必要场景未执行、失败或受阻时保留当前状态，报告只对实际完成的范围给结论。

## 独立 Worker / UI 已验证子项

本节记录已执行范围；完整故障触发和业务效果仍由上表对应实验核对。

| 对应 FM              | 本轮已通过范围                                                                                                 | 执行结果及仍待核对范围                                                  |
| -------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| FM-00/15             | 两真实 Worker 已运行；单租户周报完成、预算 waiting 释放执行槽，批准后同 Run 二次入队完成                       | 真实浏览器 2/2；未在此轮模拟批准/取消竞争，双租户公平另由进程任务交接   |
| FM-02/03/04/05/06/17 | MockTransport 验证 claim 身份、同边界重试、expiresAt 不改变边界、迟到心跳不复活、信号边界 release              | Worker 27/27；包括自建进程 SIGINT/SIGTERM，未据此标记真实服务端故障完成 |
| FM-19                | queued 断线重连 UI；两个真实完成 Run 从 0-0 重放与 Message 一致，重复帧幂等，刷新无新命令                      | 通过：实际断连、AOF/流丢失/Redis 故障与重放                             |
| FM-20                | 安全快照替换旧详情；BFF 当前权限矩阵；真实租户 usage 与平台 Timeline 权限分离；公开 Message 无 Worker 私有字段 | 通过：4 项真库流复查、权限矩阵及 UI 过滤                                |
| FM-21                | 可控成功/失败/取消、旁白、重复帧、自动折叠/用户展开偏好/键盘焦点；真实周报与预算完成后折叠和历史刷新           | 通过：9 UI、2 真实完成、M5 竞争与历史                                   |

实际入口、首轮问题及复验详见 [执行记录](ACCEPTANCE.md)。真实 Run 为 `9ab380fd-c9de-437f-9477-184693b6462c`（周报）和 `21dfd5cf-ea68-44d9-b5c8-3384f9b818a4`（预算）。原始业务输出为 `work/lesson-20/runtime-live-current-business.json`；Worker/单测/UI 日志均保存在 `work/lesson-20/`。

## 独立进程故障执行结果

独立任务已交接 `work/lesson-20/process-test.log` 与 `process-acceptance.json`：真实 Agent/PG/Redis、两个真实 Worker、独立 HTTP 效果服务，1 项复合实验通过，137.27 秒。配置为 heartbeat 3 秒、TTL 15 秒、租户容量 1、全局容量 2。

- FM-00/18：两租户各 4 个独立 Session 全部完成，A 第 5 次提交返回 429 且无额外 Run。B 首领 2.111228 秒；1523 次采样峰值为全局 2、每租户 1。A 排队 n=4、p50=6.055861 秒、p95/max=16.166829 秒；B n=4、p50=6.048310 秒、p95/max=16.183484 秒。
- FM-07/09：三个真实 SIGKILL 后均由不同 Worker/新 epoch 在 Checkpoint 1 接续，原已提交 Attempt 保留；最后心跳至新领取分别 18.605460、17.564396、15.717994 秒。物理 sum/double 各一次，最终 Message 380；旧 heartbeat/advance 为 409，迟到推进为 0。
- FM-08/11/12：未知样本的外部 HTTP 效果总数 1；无回执 Attempt 保留 dispatched 与空 output，Run blocked、job reclaiming、epoch 已撤销。A 的后继仍 ready/未领取，B 使用另一槽完成，A 的 usage occupied=1。
- 恢复样本总数 4，恢复 3、未知受阻 1；安全子集 3/3，总体恢复率 3/4。接管 n=3、p50=17.564396 秒、p95/max=18.605460 秒，满足 21 秒目标。全部样本均保留在分母。
- 48 个调度事件，观察窗口 126.483661 秒，容量分母 252.967322 slot·s；占用 130.553087 slot·s，利用率 51.6087%，其中 reclaiming 9.606331 slot·s。该进程夹具未调用模型，模型 token/费用为 0；预算与超时另由后续测试交接。

Run/PID、旧新 grant、原 Attempt、独立效果审计和逐次采样保存在 JSON；概要及三个 Run 逐项数据见 [执行记录](ACCEPTANCE.md#独立进程故障与量化验收)。

## 流中当前权限实验

`test_runtime_visibility_integration.py` 使用真实 PG/Redis 和生产 SSE/Message 入口，当前授权 callback 分别切换 forbidden、authorization_unavailable、仅 revision 变化、details 收紧。四项 4/4 通过，原连接分别在 5.081、5.075、5.087、5.093 秒关闭；后续请求执行最新权限，安全重投影保持原 packet/seq/object/revision/cursor，原持久内容与 Run.version 不变。记录为 `work/lesson-20/runtime-visibility-integration-retest.log`，关闭计时从授权回执变化开始。

## 超时、预算与观测故障补充

- FM-10/11：`test_run_scheduler_limits.py` 2/2 通过，10.69 秒。工具预算跨 4 个 Checkpoint 后以 budget_exhausted/max_tool_calls 收口，实际 SQL 1 次、模型夹具 2 次，partial 保留；模型服务实际 3 秒上游对 1 秒期限触发 model_timeout，仅 1 次物理请求，保守占额 192 tokens/768 microusd。两例 job done、occupied=0，后继任务均完成。记录为 `limits-test.log` / `limits-acceptance.json`。
- FM-22：同一实际 M6 Run 的物理计算在途时，Observer 出口被设为失败；失败期间原 Run 完成，Trace ID、Message、SSE 与平台 Timeline 可读。实际出口失败 3 次，物理计算 1 次，Timeline 8 条；历史/Replay 不增 command/Attempt/物理调用，无权主体拒绝。与四项权限变化组合运行最终 5/5 通过，32.45 秒，见 `runtime-visibility-observability-final.log`。
- 本轮 M5 durable core 18 项及 faults/cases/boundaries 13 项通过；补齐真实 Redis AOF/OOM/owner loss、实际进程退出、效果窗口、批准/取消/到期并发、ModelService Retry/Trace 和 QuickJS host 退出。M5 夹具保持原执行路径；M6 调度层通过新 30 项集成、readiness gate、limits 和真实 Worker 进程验证。对应日志和分层映射见 [执行记录](ACCEPTANCE.md#当前-ac-归并)。
