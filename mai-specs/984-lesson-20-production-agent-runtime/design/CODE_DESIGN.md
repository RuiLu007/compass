# 第 20 课 · 调度与用户事件代码设计

[返回技术方案](TECH_DESIGN.md) · [设计评审](../review/design_review.md)

以下是拟议接口与伪代码，尚未实现。复用 M5 的 `runtime/store.py`、Checkpoint、HITL、Outbox 与公开投影；HTTP 编解码、ORM 和遥测样板省略。数据库操作都经 Agent，Worker 只消费 HTTP。共同锁序为调度锁（需要时）→ Session → Run → job → Step/Attempt → Wait/Action。

## 1. 执行权与内部推进回执

`api/worker_runs.py` 验证专用凭据，解析配置绑定的 workerId；`runtime/run_scheduler.py` 管理执行权及边界。类型由 Agent 的 DTO 与路由生成，Worker 不导入 Agent 源码。

```python
@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str  # 从服务凭据解析；不接受正文替换。
    boot_id: UUID   # 每次启动一个值；同一运行过程持续复用。


@dataclass(frozen=True)
class ExecutionGrant:
    run_id: UUID
    worker_id: str
    boot_id: UUID
    epoch: UUID  # 唯一权威值是agent_run.execution_epoch。
    expires_at: datetime
    checkpoint_no: int  # 无检查点统一为0。


@dataclass(frozen=True)
class AdvanceCommand:
    grant: ExecutionGrant
    expected_checkpoint: int


@dataclass(frozen=True)
class AdvanceAck:
    command_id: UUID
    status: Literal["running", "continue", "parked", "done", "interrupted"]
    checkpoint_no: int
    reason_code: str | None
```

advance 的幂等键由服务端计算 `uuid5(worker_advance_namespace, runId + epoch + expectedCheckpoint)`，规范摘要再绑定 workerId、bootId、同一 Run 和 Checkpoint，忽略心跳会更新的 expiresAt；客户端不能用新的 requestId 反复派发同一边界。

复用 `agent_command`，只扩展内部 `kind=worker_advance`。tenant/workspace/actor_user_id 从原 Run 的授权发起人取得，表示这次执行的委托归属；实际执行者另存于内部 `response` 的 grant 元数据。`response` 为有类型、有大小上限的 `{schemaVersion, grant, expectedCheckpoint, state, ack}`；state 先为 running，再单向收口 committed/interrupted。普通用户命令仍保存不可变原回执；只有此内部 kind 允许这一次状态更新。用户 API 拒绝该 kind，任何命令查重同时校验 kind/摘要，Worker 只得到 ack，不能读取其他用户命令。

原 grant 已释放后，可通过相同 Worker 凭据、boot、epoch 与命令内容读取其已提交回执。Run 已产生新 epoch 时，读取旧回执不授予后继执行权。运行中回执遇到旧 epoch 返回 lease_lost；Agent 恢复把未收口旧命令记 interrupted，后继 grant 使用新的命令 ID。

## 2. 原子受理与公平领取

同一实例内 M5 的 submit/Resume/decision/v1 适配器均通过调度入口，禁止这些路径直接启动执行。当前权限、配置和恢复材料预读在锁外完成，事务内重验版本和归属。

