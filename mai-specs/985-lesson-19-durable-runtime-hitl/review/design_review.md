# 第 19 课数据结构 MVP 评审

日期：2026-11-12。Feature：985。Profile：standard。采用 `my-review-design` 评审结构，产物路径沿用 SDD 默认约定。

评审范围：[PRD](../spec/PRD.md)、[数据设计](../design/DATA_DESIGN.md)、[主方案](../design/TECH_DESIGN.md)、[代码设计](../design/CODE_DESIGN.md)、[HITL 增量](../design/HITL_DESIGN.md)，以及当前工作区相关实现。包括 15 张拟新增表和既有模型、Workflow、MCP、Sandbox 表的复用关系。本报告保留评审建议；2026-11-12 的双接口澄清已同步到 PRD/设计。接口、游标和短期日志相关结论已按本次本地 Redis 决定更新，其余未落实项仍为评审建议，实现尚待开展。

## 1. 总体结论

**建议修改后实施。存在过度设计，主要集中在流式增量包长期全量保存、重复正文、重复标识与游标、工具目录拆表和旧模型尝试回填。核心运行实体大部分有当前用途。**

本课的最小闭环包含完整历史展示、服务端多轮上下文、执行边界恢复和持久人工决定。Message、Checkpoint、Step、Attempt 和必要业务历史各自承担不同职责。流式增量可以采用短期日志与持久投影，完成后的界面由投影重建；本报告第 8 节记录已选定的 Redis 部署及其故障边界。

优先处理两项 P1：明确各类正文的唯一存储位置；修正 Workflow 保存邀请与资产的一对一约束。Session 类型、图标与运行状态的新增用户要求也需补入下一版契约。

推荐的表级调整是将 `agent_tool_binding` 收入 Run 的不可变配置快照。这样新增表由 15 张收敛为 **13 张正式运行表和 1 张课堂效果账本表**。其余收益主要来自字段、约束和写入职责的精简。表数量是调整结果，判断依据是实际维护成本与验收行为。

## 2. 关键发现

### R1 · P1：模型请求、结果和续接数据的保存职责重叠

数据设计同时安排了 Message 正文、`agent_attempt.input_content/output_content`、`model_call.request_content/output_content`、`model_attempt.request_content/output_content`，Checkpoint 又保存私有续接数据。主方案说明了逻辑请求和物理尝试的区别，但尚未明确相同正文何时引用、何时复制。

一轮模型调用成功时，同一份规范输入和接受结果可能进入多处密文列；后续增加内容格式或清除续接状态时，需要同步修改多个写入、读取与清理分支。主方案已经明确公开 Message/public_view 与投影游标同事务更新，这种有明确消费者的派生关系可以保留。

**建议在实施前明确以下分工：**

| 内容                          | 保存方式                                                                                                            |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| 会话正文与规范上下文消息      | `agent_message` 保存正文；实际模型输入按顺序引用已固定的消息                                                        |
| 模型逻辑请求                  | `model_call` 保存消息引用、参数与必要请求快照；独立模型测试也由它承载输入                                           |
| 每次模型派发                  | `model_attempt` 保存实际 Provider、绑定、派发状态、用量和必要结果；相同输入引用逻辑请求，经过变换的实际输入单独保存 |
| Agent 的模型步骤尝试          | `agent_attempt` 保存调用关联和 Harness 自己产生的结果；模型原始结果通过已接受调用/尝试引用读取                      |
| 工具、Workflow 的实际输入输出 | 保留在对应 `agent_attempt`；文件内容引用 Artifact                                                                   |
| 私有协议续接状态              | 指定一个权威存储位置及清除时机；Checkpoint 保存该状态或稳定引用                                                     |
| 用户可见过程                  | Redis 短期保存增量，SQL 完整投影供 Message 加载，共用同一响应协议                                                   |

引用必须能够确定原内容及其版本，读取过程不重新调用模型或工具。当前无需增加通用内容仓库、内容寻址服务或新的 Blob 表。

位置：DATA_DESIGN 第 2、3、4、5 节；TECH_DESIGN 第 4.3、4.4 节。涉及 AC-10、AC-12、AC-13、AC-17。

### R2 · P1：Workflow 草稿的资产唯一约束与现有保存行为冲突

`agent_workflow_draft.asset_id` 被设计为唯一，关系图也画成一对一。现有 `WorkflowAssets.save()` 按所有者、入口合同、版本和脚本摘要复用已有资产。两个 Run 生成相同脚本后，保存两张邀请卡片会取得同一个 assetId；拟议唯一约束会使第二张卡片的保存失败。

