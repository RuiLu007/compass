# Feature 991：第 13 课技术方案评审

> 评审日期：2026-11-05 | Profile：standard | 结论：有条件通过
>
> 评审对象：[TECH_DESIGN.md](../design/TECH_DESIGN.md)、[CODE_DESIGN.md](../design/CODE_DESIGN.md)。有效需求与 AC 维护于技术方案；本 Feature 无独立 PRD。
>
> 代码基线：`d08488b0b9f9dd2614702426f053e1421e9182c9` 加本次读取的未提交工作区。重点包含已接入的 Feature 992 路由代码。评审依据 my-review-design 工作流裁剪，产物路径采用随包 `FEATURE_LAYOUT.json` 的 `design-review` 登记。

## 1. 总体结论

**有条件通过。** 保留单次 Runtime，在外层增加薄执行器、进程共享 Gate 和有界执行摘要，能够覆盖本次进程内可靠执行目标。数据库只追加 `model_call.execution` 可空 JSON 列，符合最多两次尝试和随调用查询的实际访问方式，当前无需独立 Attempt 表。

进入正式实施前，需完成两项设计修正：

1. 按当前已接入的分类与路由链明确重试用途、父期限和协议衔接，补齐任务及验收。
2. 在获得执行槽后、构造单次策略前处理 Deadline 已耗尽的边界。

两项均为 P1，未发现 P0。条件由方案维护者 andy 落实；修订正文和代码设计后定向复评。第 1 项通过调用链及场景映射核对，第 2 项通过本报告所述边界探针及后续执行器测试核对。本轮只交付评审产物。

## 2. 关键发现

### R1 · P1：当前 Chat 已有分类与路由阶段，方案缺少可执行的重试用途划分

**状态：已核实基线差异；错误重试是按现有设计直接接入时的风险。**

**定位：** 技术方案第 19 行、§2.2、§2.5、§5；代码设计第 81 行和第 208–227 行。当前实现见 [RoutingService](../../../apps/agent/src/compass_agent/routing/service.py) 第 34–69 行、[HTTP 入口](../../../apps/agent/src/compass_agent/model_api.py) 第 254–296 行、[DTO](../../../apps/agent/src/compass_agent/schemas.py) 第 448–500 行和 [浏览器 Hook](../../../apps/web/src/features/models/use-model-call.ts) 第 67–139 行。

方案仍描述“992 尚无技术方案及对应执行器源码”，并将供应商切换、Fallback、JEV 降级和两次生成预算归到 992。当前 992 BRIEF（本地需求资料；不随本节点提交）及其 PRD（本地需求资料；不随本节点提交）已经收敛为分类、字典、选模、单次主推理；对应代码已存在于工作区。该链路按角色调用同一个 `ModelService`：

- JEV 分类、Flash 分类和主推理都经过 `prepare/run`，共用 `purpose=chat` 的可信上下文。
- Flash 分类和主推理均使用 `GenerateInput`，仅凭输入种类或 `capability_mode=verified` 无法决定重试预算。分类用途目前只在 `RoutingService.stage` 中明确，现有 `run` 参数未携带它。
- 主推理使用父 `call_id`，分类记录使用派生 UUID；父 180 秒期限在 `RoutingService.run` 开始时创建，再传给各阶段。
- Chat 的实际接口为 `POST /v1/models/calls`，返回 `RoutedCallResponse/RoutingFinalEvent`；Provider 诊断继续使用 `CallResponse/FinalEvent`。当前 Chat 的 HTTP runner 为 `routing.run`。

**触发与影响：** 若按“Chat=2、诊断和决策=1”直接在 `ModelService.run` 统一选择策略，Flash 分类收到 429/503 时也可能被重试，增加分类次数和阶段耗时。若仅接入旧调用路径，新增执行器又可能漏掉真实 Chat 主推理。当前任务表没有列入 `routing/service.py`，代码附录的 final 类型和任务登记描述也尚未覆盖路由后的入口，开发者需要自行补出关键衔接。

