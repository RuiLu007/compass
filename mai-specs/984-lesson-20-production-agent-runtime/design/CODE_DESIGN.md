# 第 20 课 · 定时任务与 Agent 内消费代码设计

[需求简报](../BRIEF.md) · [技术方案](TECH_DESIGN.md)

以下为拟议 Python 伪代码，供设计评审。已有函数沿用其语义，新增符号仅用于说明接缝。

## 1. 模块和入口

| 拟议模块                                                                            | 职责与边界                                                                          |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `scheduler/{entities,repository,service}.py`                                        | 计划管理、触发状态、持久记录和限时令牌。                                            |
| `scheduler/main.py`、`scheduler/runner.py`                                          | 同一 Agent 镜像的单实例角色。数据库租约、到期扫描、有界 HTTP 交付、重试与状态同步。 |
| `api/schedules.py`、`tools/builtin/schedule_read/`、`tools/builtin/schedule_write/` | 共用计划服务。读按实际作用域，写工具经计划所有者确认。                              |
| `api/scheduler_triggers.py`、`runtime/submission.py`                                | 内部服务鉴权、按持久触发构造执行身份，在统一 Run 受理事务内关联触发。               |
| Web `automations` 与 BFF                                                            | 列表、表单、执行记录，使用同一代理客户端和生成契约。                                |
| `runtime/run_consumer.py`                                                           | 各 Agent 副本内迁入领取、续租、推进、控制意图与退出处理。                           |

## 2. Web 与 Chat 计划写入

### 内置定时任务工具契约

模型入参由 Pydantic 判别联合生成严格 JSON Schema，并复用 HTTP 的 Cron、时区、模型选择和可编辑字段校验。只传业务字段，租户和操作者从 `ToolExecutionContext.trusted` 读取。

```python
# 拟议输入类型；模型选择沿用现有 MainModelSelection。
class ScheduleListArgs(StrictModel):
    operation: Literal["list"]
    q: str | None = None
    status: Literal["enabled", "disabled"] | None = None
    cursor: str | None = None

class ScheduleGetArgs(StrictModel):
    operation: Literal["get"]
    scheduleId: UUID

class ScheduleHistoryArgs(StrictModel):
    operation: Literal["history"]
    scheduleId: UUID
    cursor: str | None = None

class ScheduleCreateArgs(StrictModel):
    operation: Literal["create"]
    name: str
    question: str
    selection: MainModelSelection
    cron: str
    timezone: str
    enabled: bool = True

class ScheduleUpdateArgs(StrictModel):
    operation: Literal["update"]
    scheduleId: UUID
    expectedRevision: PositiveInt
    patch: SchedulePatch

class ScheduleDeleteArgs(StrictModel):
    operation: Literal["delete"]
    scheduleId: UUID
    expectedRevision: PositiveInt

ScheduleReadArgs = Annotated[
    ScheduleListArgs | ScheduleGetArgs | ScheduleHistoryArgs,
    Field(discriminator="operation"),
]
ScheduleWriteArgs = Annotated[
    ScheduleCreateArgs | ScheduleUpdateArgs | ScheduleDeleteArgs,
    Field(discriminator="operation"),
]
```

`SchedulePatch` 只能包含名称、分析内容、模型选择、Cron、时区和启用状态，至少提供一个字段。计划 ID、归属与当前版本由服务控制。使用计划服务原有边界校验字符串和游标，分页大小沿用管理 API 的上限。

| 绑定             | 拟议设计                                                                                                                                                                                                     |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 静态目录         | 在 `tools/builtin/schedule_read/` 和 `tools/builtin/schedule_write/` 分别登记 `SPEC`。能力分别为读和写。Agent 的产品装配明确启用，后台定时 Run 只声明读能力。                                                |
| Runtime 装配     | `RunToolBindings` 注入计划服务和当前授权组件，以 `ToolSpec` 的同一启用条件生成定义、预检和实际注册。对需要依赖的两个 SPEC，在注册接缝传入每 Run 绑定；保留现有无参工厂和静态工具行为。                       |
| `schedule_read`  | `InProcessBinding`，`effect=read_only, approval=none`。读取前校验当前管理权限与归属，成功载荷复用 `SchedulePage`、`ScheduleView` 或 `TriggerPage` 的安全视图。                                               |
| `schedule_write` | `InProcessBinding`，复用 `effect=external_write, approval=required` 的有副作用门禁，`parallel_safe=False`。写入影响后续任务计划，使用串行调用和既有工具等待、续跑协议。                                      |
| 确认             | 在形成提案时异步取计划快照和下一次时间。沿用 `ToolApprovalProposal` 的公开摘要；Run 服务端存储受保护的 `schedule_owner` 种类、归属、变更 DTO、当前版本与摘要。首次提案已落库后复用它，恢复时按批准内容执行。 |
| 结果与错误       | 持久写入后返回 `ScheduleWriteReceipt`（ID、revision、状态、nextFireAt），Chat 依据成功回执表述完成。未找到或无权返回安全拒绝；过期版本提示重新查询和确认，已有提交按稳定 Action ID 读取。                    |