**建议保留保存邀请实体，改为多份邀请可以关联同一资产。**

- 删除 `asset_id` 的唯一约束，保留外键；每份邀请自己的保存操作仍须幂等。
- 保留邀请的来源 Step、有效期、保存结果和时间；相同脚本在不同 Run 中可以有独立邀请。
- 脚本正文、摘要和入口版本优先引用来源 Step 已冻结的定义，避免邀请再复制一整份源码；已保存资产仍维护自己的独立生命周期。
- 原草稿仅允许保存已验证脚本。未来若增加独立编辑，再为编辑版本设计存储。

将草稿直接并入 `workflow_asset` 会牵动现有资产去重、列表、启停和执行可见性。本课继续保留轻量邀请表，改动更集中。

位置：DATA_DESIGN 第 6 节，尤其第 294、303 行；现有 [workflows/assets.py](../../../apps/agent/src/compass_agent/workflows/assets.py) 第 63、199 行起的去重与保存实现；[workflows/drafts.py](../../../apps/agent/src/compass_agent/workflows/drafts.py) 第 13、29 行起的独立邀请和 TTL。涉及 AC-07、AC-15。

### R3 · P2：不可变工具目录可以跟随 Run 保存

`agent_tool_binding` 每个 Run 创建一组不可变条目，当前读取和恢复都以 Run 为边界。现方案没有独立修改、跨 Run 查询或管理这些目录项的用例，单独的行身份、索引和复合外键增加了装载与迁移工作。

**建议放入 `agent_run.config_snapshot.tool_catalog` 的有类型 JSON。** Step 保存该快照中的稳定工具键；Run 已确定目录归属，Checkpoint 复用 Run 配置。继续保留声明、别名、Schema 摘要、非敏感绑定、策略和配置版本；仍按当前权限读取凭据、核验版本。

现有 Registry 已对工具声明设置数量和字节限制。落库 DTO 还需限制完整目录快照的大小并校验工具键唯一。只有出现目录项独立生命周期或实际跨 Run 检索需求时，再考虑独立表。

位置：DATA_DESIGN 第 3 节；[tools/registry.py](../../../apps/agent/src/compass_agent/tools/registry.py) 第 90 行起的不可变目录和声明限制。涉及 AC-16。

### R4 · P2：序号和审批身份可以减少一层

**序号方面：** 最新合同采用 Run 流式接口，因此保留 Run 内 seq，删除 Session 流序号及重复 run_seq 字段。Message 以 Run/消息顺序加载完整投影，并返回每个完整装载 Run 的增量游标。

- v2 使用 Run `seq`；Message 的分页游标另行表达历史位置，两种游标分别服务其实际读取路径。
- 既有 v1 如仍有消费者，由适配器为原流编码顺序。兼容输出无独立断点读取要求时，可按原流顺序生成编号。
- `next_run_ordinal` 可删除：创建 Run 时锁 Session，按当前最大 ordinal 加一分配，保留 `(session_id,ordinal)` 唯一约束。
- 上下文起点的上界校验改在同一事务执行；数据库继续约束起点为正数。

**审批方面：** 现方案规定参数变化创建新等待点，提案在原 Wait 内不可变，同时又要求客户端携带 proposalId、proposalRevision、proposalDigest、decisionId 和 requestId。

- 用不可变 `waitId` 标识这次提案版本；新参数创建新 Wait。
- 决定重试统一复用 `agent_command` 的 requestId；已消费的决定关联命令记录即可。
- 决定请求保留 `waitId、requestId、expectedVersion、decision`；服务端校验归属、当前权限、状态和期限。
- 提案摘要继续用于绑定实际效果，Action ID 继续跨重试和恢复保持稳定。

这些建议涉及 PRD BR-03、BR-05 和新协议的表示方式，需要与 DTO、示例及适配器一起修订。原有顺序、错配拒绝、决定幂等和参数绑定行为继续保留。

位置：DATA_DESIGN 第 2、4、7 节；TECH_DESIGN 第 3.2 节；HITL_DESIGN 第 2、3 节。

### R5 · P2：历史 model_attempt 回填超出当前恢复需要

方案保留旧 `model_call.execution`，又计划将其展开为 `model_attempt`，引入 `origin=legacy_summary`、缺 ID 派生、缺少占额填零等历史分支。主方案同时明确旧调用不会进入新 Run 恢复。

