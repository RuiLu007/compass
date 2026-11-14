# 第 19 课业务测试计划

[返回需求简报](../BRIEF.md) · [验收记录](ACCEPTANCE.md)

Profile：`standard`。采用[项目测试模板](../../_templates/TEST_PLAN.tmpl.md)，按当前实现阶段裁剪。有效需求与 AC 唯一来源为 [PRD](../spec/PRD.md#8-验收标准)；实现合同为[主方案](../design/TECH_DESIGN.md)、[数据设计](../design/DATA_DESIGN.md)、[Redis 方案](../design/REDIS_DESIGN.md)和 [HITL 方案](../design/HITL_DESIGN.md)。本计划于 2026-11-12 建立，执行结果单独记录。

## 范围与环境

覆盖 AC-01～AC-19，包含第 15～18 课案例在持久化入口下的回归、完整历史加载、执行恢复和人工决定。单测、静态检查和常规接口测试由实施流程运行；本计划补齐真实进程、数据库、Redis、工具与浏览器的业务结果核对。

| 环境               | 执行约束                                                                                                                                                                                             |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Agent / PostgreSQL | 专用 `compass_agent_test_*` 库；`AGENT_TEST_DATABASE_URL`、`AGENT_TEST_MIGRATION_DATABASE_URL`、`AGENT_TEST_ADMIN_URL` 分别提供运行、迁移和供应身份。迁移 owner `agent/main`，起点 0008，目标 0009。 |
| Web / 浏览器       | 独立 `compass_web_test_*` 库及 `WEB_TEST_*`；用 teaching fixture 的管理员、分析员、只读用户、其他租户用户；浏览器库与会重置数据的集成测试库分开。默认测试端口 8110，可使用空闲 `WEB_TEST_PORT`。     |
| Redis              | 本课独立 Docker Redis、命名卷、AOF always、noeviction；故障验收另用隔离实例。重启保留卷；丢流只删除已登记测试 Run 的键；写满只对隔离实例操作。                                                       |
| Retail             | 第 15 课固定诊断使用 `AGENT_RETAIL_TEST_*` 的同库管理与 `a_east` 只读角色；Workflow 小样使用 `COMPASS_WORKFLOW_E2E_RETAIL_DSN`，两套期望值分别核对。                                                 |
| Sandbox / MCP      | 复用真实 OpenSandbox、NFS 和 MCP HTTP 夹具；故障操作与其他任务协调。Sandbox 专项需要 `COMPASS_SANDBOX_API_KEY`，控制面/NAS 故障项需 `COMPASS_SANDBOX_FAULT_TESTS=true`。                             |
| 模型               | 稳定功能与故障用例使用可计数的受控模型响应；其余 Web/BFF/Agent、SQL、Redis、QuickJS、MCP、Sandbox 运行真实组件。模型厂商连通性只在实际执行时另列。                                                   |
| 重启与记录         | 使用真实子进程，故障屏障到达后终止原 Agent，再启动新进程。记录进程标识、Session/Run、原 Artifact 摘要及各外部调用计数；测试夹具计数存放于原进程之外。                                                |

只记录凭据变量或私有文件路径。全部临时数据库、Redis 键、效果和资源由本次测试身份隔离。正常验收保留失败现场；清理时限于本次创建的对象，不删除既有数据卷。

## 已有测试覆盖

以下为现存测试入口；本次执行状态见验收记录。

| 业务行为                       | 现有覆盖                                                                                                                                                                                                            | 本课补验                                                                        |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| 普通 Run、Retail、取消、CSV    | [Agent 单测](../../../apps/agent/tests/test_agent_run.py)、[真实 Retail](../../../apps/agent/tests/test_agent_run_integration.py)、[Composer](../../../apps/web/tests/e2e/analysis-composer.spec.ts)                | v2 受理、服务端历史、刷新、独立任务和原文件恢复；v1 过渡合同单列。              |
| 计划与 Verifier                | [受控自主测试](../../../apps/agent/tests/test_run_autonomy.py)、Retail 集成、Composer 规划场景                                                                                                                      | Todo/Verifier/预算 Checkpoint，重启后无重复查询。                               |
| QuickJS、Skill、SubAgent、资产 | [Runner](../../../apps/agent/tests/test_workflow_runner.py)、[Workflow 集成](../../../apps/agent/tests/test_hybrid_workflow_integration.py)、[Workflow 浏览器](../../../apps/web/tests/e2e/hybrid-workflow.spec.ts) | 源版本与交付日志恢复、并行子结果复用、保存邀请重启与过期。                      |
| 工具/MCP/Sandbox               | [工具 Run](../../../apps/agent/tests/test_tool_run.py)、[真实容器集成](../../../apps/agent/tests/test_tool_runtime_integration.py)、[工具浏览器](../../../apps/web/tests/e2e/tool-runtime.spec.ts)                  | 持久目录、结果、资源回执、原产物及 HITL 的后续执行。                            |
| 数据迁移与恢复                 | [数据库工具集成](../../../apps/agent/tests/test_database_tools_integration.py)                                                                                                                                      | 0008 旧数据、0009 关系约束与权限、完整/增量 SQL、独立库恢复后的业务读取。       |
| 模型与路由                     | [模型测试](../../../apps/agent/tests/test_models.py)、[路由测试](../../../apps/agent/tests/test_intent_routing.py)                                                                                                  | Run/Step/Attempt 关联、物理派发事前记录、部分输出和未知占额。                   |
| 执行过程与响应协议             | [执行树浏览器](../../../apps/web/tests/e2e/execution-process.spec.ts)、[流测试](../../../apps/web/tests/analysis-run-stream.test.ts)                                                                                | Message JSON 与 Run SSE 同 Schema/reducer；完整业务视图差分、分页、乱序和权限。 |

根入口包括 `pnpm check`、`pnpm build`、`pnpm contracts:check`、`pnpm test:integration -F agent`、`pnpm test:integration -F web`、`pnpm test:e2e -F web`、`pnpm db:sql:check -F agent`。新增测试文件接入实际 package 脚本后再登记具体命令。现有 v1 断线取消、客户端回传 history、新建对话清空内存等断言需按对应入口更新；兼容入口继续验原合同。

## 前序案例矩阵

每个案例均执行“提交 → 等待目标状态 → 保存实时业务视图 → 刷新 → 从左侧打开同一 Session → 比较视图与调用计数”。随后实际重启 Web/Agent，再打开并读取原文件。终态案例还需在测试 Run 短期流清理后重复 Message 加载。结果按下列案例编号分别登记，保留使用的夹具模式和 Session/Run ID。

| 案例   | 输入或入口                                                | 重点结果                                                                                               | 关联 AC            |
| ------ | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ------------------ |
| L15-01 | 第 15 课两个时段的渠道→商品诊断                           | 两轮真实查询、原工具 ID、回答、CSV 文件名与字节；数值对照当前测试发布库。                              | 08、09、10、12、16 |
| L15-02 | 临时查表、字段及带 JOIN/ORDER/LIMIT 的只读 SQL            | 查询卡入参/结果、截断提示、原授权范围；历史加载不执行 SQL。                                            | 10、12、16         |
| L15-03 | 数据库查询执行中取消；另运行缺配置、SQL 失败              | 保留问题、已提交过程和失败/取消原因；数据库端清理后可再查询。                                          | 05、09、10、16     |
| L16-01 | 「先列计划 · 渠道与商品诊断」                             | Todo ID/版本、依赖顺序、验证结论、CSV；完成计划与最终结果恢复一致。                                    | 10、14             |
| L16-02 | 缺失时段、Verifier 拒绝或修订未完成计划                   | 已验证渠道结果复用；修订只影响允许项；保存原观察及验证失败原因。                                       | 05、10、14         |
| L16-03 | 一次工具额度、主模型步数/Token/成本上限                   | 停止原因、部分已验证结果、消耗与未知占额持久保留。                                                     | 10、13、14         |
| L17-01 | 「渠道异动诊断」动态 JS；另注入首版脚本失败               | 源码、修复尝试、串行首调用和并行两个子任务、独立复核、保存邀请。Workflow 小样结果按夹具真实 SQL 核对。 | 01、10、15         |
| L17-02 | 确认保存、改名/启停、换时段复用、全新会话普通提问自动调用 | 保存一次；两 Run 同脚本复用同 assetId；已保存卡片刷新一致；复用不再生成脚本。                          | 10、12、15         |
| L17-03 | 「广告预算提案」直接入口及普通分析中调用同一 Workflow     | 同一动作门禁、持久等待；批准/拒绝/到期完整闭环。                                                       | 03、04、06、10、15 |
| L17-04 | 「每周经营复盘」及某周缺数据                              | Skill 正文/版本、下层 Workflow/子调用、完整周报或明确缺口；保留各层关系。                              | 10、15             |
| L18-01 | 并行查询两个时段商品实付金额，再用 Python 出图            | 原查询、并行组、Python 输入/结果、PNG 原字节及来源；清理原 Sandbox 后仍可下载。                        | 03、08、10、16     |
| L18-02 | 本人 MCP `add(2,3)`；另配置变更或禁用                     | 真实 MCP HTTP、冻结别名和 Schema/策略、结果 5；变更拒绝后继派发。                                      | 10、16             |
| L18-03 | MCP 审批写请求、慢请求取消、远端结果未知                  | 未批准零派发；批准后按当前权限执行；未知副作用保留待核对与资源回执。                                   | 04、05、06、10、16 |
| L18-04 | Python 超时/错误/非法文件及 NFS 取件失败                  | 失败原因持续可读；仅有效持久产物有可用引用；未知清理保持可见。                                         | 05、08、10、16     |

静态执行树演示仅用于组件布局与交互，单独记录。它不计入上述真实业务案例。

## 业务验收用例

### TC-01：首问受理、失败和重试形成唯一会话

- 关联需求：AC-09、AC-17。
- 前提与数据：新用户工作区、空白分析页；受控模型有成功、延迟和失败三种响应。
- 操作：提交一次问题，分别在 202 回执后、成功后和失败后刷新；再次从左侧打开；并发重发同 requestId；另以同 requestId 提交不同问题。
- 预期：空白页无空会话；受理后有唯一 Session/Run 和稳定地址；全部状态保留原问题；相同输入返回原回执，冲突输入 409；失败事务无空 Session/成功回执。
- 验证：真实 BFF/Agent/SQL，核对列表和记录计数；保存回执、页面状态和错误码。

### TC-02：全案例实时与持久视图一致

- 关联需求：AC-08、AC-10、AC-14～AC-16。
- 前提与数据：逐个运行前序矩阵，使用相同版本的公开协议 validator/reducer。
- 操作：记录每次 SSE 帧经 reducer 后的完整终态视图；加载 Message；重启后再加载；仅清理已完整投影的测试终态流，再加载一次。
- 预期：回答、消息顺序、对象 ID/revision、执行树、计划、旁白、脚本、审批、产物和终态逐字段一致；调用计数不增加；原文件名/类型/摘要/字节一致。
- 验证：协议差分比较与浏览器可见结果共同核对。比较排除网络状态、动画、滚动位置和本地展开偏好；预期业务值取独立 SQL 或固定夹具，不调用被测 reducer 生成预期答案。

### TC-03：执行中断线、刷新及切换会话

- 关联需求：AC-11、AC-17、AC-18。
- 前提与数据：延迟模型、真实长工具和并行 Workflow 三种执行；Message 读取期间仍持续输出。
- 操作：中断 SSE、刷新、切换其他 Session；按 Message projected_seq 接续；另直接使用最后已应用游标重连。
- 预期：原 Run 继续，无业务 Cancel；相同包不重复追加，所有 C 之后增量最终可见；加载基线与后续帧无遗漏；旧快照不覆盖更新版本；60 秒连接轮转后继续订阅。
- 验证：真实网络连接、服务器 Run/工具计数、消息内容及 Stream ID；浏览器重连提示和最终回答。

### TC-04：追问、上下文重置与读取隔离

- 关联需求：AC-12、AC-18。
- 操作：完成两轮后刷新追问；再重置上下文后提问；使用其他 Session、用户、租户和工作区尝试读取/续流/下载；同 Session 有非终态 Run 时提交下一轮。
- 预期：实际模型输入引用本 Session 已完成历史及原截取规则；失败/取消默认不进入模型输入；重置后旧内容仍可见，模型不带重置边界前历史；非终态期间拒绝下一轮；跨范围请求拒绝且不返回内容。
- 验证：受控模型实际请求捕获和真实 BFF 授权；刷新后再次核对。

### TC-05：列表、长历史分页及游标边界

- 关联需求：AC-09、AC-18。
- 操作：创建超过列表页和 Message 页容量的会话/轮次，逐页加载；加载时另页提交新一轮；发送重复、乱序、超前、其他 Run、格式错误游标和过期游标。
- 预期：列表按业务活动排序，翻页不重复或遗漏；以完整 Run 装载，固定分页上界后补载新轮次；非法游标明确拒绝；终态流过期返回 history_reload_required 后加载完整 Message；协议错误停止并提示重载。
- 验证：分页返回及持久记录对照、同 reducer 的乱序/重复输入测试、真实 SSE 握手。

### TC-06：版本控制、Pause/Resume/Cancel 竞态

- 关联需求：AC-01、AC-02、AC-05。
- 操作：两个控制请求使用同 expectedVersion；在运行边界暂停并恢复；重复控制；在模型和工具运行时取消；对终态 Resume；损坏 Checkpoint、修改 Schema/源码版本后 Resume。
- 预期：只接受合法版本；暂停保存边界并释放资源；Resume 使用原 Run 和已完成结果；Retry 新建当前步骤 Attempt；取消后无后继派发；终态不变；无效 Checkpoint 明确报错并保留历史。
- 验证：数据库状态/版本、执行次数、资源回执和页面控制状态；每次恢复均核对原 Run ID。

### TC-07：模型派发、重试/Fallback 与未知预算

- 关联需求：AC-01、AC-13、AC-14。
- 操作：运行分类→主模型、重试和 Fallback；在派发意图提交后、部分输出后分别真实结束 Agent；重启后查询与尝试恢复。
- 预期：每次真实派发前都有 model_attempt，关联 Run/Step/Agent Attempt；原逻辑 execution 可读；实际请求差异、输出、用量和未知结局可查；未知消耗保留占额，恢复预算不重置；已暴露片段保持 incomplete，未确认结果不自动重放。
- 验证：进程外请求计数和数据库账目；捕获时序屏障验证事前记录；不记录密钥或私有续接正文到公开事件/日志。

### TC-08：Workflow 并行恢复与草稿持久化

- 关联需求：AC-01、AC-05、AC-15。
- 操作：并行 Workflow 一个子调用已提交后结束进程；重新启动并 Resume。另用时间、随机分支及 Promise 竞争脚本；修改脚本或日志使调用身份不匹配。生成草稿后分别重启、保存、重复保存、等待邀请过期；两个 Run 生成同脚本再分别保存。
- 预期：冻结源版本、逻辑时间/随机种子、宿主 call_ordinal 与 delivery_no 保持；已完成外部调用不重复；不匹配返回 workflow_replay_mismatch；有效邀请一次保存、重放同 assetId；两张相同源码邀请复用同资产；过期源码可读且禁用保存。
- 验证：真实 QuickJS、SQL 与进程重启，外部调用计数、子结果摘要、资产数量和浏览器保存卡片。

### TC-09：工具目录、产物完整性与资源回执

- 关联需求：AC-03、AC-08、AC-16。
- 操作：分别通过内置/MCP/Sandbox 完成调用，保存 PNG/CSV；清理原计算容器后重启 Agent；以相同 Artifact ID 重放同内容及不同内容；注入保存失败、损坏字节和缺失依赖；更改 MCP 目录后 Resume。
- 预期：原目录、策略、调用 ID、输入/结果、分配及清理记录可查；相同产物幂等，冲突拒绝；产物损坏/缺失阻止依赖步骤；持久化失败无可用引用；目录变化和未知副作用阻止盲目重放。
- 验证：真实容器清理、HTTP 文件下载、独立 SHA-256、SQL 归属和派发计数。

### TC-10：等待状态重启后批准原提案

- 关联需求：AC-03、AC-04、AC-10。
- 前提与数据：已保存 GMV 图表及只读诊断，受控广告预算提案；当前租户管理员且有工作区 models.invoke 的审批人。
- 操作：进入等待，确认执行槽/MCP/计算资源清理；真实重启 Web/Agent；打开同 Session，下载原图表；批准原提案。
- 预期：原 Wait/Run/Action 身份、提案摘要和绝对有效期保持；原图表 ID/字节/来源一致；原 Run 接续；只读诊断和已完成子调用计数不增加；审批卡片与最终回执刷新一致。
- 验证：浏览器、SQL、资源 API 和持久计数；至少包含固定入口及嵌套 Workflow 两种门禁来源。

### TC-11：拒绝、到期、重复/错配决定与受信事件

- 关联需求：AC-02、AC-04。
- 操作：分别拒绝和等到数据库期限；并发批准/到期；同 requestId/decisionId 重放；更改 proposalDigest/revision/Run/wait；其他租户和无审批资格者批准；创建外部事件等待并发送错误/正确类型和关联键。
- 预期：拒绝/到期只关闭动作分支，保留已完成分析，completed 收口且无新模型调用；过期 409 且状态已提交；重复决定返回原回执；错配/越权不消费 Wait；受信外部事件按身份、类型、关联键消费一次。
- 验证：真实 SQL 并发、HTTP 状态/回执、零效果计数、刷新卡片及到期扫描；外部事件入口单独验证专用身份。

### TC-12：批准、撤权、取消及效果回执丢失

- 关联需求：AC-04、AC-05、AC-06。
- 操作：批准后派发前撤销执行人或审批人权限；竞争 Cancel 和批准/效果写入；在演示效果独立事务提交后、Run 回执前真实结束进程；重启并核对；另使效果查询未知。
- 预期：撤权无新增效果；取消先提交时无新增效果；已有效果按原 Action ID 补回执且次数为 1；核对已有结果可继续；未知效果进入 reconciliation 等待，禁止自动重放；原图表及分析继续可读。
- 验证：真实 Web 当前权限核验、Agent 同锁裁决、效果账本和回执独立事务、进程外故障屏障。明确记录两个事务之间的实际进程终止点。

### TC-13：Outbox 发布与投影故障

- 关联需求：AC-17、AC-19。
- 操作：分别在关键 SQL commit 后/XADD 前、关键批次部分 XADD 后、XADD 后/投影前、投影 commit 后/发送前结束进程；另注入 SQL 投影失败与 Redis 写入失败；恢复依赖后重启。
- 预期：关键操作原 Outbox 补发幂等；只有完整批次进入投影后放行关键包；未确认文本/未投影成功状态不可见；原有效 Checkpoint 保留；已返回模型/工具结果已存 SQL，恢复后不重复调用；Message 内容与 projected_seq 属同一完整前缀。
- 验证：真实 PostgreSQL/Redis、业务状态与公开投影分别读取、原 packetId/批次摘要与调用计数；用故障屏障定位真实窗口。

### TC-14：Redis 保卷重启、背压、丢流及保留期

- 关联需求：AC-10、AC-17、AC-19。
- 操作：检查 AOF always/noeviction/命名卷后保卷重启；在隔离实例写满并恢复；制造投影落后；删除隔离活跃 Run 流；使完整终态流过期；长文本持续输出。
- 预期：确认过的增量补回 SQL；写入失败停止新派发、已有结果可保存；活跃/等待流无 TTL 与裁剪；活跃流丢失明确 stream_data_lost/recovery_required，保留最近 SQL 历史且不创建同名空流；终态完整投影且 Outbox 已投递后才设置一小时 TTL；过期后完整 Message 可读；高频文本不产生等量 SQL Outbox 行。
- 验证：Docker 真重启、Redis 配置/TTL/键及 SQL 行数、HTTP 与浏览器恢复状态。容量和丢失实验仅操作隔离实例及本次 Run。

### TC-15：数据库升级、权限、备份恢复及单实例所有权

- 关联需求：AC-01、AC-07、AC-08。
- 操作：原生链与 bootstrap 分别建空库；原生与增量 SQL 分别升级含 Provider/调用/数据连接/Workflow/MCP/Sandbox 数据的 0008；重复执行、错误起点和结构漂移；用运行身份尝试 DDL/跨范围引用；备份恢复至新库；启动第二 Agent 并模拟所有权连接失效。
- 预期：0009 结构、版本、SQL 摘要一致，旧数据/密文可读；同 Run/Session 关系、活动 Run/Attempt 唯一性、终态约束有效；权限最小；恢复后的会话与原产物可读；第二所有者执行入口不就绪；失去所有权停止派发，旧 epoch 不可提交新结果。
- 验证：按[数据库规范](../../../docs/database-migrations.md#7-sdd-各阶段交付与完成条件)复用现有真库验收，加本课数据与约束断言；保留原库及备份。

### TC-16：界面状态、窄屏、键盘与读取期间撤权

- 关联需求：AC-09、AC-18，NFR-03～NFR-05。
- 操作：1440 px 与 390 px 下检查列表加载/空/失败、内容恢复/失败、执行/重连、等待、已决定、已过期、恢复受阻；全程键盘选择会话、折叠节点、读代码、操作批准/拒绝/控制；长连接中撤权。
- 预期：选中项与 URL 一致，长内容局部滚动，按钮可达且等待时禁用；历史加载无逐字动画、不抢焦点；错误保留输入和已有内容；最迟按五秒复核周期关闭撤权后的读取；审批和执行人权限分别核验。
- 验证：真实浏览器视图、键盘焦点、网络/权限响应；保存桌面和 390 px 截图及必要失败 trace。

## AC 对应与完成条件

| AC  | 主验收用例                  |
| --- | --------------------------- |
| 01  | TC-06、07、08、15           |
| 02  | TC-06、11、12               |
| 03  | TC-09、10                   |
| 04  | TC-10、11、12               |
| 05  | TC-06、08、12               |
| 06  | TC-12                       |
| 07  | TC-15                       |
| 08  | TC-02、09、15               |
| 09  | TC-01、05、16               |
| 10  | TC-02、10、14；全部前序案例 |
| 11  | TC-03                       |
| 12  | TC-04                       |
| 13  | TC-07                       |
| 14  | TC-02、07；L16-01～03       |
| 15  | TC-02、08、10；L17-01～04   |
| 16  | TC-02、09；L18-01～04       |
| 17  | TC-01、03、13、14           |
| 18  | TC-04、05、16               |
| 19  | TC-13、14                   |

执行记录逐项列通过、失败、未执行、受阻或待核验；所有必须用例与前序案例均通过、相关单测/集成/构建门禁通过后，才能给出完整验收结论。模型夹具、协议模拟和真实组件的边界分别记录。跳过项保留对应原因，重启恢复用实际进程结果验收。

## 实施协作事项

1. 本课 v2 测试需共享实际协议 Schema/reducer，并能读取规范化业务视图；每页完整 Run、稳定快照 revision 和游标都纳入差分。
2. 受控模型、MCP/工具、QuickJS 子调用和演示效果需要进程外计数。故障屏障必须位于实际事务/外呼边界，避免重启后计数被清空。
3. 现有 L17/L18 fixture 通过 `get_agent_executor` override 注入受控模型；持久执行接入后复用或同步该接缝。Web 部分旧用例拦截 v1 请求，按实际迁移范围改为 v2。
4. [设计评审 R2](../review/design_review.md#r2--p1workflow-草稿的资产唯一约束与现有保存行为冲突)要求核对两 Run 草稿复用资产；TC-08 保留此回归。模型正文/实际 Attempt 差异与私有续接归属按评审结论及最终实现复核。
5. Agent/Web 测试库、空闲端口、Redis 故障实例、服务启停权和可执行切片由主实施任务统一提供；收到后按本计划执行并更新[验收记录](ACCEPTANCE.md)。

## 本轮执行准备

主实施任务已提供私有环境文件 `work/lesson-19/test-env.json`；使用时由本地进程读取并注入子进程，避免将全部环境输出到终端。主任务已完成 Agent 专用库的 0009 迁移；业务服务接入仍在进行。下表是本轮静态核对结果，启服前再次确认端口占用。

| 用途                  | 当前设置                                                                              | 执行前处理                                                                                                                                                   |
| --------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Web / 授权复查        | `WEB_TEST_PORT=8130`、`WEB_PUBLIC_ORIGIN` 与 `COMPASS_WEB_AUTHZ_URL` 同为 8130        | 新授权接口交付后检查路径可达；Playwright 自启服务时保持 `WEB_TEST_EXTERNAL_SERVER` 未设置。                                                                  |
| Agent                 | `AGENT_API_URL` 指向 8132                                                             | 所选受控 Agent fixture 监听同端口；与本机 8102 开发服务分离。                                                                                                |
| MCP                   | 本地许可 origin 为 8129，现有浏览器用例 endpoint 也是 8129                            | 独立 MCP fixture 监听 8129；恢复测试保持同 endpoint 与记录账本。                                                                                             |
| Redis                 | 56379、DB 0                                                                           | 常规验收使用本课新实例；写满和数据丢失另用隔离实例及 URL。                                                                                                   |
| Web 数据              | `compass_web_test_l19`                                                                | 浏览器与重置型 Web 集成测试顺序执行；并发时另建测试库。                                                                                                      |
| Retail 默认 seed 输入 | 环境仍含 `compass_retail_dev_*` 默认连接                                              | 本轮 seed 子进程清空 `COMPASS_RETAIL_RUN_SCOPE` 与 `COMPASS_RETAIL_RUN_READONLY_DSN`，或显式改为匹配的专用测试连接。Workflow/Retail 专项继续用各自测试 DSN。 |
| 观测                  | `COMPASS_OBSERVABILITY_ENABLED=true`                                                  | 本课隔离验收覆盖为 `false`，避免写入既有观测项目；观测回归单列配置。                                                                                         |
| 用例开关              | `WEB_TEST_TOOL_RUNTIME=true`，缺 `WEB_TEST_WORKFLOW` 和 `COMPASS_SANDBOX_FAULT_TESTS` | 跑 L17 加 `WEB_TEST_WORKFLOW=true`；实际执行已协调的容器故障回归时加 `COMPASS_SANDBOX_FAULT_TESTS=true`。                                                    |

### 已有夹具的选择

| 场景                 | 注入入口                                                                                      | 现有运行方式与边界                                                                                                                                                                                          |
| -------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| L17 Workflow / Skill | `tests.workflow_e2e_app` override `get_agent_executor`；`workflow_fixture.WorkflowModels.run` | `PYTHONPATH=apps/agent uv run --locked uvicorn tests.workflow_e2e_app:app --host 127.0.0.1 --port 8132`。真实 Retail/QuickJS；模型由 FakeModels 返回。                                                      |
| L18 Python / MCP     | `tests.tool_e2e_app` override `get_agent_executor`；`ToolModels.run`                          | `PYTHONPATH=apps/agent uv run --locked uvicorn tests.tool_e2e_app:app --host 127.0.0.1 --port 8132`。真实工具链；模型由 FakeModels 返回。                                                                   |
| MCP HTTP             | `tests.tool_mcp_fixture`                                                                      | `PYTHONPATH=apps/agent uv run --locked uvicorn tests.tool_mcp_fixture:app --host 127.0.0.1 --port 8129`。`add`/`slow` 可直接复用；原 `write` 只验证未批准时零派发，获批后需要本课有持久效果记录的受控夹具。 |
| LLM 物理 Attempt     | `tests/e2e_app.py` override `get_model_service`，注入 `ProviderFixture.transport`             | `PYTHONPATH=apps/agent/tests uv run --locked uvicorn e2e_app:app --host 127.0.0.1 --port 8132`。该专项移除 `COMPASS_WORKFLOW_E2E_RETAIL_DSN`，避免入口自动导入 Workflow override 后绕过真实模型执行层。     |

上述 Agent fixture 分批使用 8132，不同时启动。测试通过环境注入读取配置；命令示例省略私有变量。首次运行浏览器前由主任务执行 `pnpm test:setup -F web -- --fixture teaching`，再按 fixture 分批运行 `pnpm test:e2e -F web -- hybrid-workflow.spec.ts` 或 `tool-runtime.spec.ts`。本课新 E2E 文件与实际启动入口交付后追加到本节。

### 故障注入位置

故障包装限于测试进程，在原函数执行完成的确认点设置屏障并通知父进程。父进程先从独立连接核对已提交状态，再结束 Agent；新进程使用同数据库/Redis/密钥。正常服务不暴露故障控制 HTTP 接口。

| 窗口                          | 当前可用接缝                                                                      | 必须保存的外部观察                                                    |
| ----------------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 受理已提交、尚未启动任务      | `RuntimeStore.submit` 返回后、执行任务登记前                                      | 原命令回执和唯一 Session/Run；重启后 Created 可显式接续。             |
| Outbox 已提交、尚未 XADD      | `RuntimeStore.outbox` 所属事务退出后，`OutboxPublisher.flush` 调用前              | 未投递 Outbox、原 Checkpoint、尚未公开的业务终态。                    |
| 关键批次部分发布              | `RedisStream.append` 中原 `writer.xadd` 成功之后，指定 batchIndex 返回前          | 已有 packetId/摘要/Stream ID；补发后每个稳定包仅一份。                |
| 增量已确认、投影未提交        | `RedisStream.append` 返回后、`RuntimeStore.project` 前                            | 原 Redis 条目与 SQL 旧游标；新进程补投影、无再次模型/工具调用。       |
| 投影已提交、尚未发送          | `RuntimeStore.project` 事务提交之后、SSE yield 前                                 | Message 已有完整业务前缀；终态和完整回答同步可读。                    |
| 模型发送前/发送后             | 实际 Attempt recorder 的 dispatched 提交，以及 ProviderFixture 接收请求处         | 进程外请求计数、事前 Attempt、未知占额；FakeModels 场景另标覆盖范围。 |
| 并行子结果已提交、尚未交付 JS | `execute_script` 中宿主 child 返回到发送 `result` 帧之间，结合本课 journal 提交点 | 已完成 Step/Attempt、delivery_no、调用摘要及外部计数。                |
| 效果已提交、Run 回执未存      | 本课演示适配器效果事务返回之后、Runtime 记录动作回执之前                          | 原 Action ID、效果次数 1、回执缺失；重启补记后仍为 1。                |

TC-14 的文本合并还需统计投影事务次数：长文本的 SQL 更新应按 500 ms/64 KiB 批次发生，关键边界允许强制提交。仅统计 Outbox 行数会遗漏逐 delta 更新同一消息行的退化。

## 新增入口与验收推进

2026-11-12：新增 [持久协议单测](../../../apps/agent/tests/test_durable_protocol.py)、[持久 Runtime 集成](../../../apps/agent/tests/test_durable_runtime_integration.py)和[真实进程/Redis 故障测试](../../../apps/agent/tests/test_durable_runtime_faults.py)，已接入现有根入口。Runtime 集成覆盖受理幂等、控制版本竞争、Pause/Resume/Cancel、审批效果与拒绝/到期、撤权阻断、QuickJS 回执/草稿、13 轮历史、Outbox/流故障、Checkpoint/Artifact 校验及受信事件。最新执行状态与真正剩余覆盖集中维护在[验收记录前部](ACCEPTANCE.md#当前-ac-状态与剩余验收)。

Runtime 集成在同一 Python 进程中重建 DurableRuntime，部分故障由 `RecoveryStop` 注入，授权检查使用受控替身，模型使用 FakeModels。故障文件另启动真实 Agent 子进程和独立 Redis 容器。下表保留最初补验清单用于追踪；已执行部分以验收记录为准。

| 关联 AC        | 已有新增入口可验证部分               | 开测后必须补齐                                                                                                                           |
| -------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 01、02、05     | 唯一受理、Busy、Scope 和同 Run 重建  | 同版本控制竞争、实际 Pause/Resume/Cancel、损坏与不兼容 Checkpoint、真实进程重启后的已完成调用计数。                                      |
| 03、04、06     | 预算 Wait、效果账本、拒绝/到期分支   | 原 GMV 图表与已清理 Sandbox；真实杀进程在效果提交/Run 回执之间；真实 Web 撤权、批准/取消/到期竞争、重复和错配决定、受信外部事件。        |
| 07、08         | 已有迁移测试和持久资源读写接缝       | 含实际旧业务数据的 0008 升级、运行账号权限、备份恢复；原文件字节、越权/损坏/写失败和 Artifact ID 冲突。                                  |
| 09、10、11、18 | 纯协议 reducer、分页快照与重复帧处理 | 真实 BFF/浏览器受理、失败刷新、14 个前序案例的完整视图差分、加载期间持续输出、游标过期和活跃流丢失、读取撤权、桌面/390 px/键盘。         |
| 12、14         | 13 轮人工终态记录和上下文边界        | 捕获真实执行层两轮后追问及重置后的模型输入；实际 Todo/Verifier 更新、失败和预算停止后的恢复。                                            |
| 13             | 现有模型执行层及 ProviderFixture     | 物理派发事前 Attempt、重试/Fallback、部分输出后真进程崩溃、未知用量占额和预算接续。                                                      |
| 15、16         | QuickJS 回执、草稿读取和工具既有集成 | 明确在并行分支完成/交付窗口杀进程；确定性重放错配；两 Run 相同脚本保存、过期、Skill/SubAgent；真实 MCP/Python 产物、目录变化与未知效果。 |
| 17、19         | 单测验证关键批次可见性               | 各 Outbox/XADD/投影真实故障窗口、Redis AOF 保卷重启、隔离写满与丢流、TTL 与完整历史、投影批次频率；单所有者及 fencing。                  |

执行顺序为：完成纯协议与接口接缝检查 → 隔离 PostgreSQL/Redis 集成 → 受控 Agent 服务的 v2 浏览器路径与前序案例 → 独占故障窗口和容器重启 → 修复复验与完整门禁。每个阶段保留当前失败结果，复验追加记录。

### Composer v2 浏览器夹具入口

主任务另行授权测试子任务维护 [analysis-composer.spec.ts](../../../apps/web/tests/e2e/analysis-composer.spec.ts) 与新增 [durable-analysis.fixture.ts](../../../apps/web/tests/e2e/durable-analysis.fixture.ts)。原 Composer 业务检查迁移至 Session submit、Message snapshot、Run SSE 和持久 Artifact 读取，原 14 项业务用例保留，另补取消版本冲突、保存邀请状态恢复、模型切换等待禁发、活跃流丢失后 Message 重载，共 18 项。

夹具在每个测试浏览器上下文保存 Session/Run/Message，回执先返回，执行通过原生 Stream ID 增量继续；刷新读取同一数据，追问历史在服务端夹具构造。响应使用当前生成的 AgentResponse JSON Schema 校验，并经过产品自己的 validator/reducer。真实 Web 仍负责登录、页面和交互；Agent 执行、SQL/Redis、模型与工具由本组受控响应替代，真实链路继续使用前序案例矩阵。

覆盖原输入/输入法、模型选择、居中与底部停靠、Markdown 和安全渲染、独立历史滚动、文件名与字节、追问、工具取消及清理、终态竞争、焦点、附件上限/滚动/删除、375/768/1440 px、短视口、计划浮层；新增明确的离开会话后台继续及重载恢复断言。完成结果直接恢复，客户端提交不含 history。

运行入口为 `pnpm test:e2e -F web -- analysis-composer.spec.ts`。使用已启动服务时设置 `WEB_TEST_EXTERNAL_SERVER=true`；本轮通过 `work/lesson-19/run-observed-env.py` 注入最终隔离环境。真实登录在同 worker 内复用，支持私有 `WEB_TEST_STORAGE_STATE`；每个用例仍通过真实 session API 核对权限，避免重复登录触发限流。

## 专项实际运行入口

- AC-03 使用最终 Web 8134/Agent 8135、ToolModels fixture 和真实 Retail/Python/MCP。等待时保留原文件与 Message，主任务 SIGKILL 后由独立测试刷新批准；只读核对入口为 `python work/lesson-19/run-final-env.py uv run --locked python work/lesson-19/ac03-inspect.py --run-id <本次 Run ID> --output <记录路径>`。该脚本仅 SELECT 和查询 OpenSandbox 资源清单。
- AC-12/18 准备入口为 `python work/lesson-19/run-final-env.py node work/lesson-19/ac12-18-web.mjs`，使用真实 ProviderFixture 后端。通过 Web CRUD 新建专用测试 Provider；以同 Session 两轮完成后刷新追问、上下文重置、另一个 Session 和另一个用户的输入核对实际模型请求。只读脚本 `ac12-18-inspect.py` 在内存解密测试输入，日志仅输出匹配结果、角色数量和标识。
- AC-18 通过管理员 HTTP 临时为测试分析员授予/移除 models.invoke，在慢响应期间检查现有 SSE、Message、新 SSE 和后续派发；脚本 finally 恢复原权限并停用自己创建的 MCP。与会修改 teaching 权限的 foundation/models E2E 顺序执行，不并行运行。

CSV 受控下载先断言原生 download 属性和 Artifact URL，再按已确认的夹具规则移除测试 DOM 中的 download 属性，由 Content-Disposition 发起可拦截下载，核对文件名及完整字节。原因是本机 Chromium 的原生 download 请求绕过 Playwright routing；原生链接的真实 Agent 的原生 PNG/CSV 下载已在 AC-03 完成。

### 实际业务帧的 Web 回放验证

[durable-history.spec.ts](../../../apps/web/tests/e2e/durable-history.spec.ts) 读取真实 Agent 业务矩阵导出的 SSE 帧和 Message snapshot，使用生产 `agentResponse` 与 `reduceAgentResponse` 校验。逐例比较完整 Session/Run、消息和全部业务对象；每帧重复应用、快照重复加载及旧帧再交付均保持对象和内容稳定。仅排除传输游标、分页指针等非业务字段。

输入由 [Agent 持久矩阵](../../../apps/agent/tests/test_durable_case_matrix.py) 导出 `work/lesson-19/durable-matrix.json`。覆盖 SQL 失败/取消、Python 异常、NAS 文件缺失、固定诊断、临时 SQL、计划/Verifier/预算、缺时段、已保存 Workflow 复用、动态脚本/修复、Skill 周报及预算批准/拒绝，共 16 类。Agent 侧另核对 Runtime 重建、终态删除短期流、原文件和调用计数；Web 侧消费真实数据执行契约差分。

运行时必须显式设置 `DURABLE_MATRIX_FILE`；未提供输入时显示跳过，报告不计通过。示例：`python work/lesson-19/run-final-env.py env WEB_TEST_EXTERNAL_SERVER=true DURABLE_MATRIX_FILE=<实际导出文件绝对路径> pnpm test:e2e -F web -- durable-history.spec.ts`。AC-03 的真实 HTTP 帧另导出为 `durable-ac03-matrix.json`，复用同一测试。

### 模型与观测 v2 回归

[models.spec.ts](../../../apps/web/tests/e2e/models.spec.ts) 保留模型管理、能力测试、响应式/键盘与权限断言；Chat 通过 `sessions/submit` 受理后记录同 Session/Run，实际 Message 经过生产 reducer 验证三轮问答，客户端请求不含 history。切换应用前后保留文档和 DOM 身份，覆盖执行中切换与后台完成。固定模型禁用保留草稿；异步模型切换等待完成后再验证禁用；早期取消和撤权清除内容独立断言。

[observability.spec.ts](../../../apps/web/tests/e2e/observability.spec.ts) 从 v2 Message 的真实 timeline 取得 modelCallId，核对 trace 头、实际模型调用、Langfuse 聚合与权限。使用 `run-observed-env.py` 注入本课 test/browser-990 观测环境。models、foundation 和 AC-18 会修改同教学分析员权限，顺序运行；Composer 与只读矩阵使用独立协议夹具，可并行但使用不同 Playwright 输出目录。

### 边界与最终门禁

新增 [持久边界集成](../../../apps/agent/tests/test_durable_boundaries.py) 覆盖批准与取消/到期竞争、受信事件 HTTP 独立身份、两 Run 同源草稿去重与过期、目录变更恢复阻断，以及实际模型 Retry/Fallback 的 Run/Step/Checkpoint 关联。7 项已通过，结果见验收记录。并行子结果提交至 JS 交付的窗口另用真实 Agent 子进程退出验证，已通过；外部子模型 6 次/Retail 3 次审计列表重启前后逐条一致，原 Attempt、host 顺序、源码和 clock 保持，同 Run 恢复及同 asset 保存均通过。

最终执行已完成：Agent 91 项集成与通用 57 项 E2E 均经全组执行及定向复验完成通过覆盖，真实 Tool 2 项、Workflow 1 项另列。2026-11-12 最新 check 19/19（Agent 340、Web 119）、源码 build 7/7、Web PostgreSQL 22 项和最新五应用容器 smoke 通过；实际命令、修复复验、410 真实链路与工具层跳过项见[验收记录](ACCEPTANCE.md)。