```python
def accept(tx, owner, command, current_access):
    require_current_submit_access(current_access, owner)
    tx.lock_dispatch_admission()
    saved = tx.user_command(owner, command.request_id)
    if saved:
        return same_kind_and_input_or_conflict(saved, "submit", command)
    tx.require_backlog_capacity(owner.tenant_id)  # 包含parked和reclaiming。
    session = tx.lock_or_create_session(owner, command.session_id)
    tx.require_no_nonterminal_run(session)
    run = tx.create_m5_submission(session, owner, command)
    tx.insert_ready_job(run.id, tenant_id=owner.tenant_id)
    # Session/Run/消息/初始投影/Outbox/job/用户命令同事务提交。
    return tx.save_submit_receipt(owner, command, run)


def claim(tx, worker):
    tx.require_agent_owner_and_streams_ready()
    tx.lock_dispatch_admission()
    existing = tx.job_for_worker_slot(worker.worker_id)
    if existing:
        session, run = tx.lock_session_then_run(existing.run_id)
        job = tx.lock_job(run.id)
        if job.worker_boot_id != worker.boot_id:
            return NoWork(reason="previous_process_cleanup")
        if job.status == "leased" and job.lease_expires_at > tx.clock_timestamp():
            return grant_from(run, job)  # claim响应丢失，返回原grant。
        return NoWork(reason="reclaim_pending")
    tx.require_global_execution_capacity()  # leased+reclaiming。
    for candidate in tx.fair_ready_candidates():
        # 按租户last_claimed_at、job.ready_at排序，争用候选不挡住后面的租户。
        locked = tx.try_lock_session_then_run(candidate, skip_locked=True)
        if locked is None:
            continue
        session, run = locked
        job = tx.lock_job(run.id)
        now = tx.clock_timestamp()  # 行锁取得后重新读取实际时钟。
        if not job.is_ready(now) or not tx.has_tenant_capacity(job.tenant_id):
            continue
        if not tx.recovery_and_projection_ready(run):
            continue
        run.execution_epoch = uuid4()
        tx.set_running(run)
        tx.lease(job, worker, expires_at=now + timedelta(seconds=15))
        tx.mark_first_claim_and_increment_count(job, now)
        tx.set_tenant_last_claimed(job.tenant_id, now)
        tx.record_scheduling_boundary(session, run, job, "claimed")
        return grant_from(run, job)
    return NoWork()
```

`record_scheduling_boundary` 更新业务版本，并将安全 run/timeline 对象写入 M5 Outbox；后续派发仍等待关键投影完成。领取失败不更新公平排序。跨表 tenant 归属在创建 job 的事务核对；Worker 槽占用另有部分唯一索引兜底。

## 3. 续租、受理推进与提交

校验执行权时同时验证专用身份、boot、Run epoch、leased 状态及数据库当前时间。心跳不改用户控制版本；取消和回收不允许靠心跳恢复执行权。

```python
def require_live_lease(run, job, worker, grant, now):
    require(worker.worker_id == grant.worker_id == job.worker_id, "lease_lost")
    require(worker.boot_id == grant.boot_id == job.worker_boot_id, "lease_lost")
    require(run.id == grant.run_id, "lease_lost")
    require(job.status == "leased", "lease_lost")
    require(run.execution_epoch == grant.epoch, "lease_lost")
    require(job.lease_expires_at > now, "lease_lost")


def heartbeat(tx, worker, grant):
    session, run = tx.lock_session_then_run(grant.run_id)
    job = tx.lock_job(run.id)
    now = tx.clock_timestamp()
    require_live_lease(run, job, worker, grant, now)
    require(run.status == "running", "lease_lost")
    job.lease_expires_at = now + timedelta(seconds=15)
    return grant_from(run, job)


async def advance(worker, command):
    await authz.recheck_run_execution(command.grant.run_id)
    with database.transaction() as tx:
        session, run = tx.lock_session_then_run(command.grant.run_id)
        job = tx.lock_job(run.id)
        key = internal_advance_key(command)
        saved = tx.worker_command(run.owner, key)
        if saved:
            require_same_worker_grant_and_digest(saved, worker, command)
            if saved.state == "committed":
                return saved.ack  # 已提交旧回执可重读；此分支无新写入。
        require_live_lease(run, job, worker, command.grant, tx.clock_timestamp())
        if saved:
            require(saved.state == "running", "command_interrupted")
            return saved.ack  # 暂停意图不妨碍只读轮询正在收口的原命令。
        require(run.status == "running" and run.control_intent is None, "run_not_runnable")
        require((run.checkpoint_no or 0) == command.expected_checkpoint, "checkpoint_conflict")
        saved = tx.prepare_worker_command(run, worker, command, key)
    # 任务map按Run/command去重；任务归lifespan，HTTP结束不取消。
    # 受理提交后尚未启动即崩溃，旧命令由启动回收处理，保持可诊断。
    try:
        tasks.ensure_boundary_task(saved.id, lambda: execute_one_boundary(worker, command))
    except TaskStartError:
        await scheduler.reclaim_unstarted_command(saved.id)
        return interrupted_ack(saved, "execution_start_failed")
    return saved.ack


def commit_advance(tx, worker, command, outcome):
    session, run = tx.lock_session_then_run(command.grant.run_id)
    job = tx.lock_job(run.id)
    saved = tx.worker_command(run.owner, internal_advance_key(command))
    require_same_worker_grant_and_digest(saved, worker, command)
    if saved.state == "committed":
        return saved.ack
    require_live_lease(run, job, worker, command.grant, tx.clock_timestamp())
    require(run.status == "running", "run_not_runnable")
    require((run.checkpoint_no or 0) == command.expected_checkpoint, "checkpoint_conflict")
    result = tx.commit_m5_boundary(session, run, outcome)
    # Outcome由Agent执行器产生，Worker不能上传模型输出、成本或业务结果。
    if result.resources_confirmed and result.waiting_or_paused:
        tx.park_and_clear_epoch(run, job)
    elif result.resources_confirmed and result.terminal:
        tx.finish_job_and_clear_epoch(run, job)
    elif result.resources_unconfirmed:
        tx.revoke_and_reclaim(run, job, reason="cleanup_unconfirmed")
    if result.scheduling_changed:
        # 扩充本事务刚创建、尚未发布的同一Outbox，保留一个run_version。
        tx.append_scheduling_to_boundary(result.outbox_id, run, job)
    return tx.commit_worker_ack(saved, result)
```