**建议旧记录继续从原 execution 读取，model_attempt 从本次升级后的真实调用开始记录。** 保留旧数据和旧查询即可满足原模型记录的升级兼容；删除本次迁移中的历史展开、`origin` 字段及相关回填分支。

`model_attempt` 表本身建议保留。当前 execution DTO 只表达收口结果，Repository 也在调用完成时才写入；直接把派发中间态塞进它，会改变现有查询合同。每次物理尝试仍须派发前登记、结局逐次提交，未知用量继续保留占额。收口后的 execution 作为兼容投影由同一服务生成。

位置：DATA_DESIGN 第 240、260–262 行；[schemas.py](../../../apps/agent/src/compass_agent/schemas.py) 第 1050、1067 行；[models/repository.py](../../../apps/agent/src/compass_agent/models/repository.py) 第 502、559 行。涉及 AC-07、AC-13。

## 3. 全部表的取舍

| 拟议表                 | MVP 建议           | 当前用途与精简点                                                                 |
| ---------------------- | ------------------ | -------------------------------------------------------------------------------- |
| `agent_session`        | 保留并精简         | 会话归属、列表、上下文边界；去掉 next_run_ordinal，补最小类型信息                |
| `agent_run`            | 保留并精简         | 每轮生命周期和配置；收纳工具目录，去掉第二套事件游标                             |
| `agent_message`        | 保留               | 展示正文和模型规范上下文；省去从公开事件重建私有模型输入的复杂度                 |
| `agent_step`           | 保留               | 稳定逻辑步骤、父子关系和已接受尝试；Workflow 恢复复用它                          |
| `agent_attempt`        | 保留并明确内容归属 | 重试、未知结局、工具结果和资源回执；模型结果通过关联复用                         |
| `agent_outbox`         | 保留关键变化补发   | 替换全量 SQL 事件表；高频 delta 存 Redis，完成后的 Message 加载依靠完整 SQL 投影 |
| `agent_checkpoint`     | 保留               | 执行位置、上下文引用、预算及私有恢复状态；公开事件无法单独提供这些内容           |
| `agent_tool_binding`   | 合入 Run           | 当前为随 Run 冻结和整体读取的目录快照                                            |
| `agent_artifact`       | 保留               | 原文件字节、身份、来源与下载；当前 bytea 已足够                                  |
| `agent_workflow_draft` | 保留为轻量邀请     | 独立 TTL 和保存状态；引用冻结脚本，多对一关联资产                                |
| `agent_command`        | 保留               | 统一处理首问和控制命令的幂等；审批复用它                                         |
| `model_attempt`        | 保留，去掉历史回填 | 模型物理调用派发、未知结局和费用；保持原 model_call 查询接口                     |
| `agent_wait`           | 保留并精简身份     | 持久等待、人工决定或外部事件；提案随 Wait 不可变                                 |
| `agent_action`         | 建议保留           | 批准之后效果仍可能 pending/unknown；独立记录稳定 Action ID 与效果核对            |
| `agent_demo_effect`    | 保留为课堂效果账本 | 覆盖效果已提交、Run 回执丢失的故障窗口；归类为演示适配器数据                     |

`agent_action` 当前与审批 Wait 一对一，可以物理合并，但审批结论和效果结局仍需分别维护，核对等待还要引用原动作。本轮建议保留清晰的动作记录，优先精简重复身份与内容。课堂效果账本与 Runtime 回执继续使用独立提交事务，以覆盖 AC-06。

既有 `model_provider/model_call/model_change`、`workflow_asset`、`mcp_connection`、`sandbox_allocation` 继续复用。连接与分配有自己的配置、资源生命周期，避免复制进新的 Agent 表。新增 Agent 运行表继续使用 `agent_` 前缀。

## 4. 关键字段取舍

