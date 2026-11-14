# 第 19 课 · 持久化与双接口同协议代码设计

[返回主方案](TECH_DESIGN.md) · [Redis 方案](REDIS_DESIGN.md) · [数据合同](DATA_DESIGN.md) · [HITL 方案](HITL_DESIGN.md)

以下是拟议 Python/TypeScript 评审伪代码，尚未实现或运行。省略 ORM 映射、依赖注入和错误响应样板；关键事务、身份、幂等、派发、回放和失败分支展开。事务工厂退出成功后才可发送回执。任何外部模型、工具或 Web 权限请求都在数据库事务之外。

## 1. 原子受理与提交后发布

拟新增 `runtime/store.py`，修改 M4 目标 `runtime/service.py`。

```python
def submit(command, actor):
    require_current_scope(actor)
    digest = canonical_digest(command)
    with database.transaction() as tx:
        tx.lock_submission_key(actor.scope, actor.id, command.request_id)
        previous = tx.command(actor.scope, actor.id, command.request_id)
        if previous:
            require_equal(previous.request_sha256, digest, "request_conflict")
            return previous.response
        require_cached_runtime_readiness()  # 仅读取本进程就绪标记；原回执不依赖Redis。
        session = tx.lock_owned_session(command.session_id, actor) if command.session_id else tx.create_session(actor, command.question)
        require_no_active_run_and_previous_projection_complete(tx, session.id)
        run = tx.create_run(session, command, projected_seq="0-0")
        message = tx.create_user_message(run, command.question)
        assistant = tx.create_empty_assistant_message(run)
        tx.bootstrap_public_view(run)  # 初始created状态，与原问题一起持久保存。
        packets = [run_upsert(run), message_upsert(message), message_upsert(assistant)]
        tx.insert_outbox(run, command.request_id, digest, packets)
        ack = accepted(session.id, run.id)
        tx.record_command(actor, command, digest, ack, session.id, run.id)
    return ack  # 首批Outbox投影后，应用级任务才允许派发模型。


def commit_boundary(run_id, epoch, previous_checkpoint, operation_id, outcome):
    session_id = database.session_of_run(run_id)
    with database.transaction() as tx:
        session = tx.lock_session(session_id)
        run = tx.lock_run(run_id)
        old = tx.operation_outbox(run.id, operation_id)
        if old:
            require_equal(old.operation_sha256, outcome.digest, "operation_conflict")
            return old.boundary_receipt
        if run.is_terminal:
            return tx.require_same_terminal_outcome_and_receipt(run, outcome)
        require_epoch(run, epoch)
        require_checkpoint(run, previous_checkpoint)
        if run.status == "cancelling":
            return tx.commit_cancelling_result_and_outbox(session, run, outcome)
        require_status(run, "running")
        tx.require_open_attempt(run.id, outcome.attempt_id)
        for artifact in outcome.artifacts:
            require_size_digest_and_scope(artifact, run)
            tx.insert_artifact_or_verify_identical(artifact)
        tx.require_checkpoint_references(outcome.checkpoint, run)
        tx.accept_attempt_and_step(outcome)
        tx.insert_private_context_messages(outcome.context_messages)
        if outcome.wait:
            tx.insert_wait_and_release_epoch(run, outcome.wait)
        elif run.control_intent == "pause":
            tx.pause_and_release_epoch(run)
        elif outcome.terminal:
            tx.finish_run(run, outcome.terminal)
        tx.bump_versions(session, run)
        checkpoint = tx.prepare_checkpoint(run, outcome.checkpoint)
        packets = tx.encode_boundary_packets(run, outcome, checkpoint)
        outbox = tx.insert_outbox(run, operation_id, outcome.digest, packets)
        tx.insert_checkpoint(checkpoint, boundary_outbox_id=outbox.id)
    publisher.wake(run_id)
    return boundary_receipt(checkpoint.number, outbox.id)


async def publish_delta(run_id, packet):
    # 所有发布走同一Run通道；边界前先排完已有delta，边界后等待投影。
    async with channels.for_run(run_id):
        await publisher.require_no_pending_boundary(run_id)
        require_current_owner()
        seq = await redis_stream.append_confirmed_once(run_id, packet)
        confirmed_tail.advance(run_id, seq)
        projector.wake(run_id)  # 500ms或64KiB合并，终止时强制刷新。
        readers.notify(run_id)


async def flush_boundary(run_id, outbox_id):
    async with channels.for_run(run_id):
        # 按run_version补齐此前未投递批次，不能越过早先的关键变化。
        for batch in await store.pending_outboxes_through(run_id, outbox_id):
            ids = []
            for index, packet in enumerate(batch.packets):
                seq = await redis_stream.append_confirmed_once(
                    run_id, packet, outbox_id=batch.id,
                    batch_index=index, batch_size=len(batch.packets),
                )
                ids.append(seq)
            confirmed_tail.advance(run_id, ids[-1])
            # 这里将整批内容、projected_seq、Outbox回执同事务提交。
            await projector.flush_through(run_id, ids[-1])
            await store.require_outbox_delivered(batch.id)
            readers.notify(run_id)
    # 调用者等待本函数完成后才允许依赖本边界的外部派发。
```