现有 `prepare_proposal` 回调是同步的。拟给登记增加可选异步提案准备钩子，由单次调用预检在数据库查询完成后把不可变预览挂到该次准备结果，再复用原摘要回调。批次预检、实际派发和续接使用同一预览或已批准快照，避免重复读取生成不同的确认内容。原有必需声明优先，随后为有权的计划工具保留名额，再填充可选 MCP/Workflow。并行模式下写工具单独串行派发，混批在生成提案前拒绝并提示独立重发。既有工具继续走原同步回调。等待查询的 `allowedActions`、批准接口与接管恢复入口共用 `authorize_wait_action(wait, phase, actor)`。种类和主体由服务端从同一持久 Wait 提取。恢复入口替换目前固定的 `approval=True` 检查。属于原业务审批的提案继续要求现有管理员权限。

```python
async def execute_schedule_write(args, tool_context):
    # 只在现有 HITL 已确认并恢复原调用后进入执行绑定。
    context = tool_context.trusted
    binding = schedule_call_binding.from_execution(tool_context, args)
    if binding.origin == "scheduled":
        raise ToolRejected("schedule_write_disabled")
    grant = action_ledger.approved_schedule_grant(
        binding.run_id, binding.step_id, binding.call_id, binding.arguments_digest
    )
    if grant is None:
        raise ToolRejected("schedule_confirmation_required")
    command = grant.canonical_command(request_id=grant.action_id)
    return await journal.execute_local_effect(
        binding,
        grant,
        lambda attempt: asyncio.to_thread(
            commit_schedule_effect, context, command, grant, attempt
        ),
    )
```

Journal 的 execute_local_effect 负责冻结/重放 Attempt，并将准备好的 Attempt 交给以下事务；写入由共用计划服务执行业务校验。Web/API 使用其独立 HTTP 事务。Chat 调用专用 Journal 本地效果分支。批准后仍要即时复核当前管理权限，底层不向 Web、Provider 或其他服务发 I/O。

```python
def commit_schedule_effect(context, command, grant, attempt):
    access.require_current_plan_permissions(context, command)
    with store.transaction() as tx:
        deployment.require_current_mode(tx)
        previous = store.command(tx, context, command.request_id, command.canonical_payload)
        if previous:
            return journal.read_accepted_schedule_result(tx, grant, attempt, previous)
        session, run, job = journal.lock_execution(tx, grant)
        step, attempt, action = journal.lock_local_effect(tx, run, attempt, grant)
        plan = repository.lock_owned_plan(tx, context, command.plan_id) if command.plan_id else None
        journal.require_live_approved_local_effect(tx, run, job, action, grant, command)
        if plan and plan.revision != command.expected_revision:
            raise Conflict("schedule_revision_changed")
        receipt = repository.apply(tx, context, command, plan, db_now(tx)).safe_receipt
        if plan:
            repository.skip_unclaimed_triggers_for_prior_revision(tx, plan.id)
        store.record_schedule_command(tx, context, command, receipt)
        return journal.accept_local_effect_in_transaction(
            tx, session, run, step, attempt, action, receipt
        )
```

锁顺序为运行模式 → 命令键 → Session/Run/job → Step/Attempt/Action → 计划/触发。准备阶段记录本地 Attempt，执行事务在锁取得后复核取消、当前租约/epoch、批准快照和计划版本，并原子写计划、命令、Action、已接受的 Attempt 执行结果、Checkpoint 与 Outbox。使用事务内 Journal 接缝生成 accepted attempt 和边界回执。通用外部工具的 `dispatched/unknown` 标记和第二次 `accept` 不用于这条本地分支。Root Checkpoint 写入后按现有 BoundaryYield 交还消费。

重放优先从 Journal 读取已接受结果，并校验原 Action、请求摘要和持久回执的一致性。进程失联时，事务未提交则效果和 Attempt 结果均不存在，可在新租约下重新授权并复用原批准；已提交则恢复原结果，计划只写一次。发现回执与 Journal 不一致时阻断并暴露诊断原因，避免补写重复效果。外部工具未知结果仍走原有保守核对路径。

## 3. 到期、HTTP 交付与受理

### Scheduler 运行类型和循环