| 字段/结构                                      | 建议                     | 理由与条件                                                                              |
| ---------------------------------------------- | ------------------------ | --------------------------------------------------------------------------------------- |
| Session `last_event_seq`                       | 删除                     | 最新合同采用独立 Run 流，Session 元数据由 version 管理                                  |
| Session `next_run_ordinal`                     | 删除                     | 已有 Session 锁和 Run ordinal；在创建事务计算下一轮                                     |
| Session `context_start_ordinal`                | 保留                     | 当前切换模型会重置输入历史、继续展示旧内容，需要持久保存边界                            |
| Run `projected_seq` / Redis Stream ID          | 保留一套 Run 流位置语义  | SQL 只存投影位置，Redis 保存实际流尾；无重复计数器                                      |
| Session/Run `version`、Message/Step `revision` | 保留各自语义             | 分别服务控制并发和对象更新；事件 seq 服务流顺序                                         |
| Run `recovery_status`                          | 优先派生                 | 由 Run 状态、原因、Checkpoint 与未决调用给出可恢复性；真正 Resume 时重新验证            |
| Run `control_intent=cancel`                    | 删除这个取值             | `status=cancelling` 已表达取消受理；边界暂停仍需保留待处理的 pause 意图                 |
| Run `execution_epoch`                          | 保留                     | 阻止旧尝试或已失效执行者提交新结果                                                      |
| Run `owner_boot_id`                            | 结合单实例执行流程再裁剪 | 若仅用于标记旧进程，可由启动扫描和 epoch 处理；当前先保留执行权约束，不增加 M6 租约字段 |
| Run `cleanup_status`                           | 保留                     | 工具已返回和资源已释放有独立结局，未知清理需持续可见                                    |
| `message.content_sha256`                       | 可删除                   | 当前没有独立的内容摘要消费路径；消息引用和 revision 已标识规范内容                      |
| Artifact、脚本、提案、请求、Checkpoint 的摘要  | 保留已明确用途的部分     | 分别用于字节校验、源码绑定、效果绑定、幂等冲突和损坏检测                                |
| Checkpoint 的多个 JSON 列                      | 可后续合并               | 整体写入和读取的状态可归入版本化 state；保持有类型 DTO 和私有数据保护，收益低于正文去重 |

Run 可恢复性派生应区分“可尝试恢复”与“当前发现阻塞”；未知结果、缺失产物或清理未确认均保留具体原因。删除存储字段后仍需提供同样的界面状态。

Step 和 Attempt 继续区分逻辑节点与真实执行。完成时必须有 accepted_attempt 的约束应限定到产生执行结果的节点；纯分组或控制节点若复用 Step，仅为满足约束创建空 Attempt 会增加无效记录。

## 5. Session 类型、图标与状态

用户已明确提出类型图标及运行状态展示需求。下一版 PRD、数据设计与列表接口需要共同补齐；当前数据设计只有标题和运行关联，尚未定义这些展示信息的来源。

建议采用以下最小结构：

| 信息               | 落点                                 | 语义                                                                              |
| ------------------ | ------------------------------------ | --------------------------------------------------------------------------------- |
| `session_type`     | Session 列，默认 `chat`              | 区分普通对话与专用 Workflow 会话                                                  |
| `workflow_key`     | Session 可空列                       | 专用预定义 Workflow 入口的稳定业务键；真实入口接入时登记和校验                    |
| `icon_key`         | 列表 DTO，按类型及 Workflow 定义解析 | 普通对话默认图标，专用入口使用其图标；缺少定义时回退默认图标                      |
| `latest_run_id`    | 列表 DTO，从最大 ordinal 的 Run 读取 | 同一 Session 当前或最近一轮                                                       |
| `execution_status` | 列表 DTO，从该 Run 派生              | created/running 显示进行中；waiting 显示待确认；paused、cancelling 和终态分别展示 |

普通对话中临时调用一个 Workflow，Session 类型保持 chat；从“生成 PPT”这样的专用入口创建会话时，记录 workflow 类型及入口键。未来入口配置与已保存脚本资产可能承担不同职责，当前不增加通用模板平台或图标管理表。

Session 的运行状态继续由 Run 维护。列表读取、选中会话的 `run.upsert` 包和 Message 加载使用同一公开状态映射；类型图标与运行指示分别表达用途和当前状态。切换会话、重获焦点以及存在活动任务时，列表需更新最新状态，避免未选中的会话长期停留在 loading。

## 6. 应保持的交付边界

- 实时和刷新消费同一包 Schema 与 reducer；当前 PRD 要求先持久化再输出。已选定 AOF always、关键 Outbox 和完整投影门禁，故障边界见第 8 节。
- Event、Message 与 Checkpoint 按职责保存；私有输入和执行恢复状态继续受到访问控制与加密保护。
- 模型和工具的物理派发保留事前记录，未知结果保留预算占额和核对状态。
- 产物保存原字节，Checkpoint 绑定原结果；重启后继续使用已经完成的模型、工具和子调用结果。
- Workflow 的恢复日志、源版本与交付顺序继续保留；本次减法不缩减 AC-15 的恢复要求。
- 当前维持单实例执行，不加入队列、Worker 租约、自动接管、通用归档或新的内容存储服务。
- 数据库所有权、迁移 SQL、权限、约束及恢复验证沿用项目规则。