**最小修改：**

1. 更新现有能力基线，撤下已失效的 992 恢复策略归属和“两套执行器”前提；可靠执行继续由本 Feature 承担。
2. 在服务端阶段调用处显式传入逻辑策略或执行用途。JEV 分类、Flash 分类和 Provider 诊断均最多一次；选定主模型生成最多两次。使用既有路由父 Deadline，保留分类阶段的更短期限，重试使用同一 Provider 和配置快照。
3. 明确本次预算单位：主生成调用最多两个 Attempt；一轮路由请求可包含 JEV 一次、Flash 分类一次和主生成两次，合计最多四次模型推理。既有派生分类记录继续保留，`execution` 的两项上限属于每条模型调用记录。无需增加父调用表。
4. 在 T4/T5/T6 中登记实际路由接入点；任务集合登记真实 HTTP 根 runner。浏览器提交示意覆盖两种 final 类型，路由失败且 `result=null` 时保持失败，不生成可提交的模型结果；保留 classification/route_selected 事件。
5. 补充路由后的验收：Flash 分类遇临时拒绝仍只调用一次；主推理 429→成功执行两次；分类消耗后的剩余期限不重置；两种 final 都在干净 EOF 后提交；shutdown 能收束 Chat 与诊断入口。

方案 §4.2 已要求在 992 先落地后重新核对并更新设计。当前工作区满足该触发条件，应在实施前完成这项更新。原 AC-991-01/02/05/10/12/15 可按上述实际调用层级细化，保持技术方案为本 Feature 的 AC 真源。

### R2 · P1：执行槽获得后耗尽期限，会在进入 Runtime 前逃逸为 ValueError

**状态：代码设计片段已复现。**

**定位：** [代码设计](../design/CODE_DESIGN.md) 第 101–120 行；[现有 CallPolicy](../../../apps/agent/src/compass_agent/llm/policy.py) 第 50–51 行；[ModelService.run](../../../apps/agent/src/compass_agent/models/service.py) 的普通异常分支。关联 AC-991-05/07。

执行器在等待执行槽之前检查 Deadline，成功获得槽后立即用新的 `attempt_started` 重建 `CallPolicy`。正文和附录将获得槽后的期限检查交给 Runtime。现有 `CallPolicy` 要求 `deadline_monotonic - started_monotonic > 0`，因此当槽在临界时刻取得，或事件循环在 acquire 返回边界发生延迟时，策略构造会先抛异常，Runtime 无法处理该次超时。

**复现：** 直接提取代码设计中的 Gate 和 execute 片段，使用项目现有 `CallPolicy`；注入总预算 10ms，并在成功取得槽后加入 20ms 同步调度延迟。辅助对象仅用于观测执行器，未修改生产源码。结果为：

```text
escaped: ValueError call duration must be within 180 seconds
runtime_calls: 0
logical_inflight: 0
available_slots: 1
```

本例的槽已正确释放。问题在于超时没有形成 `model_timeout / deadline_exceeded` 逻辑结果，也没有返回执行摘要。接入现有 `ModelService` 后会进入普通异常兜底；该兜底对已创建执行任务保守设置 `sent=True`，可能将零出站超时记为内部错误及上游结果未知。

**最小修改：** 在已拥有槽的 `try/finally` 内取得一次 `attempt_started`，先判断 `attempt_started >= deadline`，到期时直接形成超时结果；通过后再使用同一个时间值构造策略。维持现有 `CallPolicy` 正时长校验和 Runtime 出站前检查，保留正常释放槽的路径。

**验证条件：** 首次取槽后到期时，零 Adapter 调用、零 Attempt、已知零用量、`model_timeout`、`execution.stop_reason=deadline_exceeded`，逻辑名额及执行槽全部归还。另测第一次临时拒绝后，在第二次取槽边界耗尽期限，保留第一条摘要和其用量，不增加第二条出站记录。