append_confirmed_once 生成包 ID 一次并复用，写命令关闭盲重试。写入结果不明时先 XRANGE 查询有界 Run 流的相同 packetId，核对内容后确认或补写。Redis AOF always 成功确认后才推进进程内 confirmed_tail；启动核对持久流后先重建该位置，再补发和补投影。存储不可用时不回退为纯内存输出。

普通 delta 仅写 Redis。公开消息/public_view 由 Projector 统一更新，私有上下文和原执行结果由 Runtime Store 写业务记录。Projector 按游标从 Redis 读取并合并，事务锁定 Run 后再次检查原游标，以免重复应用；关键批次不拆开提交。Cancelling 分支继续保存已到达输出、效果和清理回执，禁止后继派发。

## 2. 两个接口与同一响应处理器

拟新增 `runtime/message_reader.py`、`runtime/run_events.py`、`runtime/public_projection.py`、`runtime/redis_stream.py` 和 `runtime/outbox_publisher.py`。响应外层 AgentResponse 与 AgentPacket 唯一维护于主方案第 3.2 节，实际实现由 schemas.py 生成；此处展开共享业务联合和读取逻辑。

```python
def messages(session_id, page_cursor, run_id, actor):
    authz.require_session_read(actor, session_id)
    with database.read_snapshot() as tx:
        runs, next_page = tx.page_owned_runs(session_id, actor, page_cursor, run_id)
        packets, cursors = [], []
        for run in runs:
            saved_messages = tx.public_messages(run.id)
            view = tx.public_view(run.id)
            # 使用已投影Run头，不能混入较新的业务Run.status。
            packets.extend(encode_complete_upserts(view.run, saved_messages, view.objects))
            cursors.append({"runId": run.id, "seq": run.projected_seq})
        body = AgentResponse(
            sessionId=session_id, mode="snapshot", packets=packets,
            cursors=cursors, page={"nextCursor": next_page},
        )
        validate_response(body)
    return json_response(body, cache_control="no-store")


async def project_prefix(run_id, target=None):
    # 独立有界任务；正常每500ms或64KiB，关键边界指定target强制提交。
    base = await store.read_projection(run_id)
    rows = await redis_stream.read_confirmed_after(run_id, base.seq, through=target)
    rows = complete_boundary_prefix(rows)  # 不保留半个Outbox批次到SQL。
    if not rows:
        return
    next_view = reduce_public_rows(base, rows)
    with database.transaction() as tx:
        run = tx.lock_run(run_id)
        require(run.projected_seq == base.seq, "projection_retry")
        tx.save_public_messages_and_view(run, next_view)
        tx.set_projected_seq(run, rows[-1].id)
        tx.mark_complete_outboxes_delivered(rows)
    # 仅终态、无待投递Outbox且投影到流尾时可以设置整键TTL。
    await redis_stream.expire_if_fully_projected_terminal(run_id, seconds=3600)


async def run_events(run_id, after, actor):
    await authz.require_run_read(actor, run_id)
    require_stream_id(after)
    # 头部发出前验证归属、游标、流存在性；失效409、丢失410、依赖故障503。
    await redis_stream.validate_resume(run_id, after)
    cursor = after
    while transport_budget.remaining:
        await authz.require_run_read(actor, run_id, max_age_s=5)
        run = await store.read_run_and_projection(run_id)
        rows = await redis_stream.read_confirmed_after(run_id, cursor, limit=16)
        # 普通包已获AOF确认；关键包仅在整批已投影后可见，不能越过该批。
        rows = visible_prefix(rows, projected_seq=run.projected_seq)
        rows = take_encodable_prefix(rows, frame_bytes=256 * 1024, max_items=16)
        if rows:
            require_strictly_increasing_ids(rows, cursor)  # Redis ID允许间隔。
            body = AgentResponse(
                sessionId=run.session_id, mode="delta",
                packets=[packet_from_stream_entry(row) for row in rows],
                cursors=[{"runId": run_id, "seq": rows[-1].id}], page=None,
            )
            validate_response(body)
            yield encode_sse(id=f"{run_id}:{rows[-1].id}", data=body)
            cursor = rows[-1].id
        if run.public_terminal and run.all_outboxes_delivered and cursor == run.projected_seq:
            return
        yield sse_comment("keepalive")
        await readers.wait_or_poll(run_id, timeout_s=1)
        await redis_stream.require_retained_stream(run_id, cursor)
    # 传输结束不改变业务状态；连接中途错误断流，下次握手返回明确错误码。
```