`execute_one_boundary` 复用 M5 持久执行器，先在锁外等待前序关键投影；每次模型/工具/动作派发前都经过调度门禁，内部子调用记录原 Step/Attempt。继续、等待或完成的回执与 M5 业务结果、Checkpoint、调度变化和 Outbox 同事务收口。公开调度变化同时记录领取及确认释放，供占用区间统计。对外工具和 Redis 操作全在业务锁外；等待投影完成后才允许下一命令的实际派发。已提交命令回执的只读返回不等待 Redis。

取消或过期后的已返回结果走 Agent 清理/效果核对入口：只按原 Attempt/Action ID 补记结局、费用和清理回执，不覆盖新的 Checkpoint、成功消息或执行权。旧执行者无法调用它来派发新动作。已成功提交的效果保留；未知结果沿用 M5 的保守预算占额。

## 4. 控制接续、回收和 Worker 循环

```python
def resume_or_approve(tx, owner, command, current_access):
    # 可能为旧M5 Run补job，从事务起点取调度锁。
    tx.lock_dispatch_admission()
    session, run = tx.lock_session_then_run(command.run_id)
    job = tx.lock_job_if_present(run.id)
    previous = tx.user_command(owner, command.request_id)
    if previous:
        return same_kind_and_input_or_conflict(previous, command.kind, command)
    tx.validate_m5_control(session, run, command, current_access)
    tx.require_parked_or_legacy_job_for_new_resume(job, command)
    if job is None:
        tx.require_backlog_capacity(session.tenant_id)
        job = tx.insert_parked_job(run.id, session.tenant_id)
    ack = tx.apply_m5_decision_without_starting_executor(session, run, command)
    if ack.can_resume:
        tx.require_current_resources_settled(run)
        tx.clear_satisfied_control_intent(run)
        run.reason_code = "queued"
        tx.make_ready(job)  # 保留waiting/paused，claim时才转running和授予epoch。
    elif ack.terminal:
        tx.finish_job_and_clear_epoch(run, job)
    tx.record_control_outbox(session, run, ack)
    return tx.save_user_command_receipt(owner, command, ack)


async def reclaim(run_id):
    with database.transaction() as tx:
        session, run = tx.lock_session_then_run(run_id)
        job = tx.lock_job(run_id)
        now = tx.clock_timestamp()
        if job.status == "leased" and job.lease_expires_at <= now:
            tx.revoke_and_reclaim(run, job, reason="lease_expired")
        if job.status != "reclaiming":
            return
    # 撤权先提交；执行句柄、旧流回调与效果核对在锁外完成。
    receipt = await execution.stop_and_reconcile_original_attempts(run_id)
    with database.transaction() as tx:
        session, run = tx.lock_session_then_run(run_id)
        job = tx.lock_job(run_id)
        require(job.status == "reclaiming", "recovery_conflict")
        tx.record_cleanup_receipt(run, receipt)
        if not receipt.resources_confirmed:
            tx.mark_recovery_blocked(run, receipt.reason)  # 仍占槽。
        elif run.status == "cancelling":
            tx.finish_canceled_if_effects_reconciled(run, job, receipt)
        elif run.control_intent == "pause" or tx.has_pending_wait(run):
            tx.park_and_clear_epoch(run, job)
        elif tx.safe_checkpoint_and_effects_confirmed(run, receipt):
            tx.interrupt_unfinished_worker_commands(run)
            tx.make_ready_and_clear_lease(job)
        else:
            tx.park_recovery_blocked(run, job, receipt.reason)
        tx.record_recovery_outbox(session, run, job)
```