```python
@dataclass(frozen=True)
class SchedulerLease:
    boot_id: UUID
    generation: int
    expires_at: datetime

@dataclass(frozen=True)
class DeliveryClaim:
    trigger_id: UUID
    token: UUID
    scheduler_generation: int
    expires_at: datetime
    deadline_at: datetime
    attempt: int

@dataclass(frozen=True)
class AdmissionResult:
    outcome: Literal["accepted", "retry", "rejected", "stale"]
    run_id: UUID | None = None
    retry_after: datetime | None = None
    reason_code: str | None = None

@dataclass(frozen=True)
class RunStatusSnapshot:
    run_id: UUID
    version: int
    status: DurableStatus
    finished_at: datetime | None
```

`SchedulerRunner` 持有 `SchedulerLeaseStore`、`DueScanner`、`TriggerDispatcher`、`RunStatusSync`。扫描器使用无 I/O 的 `CronCalculator` 和仓储。交付器使用仓储与 `RunAdmissionClient`，HTTP 超时或 429 归并为带重试时刻的单条记录。同步器只读安全的 `RunStatusSnapshot` 并按版本更新。仓储对每次写入校验当前 `SchedulerLease`，交付更新额外校验 `DeliveryClaim`。

```python
async def run_scheduler(stop):
    await deployment.require_scheduler_startup()
    lease = await leases.acquire(boot_id)
    if lease is None:
        return ExitCode.SCHEDULER_LEASE_BUSY
    async with supervised_lease(leases, lease) as session:
        await supervise_until_stop_or_lease_lost(
            stop,
            session,
            scan=lambda: scanner.scan(session.lease, batch_size),
            deliver=lambda: dispatcher.dispatch_due(session.lease, max_inflight),
            sync=lambda: status_sync.sync(session.lease, batch_size),
        )
```

`supervised_lease` 独立续租，监督器分开运行扫描、交付、同步。短时错误按循环间隔收敛，循环失败上报并停止写入，交由进程管理器重启。退出时先停领取，在途 HTTP 只在有界截止内等待。未持久化收口的触发按交付租约恢复；确认不再写入后仅释放当代角色租约。请求可能已送达 Agent 时，以原触发回执判断结果。

```python
def record_due_occurrence(scheduler_grant, plan_id):
    with store.transaction() as tx:
        deployment.require_current_mode(tx)
        scheduler_lease.lock_for_validation(tx, scheduler_grant)
        plan = repository.lock_due_enabled_plan(tx, plan_id)
        if plan is None:
            return
        scheduler_lease.require_current_after_locks(tx, scheduler_grant)
        now = db_now(tx)
        due = rules.most_recent_due(plan.rule, plan.next_fire_at, now)
        if due is None:
            return
        overlap = repository.has_unfinished_trigger(tx, plan.id)
        repository.insert_occurrence_once(
            tx, plan, due, detected_at=now, skipped_reason="overlap" if overlap else None
        )
        plan.next_fire_at = rules.first_after(plan.rule, now)

async def deliver_one(grant):
    try:
        result = await agent_http.submit_trigger(grant.trigger_id, grant.token)
    except TransportError:
        await triggers.defer_or_mark_for_reconciliation(grant)
        return
    if result.outcome == "retry":
        await triggers.defer_or_mark_for_reconciliation(grant, result.retry_after)
    elif result.outcome == "rejected":
        await triggers.settle_rejection_if_current(grant, result.reason_code)
    # accepted 已随 Agent 受理提交；stale 交给现任 Scheduler 按记录处理。
```

领取触发时持久化调度世代、交付令牌与期限，提交事务后才发 HTTP。429 采用 Retry-After 与有界指数退避加抖动的较晚时刻。失败耗尽尝试，或检查到截止已到时，把触发置为 `reconcile_pending`。有独立的就绪查询按批领取待核对记录并刷新令牌。旧世代或过期令牌的快照重领后先核对账本，只有仍可交付时才消耗新的尝试。

```python
def accept_trigger(service_identity, trigger_id, token, *, reconcile_only=False):
    internal_auth.require_scheduler(service_identity)
    saved = triggers.read_frozen_input(trigger_id)
    context, command = build_trusted_submission(saved, request_id=trigger_id)
    authorization = None if reconcile_only else access.check_scheduled_run(context)
    with store.transaction() as tx:
        deployment.require_current_mode(tx)
        scheduler_lease.lock_for_validation(tx, saved.scheduler_grant)
        queue.lock_admission(tx)
        receipt = store.lock_and_find_submit_receipt(tx, context, command)
        trigger = triggers.lock(tx, trigger_id)
        scheduler_lease.require_current_after_locks(tx, saved.scheduler_grant)
        if receipt:
            return triggers.verified_submitted_receipt(trigger, receipt)
        triggers.require_current_delivery_token(tx, trigger, saved, token, db_now(tx))
        if reconcile_only:
            return triggers.resolve_without_admission(tx, trigger)
        if trigger.past_delivery_budget(db_now(tx)):
            return triggers.close_unaccepted(tx, trigger, "delivery_budget_exhausted")
        if authorization.permanently_denied:
            return triggers.close_unaccepted(tx, trigger, "permission_revoked")
        if not authorization.allowed:
            raise AuthorizationTemporarilyUnavailable()
        run = submission.admit_new_run(tx, context, command)
        triggers.record_submitted(tx, trigger, run.id, accepted_at=db_now(tx))
        return run.receipt
```