下面的 value 类型复用对应的公开 DTO；审批、草稿和产物字段沿 DATA_DESIGN/HITL_DESIGN 展开。每个对象的稳定 ID 必须与 DTO 内部身份匹配，私有字段禁止进入该联合。

```typescript
type MessageSnapshot = {
  messageId: string;
  ordinal: number;
  role: "user" | "assistant";
  revision: number;
  status: "streaming" | "completed" | "incomplete";
  content: { text: string; artifactIds: string[] };
};

type PublicViewObject = {
  objectId: string;
  revision: number;
} & (
  | { kind: "execution.node"; value: ExecutionNodeSnapshot }
  | { kind: "plan"; value: PublicPlanSnapshot }
  | { kind: "narration"; value: PublicNarration }
  | { kind: "workflow"; value: PublicWorkflowSnapshot }
  | { kind: "approval"; value: PublicApprovalSnapshot }
  | { kind: "artifact"; value: PublicArtifactMetadata }
  | { kind: "timeline"; value: PublicTimelineEntry }
);

type AgentEvent =
  | { type: "message.upsert"; payload: MessageSnapshot }
  | {
      type: "message.delta";
      payload: { messageId: string; revision: number; text: string };
    }
  | {
      type: "run.upsert";
      payload: {
        ordinal: number;
        version: number;
        status: RunStatus;
        reasonCode: SafeReason | null;
        recoveryStatus: RecoveryStatus;
        cleanupStatus: CleanupStatus;
      };
    }
  | { type: "view.upsert"; payload: PublicViewObject };
```

upsert 首次出现时创建对象，已有对象按 revision/version 替换；相同版本同内容为幂等，版本冲突且内容不同报协议错误，旧版本忽略。message.delta 只追加到已存在消息的下一个 revision。正文完成后用完整 message.upsert 收口为 completed/incomplete；终态 run.upsert 与完整结果形成关键 Outbox；投影提交完成后再从流输出。历史通过相同 upsert 直接恢复完整内容，保留节点父子关系、公开旁白顺序和实际展示的 Timeline。

Agent 将既有 routing/step/tool/extension 过程转换为上述公开对象更新，通过统一 Projector 维护 public_view；v1 适配器继续编码原合同。转换规则只有一份业务对象定义；两个 v2 接口都使用它，新增或修改展示字段时共同升级 Schema。

