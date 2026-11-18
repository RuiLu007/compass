# 第 21 课代码设计 · 供评审

[返回技术方案](TECH_DESIGN.md)

片段使用 Python 风格伪代码表达事务和运行时约束。SQLAlchemy 映射、HTTP DTO 与外围样板留给实现。标记为拟新增的符号需要实现及测试。

## 1. 受理时读取发布指针

**接入点** `runtime/store.py::RuntimeStore.submit`。双角色 Scheduler 基线使用 `submit_in_transaction`。新增 `promptops/repository.py::select_binding`。Store 的命令去重、授权范围和入队顺序沿用现有契约。

```python
@dataclass(frozen=True)
class PromptBinding:
    bundle_id: UUID
    bundle_sha256: str
    scope_key: str
    generation: int
    lane: Literal["stable", "canary"]
    audience: str  # 已通过 Web 的成员与作用域检查


def select_binding(tx, verified_scope, actor_id, audience):
    scopes = ordered_scopes(verified_scope)  # workspace → tenant → global
    heads = load_heads_in_one_select(tx, scopes)  # 读取主库的一致语句视图
    stable_only = False
    for scope in scopes:
        head = heads.get(scope.key)
        if head is None:
            continue
        if not stable_only and head.canary_bundle_id:
            if bucket_0_to_9999(scope.key, actor_id) < head.rollout_bps:
                return checked_binding(tx, head, head.canary_bundle_id, "canary", audience)
            # 首次局部灰度未命中时走父级稳定链。
            stable_only = head.stable_bundle_id is None
        if head.stable_bundle_id:
            return checked_binding(tx, head, head.stable_bundle_id, "stable", audience)
    raise PromptConfigUnavailable()


def submit_new_run(tx, command, trusted, audience):
    prior = existing_run_command(tx, trusted, command.request_id, command)
    if prior:
        return prior.receipt
    require_capacity_and_lock_session(tx, trusted, command)
    binding = select_binding(tx, trusted.scope, trusted.actor_user_id, audience)
    run = insert_run(tx, command, trusted, prompt_binding=binding)
    enqueue_job_and_write_receipt(tx, run, command.request_id)
    return run.receipt
```

`checked_binding` 校验选中 Bundle 归属、只读生命周期、结构兼容与摘要指纹元数据。执行前校验完整内容摘要；配置损坏时拒绝受理或受阻，保留明确诊断。若工作空间有稳定版，该空间优先使用其固定配置。等待、执行、恢复和幂等重试路径只接受 Run 自己的绑定。发布变更与受理并发时，在线读语句观察到的版本可被完整重建。

## 2. 编译与发布

**拟新增** `promptops/compiler.py` 与 `promptops/service.py`。编译输出整体候选，事务只切换发布指针。源材料与当前父级 ID 都参与候选摘要。

```python
def compile_draft(draft, parent, policy, catalog):
    require_allowed_parent(draft.scope, parent)
    fragments = merge_four_layers(parent, draft.document, policy.allowed_slots)
    check_unique_slots_by_layer(fragments)
    check_variable_types_and_references(fragments, policy.variable_contracts)
    check_protected_slots_unchanged(fragments, policy)
    ordered = topo_sort_with_layer_then_key(fragments)
    mapping = validate_scene_profiles(draft.document, catalog, ordered)
    return seal_bundle(draft.scope, draft.revision, parent, catalog, policy, ordered, mapping)


def publish(tx, cmd, actor, auth):
    permit_action(auth, actor, cmd.action, cmd.scope)
    old = find_operation(tx, actor.id, cmd.scope, cmd.request_id)
    if old:
        require_request_digest(old, cmd)
        return operation_receipt(old)

    # 首次局部发布也需先创建可锁定的空 Head。
    head = ensure_and_lock_head(tx, cmd.scope)
    # 第二个同请求事务可能在等待锁时已由另一实例完成。
    old = find_operation(tx, actor.id, cmd.scope, cmd.request_id)
    if old:
        require_request_digest(old, cmd)
        return operation_receipt(old)
    if cmd.expected_generation != head.generation:
        raise ReleaseConflict(head.generation)

    if cmd.action in {"publish", "promote", "change_canary"}:
        candidate = read_approved_candidate_for_update(tx, cmd.scope, cmd.bundle_id)
        require_current_evaluation(tx, candidate, head)
        require_two_ordered_approvers(tx, candidate)
        require_compatible_scope_and_policy(candidate, cmd.scope)
        updated = switch_candidate(head, candidate.id, cmd.rollout_bps)
    elif cmd.action == "rollback":
        target = read_previously_activated_bundle(tx, cmd.scope, cmd.bundle_id)
        require_compatible_scope_and_policy(target, cmd.scope)
        updated = set_stable_and_clear_canary(head, target.id)
    elif cmd.action == "withdraw_canary":
        updated = clear_canary(head)
    elif cmd.action == "inherit" and cmd.scope.is_overlay:
        updated = clear_scope_selection(head)
    else:
        raise UnsupportedReleaseAction()

    updated.generation = head.generation + 1
    store_head_and_append_operation(tx, actor, cmd, previous=head, updated=updated)
    return release_receipt(updated)
```

- 一级复核必须匹配当前候选作用域。二级复核与发布由平台授权。两位审批人互异，作者不得审批。同一轮审批的 `candidate_id / revision / evaluation_id` 必须一致。在批准至过渡状态时用同一草稿锁串行化，激活同时核对草稿及发布指针。需要两类锁的操作统一先锁 Head 再锁草稿。
- `require_current_evaluation` 验证回归结果所用对照是该作用域当时应使用的稳定版本，局部无 stable 时使用父级稳定版本。审批和首次激活若发现当前稳定对照已变，则重新评估。扩大同轮灰度时校验审批仍有效且对照链符合该轮的绑定。已核验父级静态版本留存在候选中，显式同步父级才改变继承内容。
- 冻结内容、状态和指针校验由 Agent 实现。可编辑的 Prompt 文字不会改变已授权的工具集合、数据库权限、输出验证器或者范围查询条件。