`submit` 与 `reconcile` 由固定内部分支选择，外部用户无法提交该布尔参数。两入口按同一顺序取锁，锁后再次读取实际数据库时间检查角色和交付令牌。已接受回执按同触发归属和摘要核验，交付令牌已清理或用户软删会话后仍可核对。若当前无回执，核对入口仅在交付关闭且不可再受理时标记失败；仍有预算的恢复记录返回继续交付，由 Scheduler 另起正式 submit。核对请求超时不触发 Run 或增加交付次数。Agent 提交时同时完成 Run、job、Outbox、回执和触发关联。

## 4. Agent 内消费槽

```python
async def consume_slot(slot, driver, stop_claiming):
    while not stop_claiming.is_set():
        grant = await driver.claim(slot)
        if grant is None:
            await wait_for_next_poll(stop_claiming)
            continue

        async with driver.lease_heartbeat(slot, grant) as lease:
            try:
                while lease.valid and not stop_claiming.is_set():
                    receipt = await driver.advance_until_receipt(slot, lease.grant)
                    if receipt.status != "continue":
                        break
                    lease.accept_next_checkpoint(receipt)
            finally:
                await driver.request_release_if_still_owned(slot, lease.grant)
```

现有 advance 接口中的当前授权、可信 Run 上下文和 start_boundary 移入驱动服务。HTTP 过渡适配与 Agent 内驱动共用该接缝。续租必须保持同一 Run/epoch，失去 Run 租约或超过本地安全截止后停止推进，与既有租约规则一致。副本启动核对共享策略版本，为每条 Run 恢复其已记录的预算快照和已结算 Attempt。释放根执行边界前在原事务结算时间、步骤和工具次数；回收者从持久 Attempt 核对费用与尾段时长。持租者有界轮询共享暂停和取消意图，每次派发前再次检查。其他副本只记录请求，或在租约过期后进入回收。关闭时只让已在途的边界有界结束，release 以现有清理门禁为准。任一健康副本可在执行租约到期后领取回收令牌，核对持久状态并重排。

## 5. 跨副本接管与发布

```python
async def recover_expired_run(run_id):
    grant = await queue.revoke_expired_epoch_and_claim_recovery(run_id)
    if grant is None:
        return
    async with queue.renew_recovery_lease(grant):
        outcome = await runtime.reconcile_actions_resources_and_checkpoint(run_id)
        await queue.settle_recovery_if_current(grant, outcome)

async def publish_one_run(run_id):
    grant = await outbox.claim_publish_lease(run_id)
    if grant is None:
        return
    async with outbox.renew_publish_lease(grant):
        await streams.register_generation_if_newer(run_id, grant.generation)
        for batch in await outbox.read_ordered_pending(run_id, grant):
            for packet in batch.packets:
                await streams.append_atomic_once(run_id, grant.generation, packet)
            await outbox.project_committed_prefix_if_current(run_id, grant)
```

回收写入先锁 Run/job，在数据库实际时间验证执行租约或上次回收租约确实过期。同一事务撤销旧 epoch 并持久生成回收令牌。提交结论必须匹配当前令牌；原 Attempt、Action 与资源记录用于核对，未决效果依旧阻止重放。审批恢复和其他控制路径写持久状态，避免根据另一实例缺少本地 task 的事实强制接管。

每 Run 的 Outbox 按 SQL 分配的顺序发布，公开流式包在当前执行 epoch 的事务中登记。Redis 的世代登记只接受更高世代或同一世代重试。追加脚本同时验证世代、内容摘要与 packetId，重复包返回原游标。发布切换期间较早世代只能发送已提交的同序持久包；较新世代登记后，旧世代无法追加。SQL 投影在行锁下核对当前发布令牌及预期前一游标，只提交完整批次。发布失败保留 Outbox，从已提交游标恢复；Stream、世代与去重索引同一生命周期。活跃流或必要索引缺失、Stream 尾游标落后于 SQL 已投影游标时，暂停发布并按安全恢复契约处理。终态投影完成后统一清理，旧游标回到 Message 快照。消费者内存中的锁继续用于本地节流。

## 6. 实施前核对

- 同一触发的事务、令牌和 requestId 在进程中断、重复扫描和背压下应归并至一个 Run。
- 删除和领取竞争按首次领取提交顺序裁决，终态收口时不存在迟到派发。
- 进程内驱动沿用过期 epoch、等待释放和外部效果核对，回归现有 SSE、Timeline 及公开消息行为。