## 3. 保留的设计与实施期验证

| 范围                   | 本轮判断                                                                           | 后续验证                                                                                                                |
| ---------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 同 Provider 有界重试   | 保留。默认禁止恢复，以发送事实、无已展示片段及清理确认放行，范围符合当前纯模型生成 | T1/T2 验证真实 SDK 归一后的 429/503、其他 4xx、已发送超时、取消及关闭失败；恢复枚举保留到 RuntimeResult                 |
| 共享 Gate 与资源隔离   | 保留。N 限逻辑进入量，K 限本地执行与未确认清理占用；准备事务的边界已明确           | T2/T4 验证多 Service 共享实例、等待取消、关闭失败及真实 HTTP 根任务 shutdown；并发指标按本地进程解释                    |
| 输出校验与最终提交     | 保留现有类型分支、本地 Schema 校验及 EOF 后提交；按 R1 补齐现有路由协议            | T5/T6 覆盖两种 final、分类失败、Schema 失败、记录写入失败、重复 final 和尾帧异常                                        |
| `model_call.execution` | 追加可空 JSON 列合理；两项摘要和 16 KiB 上限有明确消费者；旧记录 null、无回填      | T3 验证摘要与终态同事务更新、查询隔离和旧行读取                                                                         |
| 0003→0004 及恢复       | owner、迁移真源、固定区间 SQL、历史不可变、两端匹配发布和回退前提已登记            | 实施时复核 Alembic head；真实 PostgreSQL 验证空库、旧库、权限、SQL 一致性和恢复。旧版 readyz 对新版本的限制已有处理安排 |

当前无需追加服务、分布式队列、常驻恢复任务、Attempt 表、逐 Attempt SSE 或策略管理页面。原有故障注入、浏览器联调和负载测试任务继续保留；R1/R2 所需用例直接补入已有安排即可，无需另建一套测试体系。

## 4. 本轮执行结果与限制

- 已读取 BRIEF、上下文、技术方案、代码附录、项目数据库规范、当前 992 需求，以及 Runtime、Transport、Adapter、Service、Repository、路由、DTO、BFF 和浏览器相关实现。已通过只读 recall 加载当前 design-review 场景的个人偏好，未新增记忆。
- 执行 `uv run --locked --package compass-agent pytest apps/agent/tests/test_runtime.py apps/agent/tests/test_model_lifecycle.py apps/agent/tests/test_intent_routing.py -q`：**37 passed，2 warnings**。warnings 来自第三方 TypedDict 的 ReadOnly 提示；这些测试验证现有单次 Runtime、生命周期和路由基线。
- 已执行 R2 的代码设计边界探针，复现策略构造异常。该结果适用于附录中的协调逻辑；本次没有生产版 ReliableExecutor 可供验收。
- 未执行本 Feature 的真实 PostgreSQL 迁移、浏览器联调、SDK 重试矩阵或 1,000 次负载验收；上述内容继续按原实施任务完成。
- 本轮文档检查：Prettier、Feature 产物路径、报告内部文件链接和新增文件空白检查通过；`git diff --check -- mai-specs/991-lesson-13-reliable-model-execution` 通过。未更改技术方案、需求、源码、Phase 或 ROADMAP。

## 5. 优先下一步

先按当前路由链修订 R1，再修正 R2 的取槽后期限检查，随后定向复评这两个边界。可直接使用请求：**“按评审报告修订 Feature 991 的技术方案和代码设计，保留现有最小架构，再复评 R1/R2。”**

## 2026-11-06 实现复核

保留前文初次评审记录。R1/R2 已修订并落实至实现：路由用途策略与共享父期限由路由/集成测试验证；取槽后到期有首次/第二次边界测试；两类流式终态和实际后台演示通过浏览器验收。当前实现、完整测试结果与容器未验项见[实施与验收](../IMPLEMENTATION.md)。初次评审的两个实施前条件已满足。