取消事务复用 Session/Run/job 的锁顺序，先保存 cancelling 并撤销新派发许可。未领取且无资源的任务直接取消；在途任务转 reclaiming，清理/效果核对后才 done。批准与取消、claim 与取消按同一行锁裁决。暂停只在安全边界停下；已接受的当前步骤可以收口，后继派发须检查 pause 意图。

Worker 每个进程只有一个配置槽。启动后生成 bootId；claim 无任务按 1 秒上界退避。拿到 grant 后并行运行心跳和 advance 轮询：同一边界一直使用相同命令；continue 才换 expectedCheckpoint；parked/done 结束本轮。心跳确认使用“发送时本地单调时钟 + TTL”作为保守截止，HTTP 超时按原命令重试；lease_lost、截止或取消意图立即停止新 advance。SIGTERM 停止 claim、请求 release，当前任务在有界清理内续租；超时退出由 Agent 继续回收。

Agent 启动恢复先持有 M5 单执行所有者锁并补齐流和投影，再对旧 leased/reclaiming 撤权核对。持久命令 running 但没有本地任务的情况通过这个恢复入口处理。用户 waiting/paused、未知远端效果及显式暂停保留原控制语义。

## 5. M5 公共协议扩展与最终回答

保留 M5 `AgentResponse/AgentPacket/AgentEvent` 唯一联合，沿用 `runtime/public_projection.py` 和 `runtime/run_events.py`，不再新增第二个投影服务。调度事件转换为有类型 Timeline 对象，私有 Worker 身份留在内部回执。

```python
def scheduling_view(run, job, transition_id, reason, occurred_at):
    return ViewUpsert(
        object_id=f"timeline:{transition_id}", revision=1, kind="timeline",
        value=PublicTimelineEntry(
            run_id=run.id, phase="scheduling", status=job.status,
            reason_code=public_reason(reason), claim_count=job.claim_count,
            occurred_at=occurred_at,
        ),
    )


def filter_public_response(body, access):
    require_owned_session_and_runs(body, access)
    packets = []
    for packet in body.packets:
        # snapshot/delta调用同一函数，保留身份、seq和revision。
        if packet.event.type == "view.upsert":
            event = allowlisted_view_or_safe_placeholder(packet.event, access)
        else:
            event = require_visible_message_or_run(packet.event, access)
        packets.append(packet.with_event(event))
    return body.with_packets(packets)
```

`allowlisted_view_or_safe_placeholder` 复用原对象类型，只替换该 DTO 支持的安全标题/状态并去掉全部受限详情；对象本身不允许最低展示时拒绝对应 Run 读取。Message 正文当前权限不足时同样拒绝，避免删掉某个 message.delta 破坏后续 revision。不存在新的 cursor-only 包；整帧最后一个 seq 仍与外层 cursor 一致。授权范围变更时通过重新加载 Message 清除旧详情，所有预览均按同一白名单生成。

```typescript
function applyScheduledResponse(state: SessionView, response: unknown): SessionView {
  const next = applyAgentResponse(state, response); // M5：整帧校验与原子应用。
  for (const run of next.runs) {
    const answer = next.completedAssistantMessage(run.id);
    const completed = run.status === "completed" && answer !== undefined;
    if (completed && !next.answerTransitionSeen.has(run.id)) {
      next.answerTransitionSeen.add(run.id);
      // 用户显式展开优先，重复快照与旧事件不改变这一选择。
      if (!next.userProcessPreference.has(run.id)) next.collapseProcess(run.id);
    }
  }
  return next;
}
```

一次成功边界发布完整 message.upsert 和 completed run.upsert。两者完成 SQL 投影后才可见；工具旁白只影响 view 对象。失败/取消的 incomplete 消息不触发成功切换。Stream ID 比较、重连、409 历史重载与 410 活跃流丢失直接使用 M5 逻辑。

## 6. 必要验证

实施时验证：重复 submit/claim/advance、等锁跨越 TTL、过期心跳、boot 更换、所有恢复入口的配额、锁序争用、取消与效果写入、旧 delta 回调、真实双 Worker 接管、Agent 重启、Redis 过期后的完整 Message 与回答折叠。并核对每个租户占用、未知成本和本机恢复时间。这里的片段是设计示意，运行结果待实施阶段获取。