## 7. 下一步与验证

优先按 R1、R2 明确正文归属与草稿资产关系，再把目录、序号、审批标识和旧数据回填的精简统一同步到数据设计、PRD、接口示例及 HTML 审阅版；同时补齐 Session 的最小展示契约。顺序上先收敛合同，再生成迁移。

实施验证复用既有 AC，重点补充以下具体输入：

1. 两个不同 Run 产生相同脚本，先后保存邀请，最终复用同一资产，两张卡片均正确恢复。
2. 同一 Session 并发重复首问、连续多轮和上下文重置，ordinal 与边界正确；Message 加载游标衔接对应 Run seq，日志过期后重载 Message。
3. 同一不可变等待点重复决定、参数变化后提交旧决定，分别得到原回执和明确冲突；真实重启后核对同一 Action。
4. 模型派发前后故障、重试/Fallback 和旧调用读取：新增调用逐次可查，旧 execution 可继续读取，内容引用不丢失实际输入差异。
5. 普通对话、专用 Workflow、运行中、等待审批和终态分别刷新及切换会话，图标和状态与原 Run 一致。

本次完成静态设计与现有代码核对；运行恢复、迁移和产品验收尚待实施。当前工作区已存在 0008 迁移及 `api/harness/workflows/tools` 拆分源码，主方案关于这些内容仍待落地的基线描述需要在下次设计修订时更新；数据库实际应用版本本次未查询。

## 8. 已采纳：本地 Redis 短期流与数据库投影

2026-11-12 用户明确要求引入本地 Docker Redis。本节替换此前的存储候选比较；有效设计见 [Redis 方案](../design/REDIS_DESIGN.md)，双接口与游标见 [主方案](../design/TECH_DESIGN.md)。其余内容去重、工具目录与历史回填建议仍待逐项落实，本次不自动合并这些独立调整。

### 8.1 Event 的存储

Event 是共享响应里的业务包，包含文本增量和公开对象变化；SSE 心跳、HTTP 分片不保存。高频增量合并后写 Redis Streams，完整消息和展示对象定时投影到 PostgreSQL。原 `agent_event` 全量 SQL 表移除，改为只记录关键业务变化的 `agent_outbox`，补齐 SQL 提交后发布失败的窗口。

### 8.2 已确定的一致性边界

- Redis 使用本地命名卷和 AOF always；文本包取得持久确认后才可对前端发送。已确认内容在保卷进程/容器重启后补回投影。
- SQL 关键业务状态和 Outbox 同事务提交；关键批次先完整发布到 Redis，再强制提交消息、public_view、投影游标和投递回执，随后放行关键 SSE 包。
- Message 使用同一已投影 Run 头、消息、过程与游标；终态公开前，完整回答和最终视图已存 SQL。业务状态和展示投影分工明确。
- Stream ID 直接作为 Run 游标，采用字符串 `毫秒-序号`，按两段整数比较，允许间隔。SQL 仅维护 projected_seq，不增加 Session 流序号或逐包 SQL 计数。
- 活跃/等待流不设置 TTL、不做 MAXLEN 裁剪。终态、投影到尾且 Outbox 投递完成后，整条流保留 1 小时；过期后 Message 仍完整可读。
- 容量/持久写失败停止新派发；已返回执行结果继续保存 SQL/Outbox。永久丢失活跃流时保留 SQL 投影并显式受阻，不自动重建同名空流。

### 8.3 Message 非流式，Run 流式

Message JSON 响应与每帧 Run SSE data 均为 AgentResponse，业务包共用判别联合、validator 和 reducer。刷新先加载完整 Message 投影，再从该 Run 的 projected_seq 接续流。短期增量过期只触发持久加载，不影响最终历史。

### 8.4 MVP 边界与验证

本课只有单个 Agent 里的发布/投影任务和一个本地 Redis 实例；无需独立消费者服务、消费组、集群或租约体系。高频 delta 不生成 SQL 行；Outbox 行随关键操作增长。保留两个数据库提交阶段解决业务提交与流发布之间的可靠衔接，执行 Checkpoint 和效果账本保持直接持久化。

验收以 PRD AC-10、AC-11、AC-17、AC-19 为准：XADD 前后退出、关键 SQL 提交后发布失败、部分批次补发、投影提交前后退出、Redis 保卷重启、写满背压、活跃流丢失以及终态流过期后的完整刷新。AOF 恢复边界限于原卷完整的本地故障，永久磁盘损坏需要备份与人工恢复。