```typescript
function compareStreamId(a: string, b: string): number {
  const [am, as] = parseStreamId(a); // 校验格式后转为两个bigint。
  const [bm, bs] = parseStreamId(b);
  return am === bm ? (as === bs ? 0 : as < bs ? -1 : 1) : am < bm ? -1 : 1;
}

function applyAgentResponse(state: SessionView, input: unknown): SessionView {
  const body = validateAgentResponse(input);
  require(body.sessionId === state.sessionId, "session_mismatch");
  let next = cloneViewForAtomicApply(state);
  if (body.mode === "snapshot") {
    for (const cursor of body.cursors) {
      const last = next.runSeq[cursor.runId] ?? "0-0";
      if (compareStreamId(cursor.seq, last) < 0) continue;
      next = clearRunProjection(next, cursor.runId);
      for (const packet of body.packets.filter((p) => p.runId === cursor.runId)) {
        require(packet.seq === null, "snapshot_cursor_invalid");
        next = reduceAgentPacket(next, packet);
      }
      next.runSeq[cursor.runId] = cursor.seq;
    }
  } else {
    require(body.cursors.length === 1 && body.page === null, "stream_envelope_invalid");
    requireStrictPacketOrder(body.packets); // 帧内严格递增，序号无需相邻。
    for (const packet of body.packets) {
      require(packet.runId === body.cursors[0].runId, "run_mismatch");
      require(packet.seq !== null, "delta_cursor_missing");
      const last = next.runSeq[packet.runId] ?? "0-0";
      if (compareStreamId(packet.seq, last) <= 0) continue;
      next = reduceAgentPacket(next, packet); // delta按messageId/revision防止重复追加。
      next.runSeq[packet.runId] = packet.seq;
    }
    requireEnvelopeCursorMatchesPackets(body);
  }
  return next; // 整帧成功后才发布状态和游标。
}

async function loadMessages(sessionId: string, options: MessageReadOptions) {
  const response = await getMessages(sessionId, options);
  updateView((state) => applyAgentResponse(state, response));
  return response;
}

async function followRun(runId: string, signal: AbortSignal) {
  while (!signal.aborted && !isTerminal(currentView(), runId)) {
    const response = await getRunEvents(runId, lastAppliedSeq(runId), signal);
    if (response.status === 409 && (await isHistoryReloadRequired(response))) {
      await loadMessages(currentSessionId(), { runId, signal });
      continue;
    }
    if (response.status === 410 && (await isStreamDataLost(response))) {
      await loadMessages(currentSessionId(), { runId, signal });
      showRecoveryBlocked(runId); // 保留更近的本地内容，停止自动重连。
      return;
    }
    if (response.status === 503) {
      showSyncUnavailable(runId);
      await reconnectBackoff(signal);
      continue;
    }
    requireSuccessfulResponse(response);
    for await (const body of parseSseData(response, signal)) {
      updateView((state) => applyAgentResponse(state, body));
    }
    if (!isTerminal(currentView(), runId)) await reconnectBackoff(signal);
  }
}
```

首次打开先调用 loadMessages；历史分页继续使用此函数；已装载的非终态 Run 再调用 followRun。首次 submit 得到新 Run 后可从 `0-0` 接收其创建与后续增量。业务状态只经 applyAgentResponse 更新，JSON/SSE 读取代码独立处理传输。结构错误停止并提示重载；权限错误退出相应会话视图；两类错误不进入无限重试。

## 3. 真实模型 Attempt 的写入接缝

修改 `llm/executor.py`，通过窄 `AttemptRecorder` 端口连接 `models/repository.py`，LLM 适配器不导入 Runtime Store。无持久 Run 的模型测试同样记录 model_attempt，内容采集沿原测试政策。

```python
async def invoke_attempt(call, attempt_no, request, recorder, provider):
    saved = await recorder.prepare(
        call_id=call.id, number=attempt_no,
        provider=request.spec, reservation=request.budget_reservation,
    )
    if saved.terminal:
        return await recorder.read_result(saved.id)
    if saved.dispatched_at is not None:
        raise RecoveryRequired("upstream_outcome_unknown")
    # 先持久派发意图，派发窗口崩溃也保守占额。
    await recorder.mark_dispatched(saved.id)
    try:
        result = await provider.invoke(request)
    except BaseException:
        await recorder.mark_unknown_or_leave_dispatched(saved.id)
        raise
    await recorder.finish_attempt(saved.id, result)
    # result未持久成功时调用方不可提交成功终态。
    return result
```

运行中的 retry/fallback 先执行原 policy 判定，再产生新 model_attempt；上游结果未知、已暴露内容或清理未确认均沿用原拒绝重试语义。模型 token 回调经 Runtime 持久 delta sink 写公开事件；私有协议 continuation 通过单独加密端口保存，不进入 sink。

## 4. Workflow 日志恢复

修改 M4 目标 `workflows/script.py` 与真实 `workflow-runner/run.mjs`。纯 JS 从原入口重新计算；宿主边界稳定记录，执行过的外部能力取原结果。

```python
async def handle_host_call(workflow, frame, journal, dispatcher):
    key = (workflow.step_id, frame.call_ordinal)
    digest = canonical_digest((frame.kind, frame.name, frame.input))
    saved = await journal.lookup(key)
    if saved:
        require(saved.input_sha256 == digest, "workflow_replay_mismatch")
        if saved.accepted_attempt_id:
            # 按原delivery_no排队交回QuickJS，不重复发布旧过程节点。
            return await journal.replay_delivery(saved)
        if saved.outcome_unknown:
            raise RecoveryRequired("execution_unknown")
        require(saved.safe_to_resume, "resume_blocked")
    else:
        saved = await journal.prepare_host_step(key, digest, frame)
    result = await dispatcher.execute_or_resume(saved)
    # 完整结果、接受的Attempt、delivery_no与事件先提交。
    delivered = await journal.commit_result(saved, result)
    return await journal.deliver_in_recorded_order(delivered)
```