## 3. 阶段推进与模型输入

**现有** `RunService.run` 负责路由与工具执行。**拟新增** `context_runtime/assembler.py` 的组装以及 `ExecutionJournal` 的加密收据写读，在既有逻辑模型 Step 处接入。

```python
@dataclass(frozen=True)
class ContextItem:
    kind: ContextKind
    source: SourceKind
    source_ref: str
    source_version: str
    access_ref: AccessReference
    visibility: Visibility
    trust: Literal["trusted_instruction", "data"]
    confidence: float | None
    expires_at: datetime | None
    estimated_tokens: int
    required: bool
    priority: int
    group_key: str  # 成对的工具请求和结果共用预算单元
    value: object = field(repr=False)


def next_phase(facts, profile):
    if facts.awaiting_tool_receipt or facts.awaiting_decision or facts.effect_unknown:
        return HoldCurrentPhase()
    if profile.plan_required and not facts.has_verified_plan:
        return Phase.PLAN
    if facts.has_pending_data_requirement:
        return Phase.QUERY
    if facts.has_unverified_required_observation:
        return Phase.ANALYZE
    return Phase.GENERATE


def model_frame(state, new_phase, system_text, approved_data):
    if new_phase != state.phase:
        require_all_requested_tools_settled(state)
        return new_frame(
            phase=new_phase,
            system_text=system_text,
            user_task=state.user_task,
            approved_history=approved_data.conversation,
            observations_as_data=approved_data.verified_observations,
            continuation=None,
        )
    if state.continuation:
        # 供应商令牌校验已封存的消息前缀和允许的 tool call IDs。
        return retain_prefix_and_attach_tool_pairs(state)
    return assemble_fresh_frame(state, system_text, approved_data)


async def execute_managed_model_step(journal, step_key, run, facts, live_access):
    bundle = open_pinned_bundle(run.prompt_binding)
    existing = await journal.load_model_context(step_key)
    if existing:
        # 已接受结果可在当前权限下本地重放，未发送的请求额外复核有效期。
        await live_access.check_references(existing.access_refs)
        if existing.needs_dispatch:
            require_fresh_before_dispatch(existing.required_items)
        prepared = await journal.restore_prepared_request(existing)
    else:
        profile, phase = choose_profile_and_phase(bundle, run, facts)
        candidates = await collect_context(run, facts, phase)
        authorized = await live_access.filter_and_check_required(candidates)
        permitted_tools = frozen_tools(run) & await live_access.current_tool_ids()
        receipt = assemble(bundle, profile, phase, authorized, permitted_tools)
        # 同事务保存 input digest、私有变量/工具元数据、加密消息引用和安全摘要。
        prepared = await journal.prepare_model_context(step_key, receipt)
    return await invoke_with_existing_journal_and_dispatch_gate(prepared, live_access)
```

- `journal.load_model_context` 与写入先确认 Run 和 epoch，然后按 `run_id + node_key` 读取并校验摘要。新收据复用加密 Context 消息引用。其余请求字段、变量与来源权限引用封装在 `context_state`，与准备的 Attempt 一同持久化，日志不序列化正文。
- `invoke_with_existing_journal_and_dispatch_gate` 沿用已接受结果的复用和未知物理调用的恢复限制。真正发起新物理调用时，最后一次检查已准备证据的权限、有效期、预算和当前执行权。可继续条件不足返回受阻；有待核对的调用采用既有对账。
- 新模型帧只重构当前可读的受验数据。已配对的历史观察按结构化数据输入。空值、预算裁剪和来源失效通过保守分支反馈；已受管配置越权或来源丢失时，记录原因并停止派发。纯澄清或受限任务保留既有固定路由行为。

## 4. 产物校验和评测领取

**拟新增** 受管输出校验与最小评测接口。评测替身走固定测试数据，调用真实选定模型并约束工具清单。

```python
def create_dashboard(result, run, evidence):
    validate_dashboard_contract(result)  # 组件、布局、尺寸及格式白名单
    resolved = [
        resolve_verified_observation(evidence, run.id, view.data_ref)
        for view in result.views
    ]
    return render_dashboard_payload(result, resolved)


def transfer_quantity(source_stock, safety_stock, destination_need):
    surplus = max(source_stock - safety_stock, 0)
    return min(surplus, max(destination_need, 0))


def claim_evaluation_case(tx, subject, evaluation_id, case_id, model_binding):
    report, row = lock_authorized_case(tx, subject, evaluation_id, case_id)
    require_frozen_suite_and_model_binding(report, case_id, model_binding)
    if row.is_terminal:
        return case_receipt(row)
    if row.claim_token and row.lease_until > tx.clock_timestamp():
        return case_running_receipt(row)
    if row.claim_token:
        mark_expired_attempt(tx, row)  # 计入结果记录，需显式授权重试。
        return case_interrupted_receipt(row)
    return mark_case_claimed(tx, row, timeout_seconds=30)
```

Case 完成按占用令牌比对条件写入，旧执行者提交过期结果时拒绝。显式重试只用于未完成或运行故障，保留旧运行状态，不覆盖已判定的业务失败。同一冻结候选重新建评测时须完整运行固定用例集。回归汇总只以登记用例和确定结果为准。