父流程冻结 logicalTime/randomSeed，QuickJS 的 Date/随机入口使用确定性实现，其他外部 IO 全走桥。所有宿主返回顺序，包括并行结果，分配 delivery_no 后再交付；恢复加载原 journal，依次释放对应 Promise，并泵送相同 microtask 顺序。重放已接受结果时不再增加模型/工具预算，不重复节点事件。每个完成子 Step 可独立恢复，父 Join 的完成结果另做 Checkpoint。

实施先验证原 parallel 案例、条件分支、随机/时间与 Promise 竞争：脚本相同却产生不同宿主调用序列时必须报 mismatch。资源耗尽、脚本版本变化或日志缺失同样阻止接续。保持完整历史可读，不回退到无记录地重新调用全部子 Agent。

## 5. HITL 事务与效果核对

拟新增 `runtime/hitl.py`。外部权限请求在事务前完成，资源状态在事务内再次核验。

```python
def record_decision(command, actor, current_access):
    require_current_approval_access(actor, current_access, command.decision)
    with database.transaction() as tx:
        session, run = tx.lock_session_then_run(command.run_id)
        previous = tx.command_for(actor, command.request_id)
        if previous:
            return same_input_or_conflict(previous, command)
        wait = tx.lock_wait(command.wait_id)
        require_same_run_proposal_and_digest(run, wait, command)
        if wait.decision_id == command.decision_id:
            return same_decision_or_conflict(wait, command)
        require_version(run, command.expected_version)
        require_status(run, "waiting")
        require_status(wait, "pending")
        if tx.now() >= wait.expires_at:
            # 提交过期状态，再由API返回409；不能抛异常回滚到pending。
            ack = tx.expire_wait_and_record_result(session, run, wait)
            tx.record_command_receipt(actor, command, ack)
            return ack
        if command.decision == "reject":
            ack = tx.close_action_branch_with_saved_analysis(session, run, wait, command)
            tx.record_command_receipt(actor, command, ack)
            return ack
        tx.approve_wait(wait, command, actor)
        tx.create_action_once(wait, stable_action_id(wait))
        epoch = tx.set_running_with_new_epoch(run)
        ack = tx.record_decision_state_and_outbox(session, run, wait, epoch)
        tx.record_command_receipt(actor, command, ack)
    return ack


async def continue_approved_action(run_id, approval, epoch):
    previous = await adapter.lookup(approval.action_id)
    if previous.is_applied:
        return await store.record_receipt_without_new_effect(run_id, previous)
    if previous.is_unknown:
        return await store.wait_for_reconciliation(run_id, approval.action_id)
    await publisher.require_decision_projected(run_id, approval.decision_id)
    require_runtime_dependencies_ready()
    access = await web_authz.recheck_approval_and_execution(approval)
    if not access.allowed:
        return await store.record_authorization_denied(run_id)
    # 适配器独立短事务按Session→Run→Wait→Action锁序，检查epoch/取消/摘要。
    applied = await adapter.apply_if_current(run_id, epoch, access, approval)
    if applied.is_unknown:
        return await store.wait_for_reconciliation(run_id, approval.action_id)
    if not applied.is_applied:
        return await store.record_action_denied(run_id, applied.code)
    return await store.record_receipt_without_new_effect(run_id, applied)
```

批准、拒绝、到期和取消的事务辅助函数均同事务登记对应 Outbox；受理回执不直接覆盖前端展示状态，原 Run 任务等待决定的关键投影完成再派发。

同 actionId 的效果写入有唯一约束，冲突读取原效果并验证摘要；取消先提交时禁止新增效果。核对已有回执允许在撤权或 Cancelling 后内部完成，但不得再派发后续动作。故障测试应在效果 commit 后、Run 回执前真实终止进程。

## 6. 评审与实施验证

重点核对：受理幂等键在无 Session 时的并发；Session/Run 锁序；终态与最后回答的投影提交门禁；delta 的 Redis 持久确认；Outbox 部分发布恢复；Message投影游标与Run续流无遗漏；LLM 派发窗口；QuickJS 结果交付顺序；审批过期提交后返回错误；取消和效果写入的同锁裁决。这里只交付设计伪代码，语法/运行与数据库行为验证在实施阶段执行。
