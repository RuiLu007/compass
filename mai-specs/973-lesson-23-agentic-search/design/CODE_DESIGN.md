# 第 23 课 · 供评审的代码设计

[返回技术方案](TECH_DESIGN.md)。下列片段是拟议接口与关键逻辑，用于判断实施边界。存储适配、HTTP 序列化、异常映射和完整工具注册沿用项目实现。示意中的辅助函数均写明输入与保证。生产实现与用例在开发阶段交付。

## 1. 固定来源与查询时点

现有 `runtime/store.py` 在创建 Run 时固定 Context Runtime 与经营语义清单。在相同作用域中解析可选的资料包绑定。远端 pin 操作按租户、工作区、Run ID 幂等，先取得不可变清单标识、再与本地 Run 快照一起提交，恢复时只读已提交值。两边没有跨库事务。绑定缺失与读取失败分别记为“未启用”和“暂不可用”，启用工作区禁止因 pin 失败退为无截止的通用连接。可信的 `known_at` 覆盖语义解析以及重新解析 / 分页执行的默认截止，调用者参数始终从严收窄。

```python semantic/service.py · 拟议截止计算
def effective_cutoff(accepted_at, search_snapshot, historical_as_of=None):
    """所有输入为带时区时间。search_snapshot 仅由 Run 接受端写入。"""
    cutoff = accepted_at.astimezone(UTC)
    if search_snapshot is not None:
        cutoff = min(cutoff, search_snapshot.known_at.astimezone(UTC))
    if historical_as_of is None:
        return cutoff
    hint = datetime.fromisoformat(historical_as_of)
    if hint.tzinfo is None or hint > cutoff:
        raise ToolRejected("invalid_parameters")
    return hint.astimezone(UTC)
```

执行端校验 resolution/receipt 中的来源范围、manifest、注册绑定、K 与运行快照一致。原始 SQL 选择同一受限副本。没有启用课堂绑定的工作区保留现有截止逻辑。

## 2. 先做授权，再构造任何候选

Web 使用 `bindAccess(client).current` 从可信调用中的主体读当前活动成员和 `workspacePermissions`。当前来源政策从当前工作区的受控发布指针读取，按文号查询。政策被删除或撤回时拒绝读取历史版本。以下布尔判定在搜索、取原文、读取相邻段及 verify 中共用。接口层先验证服务令牌、主体和绑定，在同一请求上固定 ACL / 成员读取视图。私有报价受众按角色与区域显式分组。并发撤权按授权读取点生效，后续交付重新取当前视图。

```typescript documents/access.ts · 拟议可见性判定
type Audience = { workspaceId: string | null };
type DocPolicy = Audience & { tenantId: string; enabled: boolean };
type Version = Audience & {
  tenantId: string;
  collectionDigest: string;
  publishedAtMs: number;
  requiresApproval: boolean;
  approvedAtMs: number | null;
};
type RunPin = {
  tenantId: string;
  workspaceId: string;
  collectionDigest: string;
  knownAtMs: number;
};

function canReadVersion(
  access: CurrentAccess,
  pin: RunPin,
  version: Version,
  currentPolicy: DocPolicy | undefined,
): boolean {
  if (!access.memberId || access.tenantId !== pin.tenantId) return false;
  if (!currentPolicy?.enabled || currentPolicy.tenantId !== pin.tenantId) return false;
  if (version.tenantId !== pin.tenantId || version.collectionDigest !== pin.collectionDigest)
    return false;
  if (version.publishedAtMs > pin.knownAtMs) return false;
  if (
    version.requiresApproval &&
    (version.approvedAtMs === null || version.approvedAtMs > pin.knownAtMs)
  )
    return false;
  if (!allowed(access, "module.analysis", pin)) return false;
  return [version, currentPolicy].every(
    ({ workspaceId }) => workspaceId === null || access.workspacePermissions.has(workspaceId),
  );
}
```

`workspaceId: null` 表示发布方明确登记为当前业务工作区内公开，导入拒绝丢失受众字段。查询另要求工作区当前允许使用该资料类别，历史/当前查询按业务适用期筛选。直接读取也验证这一范围，且段落引用必须在同 Run 的受信观察集合内。来源的并发更新通过摘要隔离，旧版保留，当前访问政策即刻重验。接口只在可见段落中计算排名和摘要，未命中、越权、猜测文号使用同一无可见结果语义。

## 3. 为缺货率绑定提供可重算的分子、分母

沿用 Web 语义绑定的受信参数 `p`、来源水位 `coverage`、schema 验证及最终回执格式。以下 PostgreSQL 片段示意**整个窗口**的指标核心，承接既有 `checked/p` 的授权范围、release 和参数检查。数据库会话和 RLS 再限制租户、区域。平台把同一次 Run 的可信连接范围独立传给绑定。完整绑定清单还要列出 `order_header`、`stock_allocation_attempt`、`source_watermark`、`tenant` 等实际访问关系。

```sql allocation-insufficient.sql · 拟议窗口聚合
, eligible AS (
    SELECT a.tenant_id, a.allocation_attempt_id, a.attempted_at, a.outcome
    FROM p
    JOIN stock_allocation_attempt a ON a.tenant_id = p.tenant_id
    JOIN order_header o ON o.tenant_id = a.tenant_id AND o.order_id = a.order_id
                       AND o.region_id = a.region_id
    WHERE a.attempted_at >= p.start_at AND a.attempted_at < p.end_at
      AND a.available_at <= p.k AND o.available_at <= p.k
      AND a.outcome IN ('COMMITTED', 'INSUFFICIENT', 'ROLLED_BACK')
      AND (NOT (p.filters ? 'region') OR a.region_id = (p.filters->>'region')::int)
      AND (NOT (p.filters ? 'channel') OR o.channel_id = (p.filters->>'channel')::int)
      AND (NOT (p.filters ? 'fulfillment_site')
           OR a.site_id = (p.filters->>'fulfillment_site')::int)
      AND (NOT (p.filters ? 'sku') OR a.sku_id = (p.filters->>'sku')::int)
), counts AS (
    SELECT count(*) FILTER (WHERE outcome = 'INSUFFICIENT') AS numerator,
           count(*) AS denominator
    FROM eligible
), results AS (
    SELECT 'total'::text AS group_key,
           CASE WHEN gap IS NULL THEN numerator::numeric / nullif(denominator, 0) END AS value,
           coalesce(gap, CASE WHEN denominator = 0 THEN 'NO_FACTS' ELSE 'OK' END) AS status,
           detail || CASE WHEN gap IS NULL
               THEN jsonb_build_object('numerator', numerator, 'denominator', denominator)
               ELSE '{}'::jsonb END AS detail
    FROM counts CROSS JOIN coverage
)
```

尝试主键为租户与尝试 ID，订单主键约束保证每次尝试至多匹配一个订单。按日分组从相同 `eligible` 取业务日期并补齐请求日期格，无尝试日返回无样本，延迟日标记相应水位。全窗直接对这些尝试计数。既有执行器的展示与 `period_totals` 应对 ratio 禁用按日加法，观察分子/分母由 `detail` 传给报告层。SKU 日报的访客人日与原始会话计数保持不同的字段与单位。

## 4. 同一条派生意见，怎样挡住过期报价或错误余款

从已获准且已登记的观察构造类型化叶子。结构化叶子使用 `observationId + rowKey + column`，文档叶子使用 `observationId + sectionHash + span` 或完整表的行/列键。offset 按规范化后、用于生成 `sectionHash` 的同一串 Unicode 码点计数。邻近单位和表头必须包含在校验上下文。模型不能传入新的事实常数。币种和分、元转换由叶子的受信解析完成。每条派生记录携带父叶子集合，最终投影取并集。下面的代码是这一通用操作的可审查核心。

```python context_runtime/delivery.py · 拟议的整档校验
def check_whole_tier(balance, fraction, offer, draft, decision_at):
    # 参数来自已验证的观察，draft 仅声明希望展示的选择。
    if not offer.valid_at(decision_at) or not offer.complete_table:
        return Pending("当期完整报价待核")
    if balance.currency != offer.currency or not 0 <= fraction.value <= 1:
        return Pending("币种或额度比例须复核")
    if balance.fen < 0 or any(tier.fen <= 0 for tier in offer.tiers):
        return Pending("余额或档位金额须复核")
    limit = (Decimal(balance.fen) * fraction.value).to_integral_value(rounding=ROUND_FLOOR)
    eligible = [tier for tier in offer.tiers if tier.fen <= limit]
    best_fen = max((tier.fen for tier in eligible), default=None)
    if best_fen is None:
        if draft.tier_id is not None or draft.retained_fen != balance.fen:
            return Invalid("无符合额度的整档，请修正建议")
        return Verified(None, balance.fen, balance.refs | fraction.refs | offer.refs)
    selected = next((tier for tier in eligible if tier.id == draft.tier_id), None)
    if selected is None or selected.fen != best_fen:
        return Invalid("选定档位与已核上限及适用条款不符")
    retained = balance.fen - selected.fen
    if draft.retained_fen != retained:
        return Invalid("余款与已核余额及购买金额不符")
    return Verified(selected.id, retained, balance.refs | fraction.refs | offer.refs)
```

“上限内最高整档”仅在模型引用的当前条款要求该选择、资格齐备且报价明确整档约束时采用。调用前验证必需的指标回执及样本、主体/对象/期间和单位，结果再次合并资格、选择规则和整档限制的来源依赖。等额同价档可选择任一满足适用条件的档，否则保留待定。无可行档时完整保留余额。金额上限取不超出的整数分。表达式仅允许有限深度、有限叶子、白名单操作和已有观测引用，不执行模型提供的表达式代码。

日期的对应操作从已核上线本地时刻起逐日向后，先读覆盖年份内的调休/法定例外，再用该日历声明的通常工作周。取首个工作日，再结合复核条款的本地时刻。没有足够日历、条款或时区就返回待确认。报价上的回函时刻与复核时刻各自绑定出处。决策所需的自然语言判断与未覆盖字段仍需经营金标准评估。

## 5. 让撤权穿过运行记录和缓存

既有 `record_source` 在 Run 锁与 epoch 检查下写 `AgentMessage.source_refs`，新文档引用通过 `allows` 识别并调用 Web verify。下面是持锁更新中对**已启用多源快照的 Run**新增的处理。批量登记在一次锁内完成后返回。`existing + incoming` 包含本批搜索返回的所有段落。当前来源记录逻辑对超过 64 条的集合已有溢出处理，新逻辑防止溢出后丢掉敏感来源依赖。

```python context_runtime/source_access.py · 拟议的持久来源集合
def merge_protected_refs(existing, incoming):
    blocked = {"kind": "source_set_blocked"}
    if blocked in existing:
        return [blocked]
    merged = list(existing)
    for ref in incoming:
        if ref not in merged:
            merged.append(ref)
        if len(merged) > 64:
            return [blocked]
    return merged
```

`allows` 看到阻断标记立即返回拒绝。私有状态仍受保护，下一次交付可以说明来源超限、需重新缩小查询。签批意见统一在“登记成功 → 重新鉴权全部父引用 → 校验草稿 → 提交私有内容 → 当前主体可见投影”链路产生。撤权触发后，历史搜索候选、派生明细、摘要、SSE 续传和附件均由同一投影检查挡住。可缓存不可变原文与索引。复用观察同样需要最新授权。此处优先整份隐藏，避免删掉报价行后仍暴露余款。

## 6. 能从一次真实运行读出什么

保留 `context_runtime/model_step.py` 已有的阶段与提示词回执，工具观察的扩展只承载业务行为，不要求模型提交思维过程。公开运行事件记状态和费用可计量性，授权私有记录关联观察、实体键与范围。单个补查应能还原到以下形状。

```text 可审阅的观察关系 · 字段示意
ToolObservation
  runId, stepId, toolCallId, observationId
  tool, normalizedInputDigest, snapshotDigest, logicalSourceKinds
  status, sourceRefs, observedAt, elapsedMs
  resolvedKeys, visibleTimeRange, resultShape
  parentObservationIds, changedFilterOrQuestion
  stopReason, remainingBudget
```

评测从真实参数和回执核对父观察的实体或日期是否进入后续检索。已有线索可由不同调用获得。费用与 token 取真实模型回执，缺项标记未计量。入参摘要含 Run 快照与操作，不含敏感片段。讲师评分表根据可见记录独立给分。

## 7. 一批结果怎样驱动下一次模型调用

`runtime/turn.py` 继续承接模型的 tool calls。工具结果一旦提交，先验证其来源与质量，再计算本分支相对上一批的事实变化。该反馈在下一次 `prepare_input` 进入 L3/L5，原始行和段落通过 L4 的私有引用重开。下面是搜索停止的纯判定示意。调用方另行验证当前 Worker 的 epoch，失租由接管端处理。

```python runtime/turn.py · 拟议搜索反馈判定
def search_feedback(gaps, budget, *, cancelled, goals_covered, model_submitted):
    # gaps 来自已提交的目标、规则及校验结果。可选假设不阻塞采购决定。
    required = [gap for gap in gaps if gap.required]
    pending = [gap for gap in required if not gap.resolved]
    if cancelled:
        return "cancelled"
    if goals_covered and required and not pending:
        if model_submitted or not budget.can_search_and_deliver:
            return "sufficient"
        return "ready_to_finish"
    if not budget.can_search_and_deliver:
        return "budget_exhausted"
    if pending and all(not gap.authorized_paths for gap in pending):
        return "blocked"
    viable = [gap for gap in pending if gap.authorized_paths]
    if viable and all(gap.no_gain_batches >= 2 for gap in viable):
        return "no_progress"
    if model_submitted and (not required or not goals_covered):
        return "define_decision_or_report_gap"
    return "continue"
```

`goals_covered` 要求草稿涵盖已登记的决策目标和适用条款带来的必要事项，接受端复核其范围与来源。主模型收到 `ready_to_finish` 后可立即提交，预算硬限独立生效。这里的“两批”为首版可调整的分支停止值。已确认“不符合且应暂缓”或“用户只需一个已核取值”可以是 `resolved`。信息不足且签批待定的条件保留为 `pending`。收到候选片段、同一事实的复述、空数据和工具成功码，各自保留其可用范围。只有新增对象、可核值、质量改善、适用反证或决定条件的变化才重置该分支的无进展计数。一次重复提议在去重后仍作为本轮无新增反馈回到模型。并行批次按业务缺口一次结算。

**与当前接口对接。** 工具调用继续使用真实模型返回的 `tool_calls`。主 Agent 要提交核验时以无工具的结构化草稿表达 `targets / claimProposals / answerBlocks`。在启用搜索的 Run 内，`handle` 消费草稿并调用校验函数。有缺口且仍有可行路径时，`begin_phase("query", feedback)` 交回获权工具。已充分则进入 generate 或直接渲染已核的简单答复。相同缺口、相同已核观察的提交被记为无进展，避免反复校验。复杂任务直接沿用 `todo_write` 的 `baseVersion` 维护计划。原待办的 `verified` 保留为计划项完成标记，证据状态来自服务端校验。

### 两个接入点的调用约定

拟新增 `context_runtime/search_verification.py`，直接由主 Run 的现有工具分派和答复接收端调用。`verify_batch(run, step, calls, results, targets)` 接受有序的 `ToolExecutionResult`，`verify_draft(run, step, draft, admitted_state)` 接受主模型的结构化提案。两者读取同一份已签收状态。数据库操作复用 `journal`、`save_private`、当前 epoch 和授权读取端；断言判断可在纯函数中测试。

| 调用方需要改哪一段                                                                | 新行为与不变量                                                                                                                                                                                                                                                                                                                                         |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `runtime/tool_bindings.py::RunToolBindings.dispatch` 中 `dispatch_batch` 返回以后 | 先按 `call_id` 对齐 `step.tool_calls` 与原始执行结果。执行状态与业务载荷里的覆盖/截断分别解析，取消、未确认清理和审批等待仍走现有控制分支。对可消费的同一批，在 Run 锁与 epoch 下幂等登记来源、事实和差异。持久化完成后才按原工具顺序调用 `state.record_tool_result`。响应使用获准的有界观察，保证每个原生调用有且仅有一个对应工具消息                 |
| `context_runtime/model_step.py::call_model` 与 `pipeline.prepare_input`           | 在整组工具消息之后附加由 Runtime 序列化的 `searchFeedback`，标明可信来源，只提供可见事实与缺口。模型收到一次反馈后自己选下一步。若 provider 续接要求精确前缀且不接受新增数据消息，或片段被撤权，清除续接 token，通过同一阶段重新组帧。重放使用同一批次键，避免重复反馈或计数                                                                           |
| `runtime/turn.py::execute_turn` 内嵌的 `handle`                                   | 启用搜索时，plan 的无工具输出先收下目标，query/analyze 的无工具输出先校验草稿，generate 的输出检查已核键。入口放在普通问答 `_final_answer` 和报告 `finish_delivery` 之前。模型草稿按局部键签收并由服务端分配修订，不能直接绘成普通聊天正文。歧义沿现有 `RunWaiting` 交还用户；多目标查询中的某一正式绑定不可用时记为该事项的缺口，仍有独立事项则可继续 |
| `context_runtime/delivery.py` 与 `source_access.py`                               | `verified` 的事实块在交付前再次查父引用与当前 ACL。通过后生成正文、角标及其不可变清单。缺口可反馈给同一模型；当前无权限的值、原文与由它算出的余款不进入反馈。已有 `RunService` 在候选结果后的可选只读 hooks 继续在原处运行                                                                                                                             |

```python runtime/turn.py · 拟议草稿核验后的分支
def advance_after_verification(report, *, repair_used):
    # report 由 verify_draft 按已接受回执生成，模型无法写这些字段。
    if report.cancelled:
        return "cancel"
    if report.access_changed:
        return "project_current_access"
    if report.open_required or not report.goals_covered:
        if report.can_search and report.stop_reason not in {
            "budget_exhausted", "blocked", "no_progress"
        }:
            return "query"
        return "render_partial"
    if report.invalid_blocks:
        if report.can_generate and not repair_used:
            return "repair_generation"
        return "render_verified_only"
    if report.publishable:
        return "publish"
    if report.can_generate and not repair_used:
        return "generate"
    return "render_verified_only"
```

`query` 调用 `begin_phase("query", feedback)` 并返回 `RepeatStep()`。`generate / repair_generation` 进入无工具阶段，在原预算内使用已核键，格式修复最多一次。`publish` 经引用校验输出已核正文。`render_partial / render_verified_only` 由服务端渲染获准事实和缺口，缺资格时抑制采购建议。`project_current_access` 重新投影可读范围。硬截止、取消和失租服从现有 Run 终态与持租规则。

### 正式值与 SQL 明细如何在同一轮使用

`database_query/handler.py` 当前在解析任何 `op` 前根据整轮的 `formal_query_started()` 拒绝自由 SQL。新增逻辑读取已签收的任务状态与当前调用授权。纯正式取值继续使用 `formal_metric_requires_query_metric`；启用搜索的混合任务须有独立开放的明细事项、获准的只读连接和固定截止。模型调用参数无法修改事项的可信绑定。适配器把结果签为明细，表发现与字段说明仅签为目录。正式断言的接受条件可用下列判断验证。

```python context_runtime/search_verification.py · 拟议正式指标取值检查
def supports_metric_value(target, receipt):
    # scope_key 包含租户、工作区、对象、过滤、业务窗、K 和粒度。
    return (
        receipt.tool == "query_metric"
        and receipt.accepted
        and receipt.currently_readable
        and receipt.run_id == target.run_id
        and receipt.manifest == target.manifest
        and receipt.metric_version == target.metric_version
        and receipt.scope_key == target.scope_key
        and receipt.unit == target.unit
        and receipt.complete
        and receipt.value is not None
    )
```

阈值比较、跨期比率与签批建议在对应取值通过后再校验父断言和规则。`complete` 取自已签收回执的水位、质量状态及样本；0/57 的零比率可以通过，无数据、截断和未知水位各自保留缺口。对有金额或比例的候选 SQL 行，即使数值碰巧相等，这个判定仍要求正式工具和对应绑定。独立采购实收断言走明细键、单据动作及时间校验。

## 8. 在压缩输入前选择正确的材料

```python context_runtime/pipeline.py · 拟议组装次序
def select_search_material(stage, bootstrap, task, observations, verified):
    # bootstrap 已按真实身份、当前权限与固定修订解析。
    if stage == "identify":
        return [bootstrap.platform(stage), bootstrap.routing_scope()]
    frame = [bootstrap.platform(stage), bootstrap.workspace_summary()]
    frame.append(bootstrap.selected_scene_pack(stage))
    frame.append(task.decision_and_remaining_budget())
    if stage == "generate":
        frame.extend(verified.answer_context())
    elif stage in {"query", "analyze"}:
        frame.extend(verified.progress_and_open_gaps())
        frame.extend(observations.relevant_to(task.active_targets))
    return frame
```

上述函数返回带 `layer, source, access, group, required, trust` 的条目。identify 的工作区摘要仅含路由许可的最少字段，场景包在路由后选定。组装器把选中的小包视为完整单位，依现有 `fit` 的字节上界和模型输出预留进行装载，并记录被省略的组。改变必需包、来源权限或已送 provider 的前缀时生成新帧。首次步骤、工具续接、托管问答、报告生成和恢复载入都需要经过这一接口。`answer_context()` 从同一轮已核状态产生业务口径、已核事实、假设/缺口、允许引用的四组数据。条目中的原文以观察角色封装，不能变成新的系统规则。

## 9. 校验断言状态后再分配角标

内部草稿中的事实块使用 `{use, claimId, revision}`。标题和连接词由展示层提供，每个事实字段、表格单元和建议均经受支持的断言类型渲染。原代码中的 `ViewSpec.factRef` 仍指向本轮可用视图。引用索引按断言修订单独分配，单个断言可包含多条取值、条件和反证关系。

```python context_runtime/delivery.py · 拟议引用投影
def publish_claim_blocks(blocks, manifest, reader):
    allowed_states = {
        "fact": {"supported"},
        "recommendation": {"supported"},
        "hypothesis": {"insufficient"},
        "conflict": {"contested"},
        "correction": {"refuted"},
    }
    numbers, output = {}, []
    for block in blocks:
        key = (block.claim_id, block.revision)
        claim = manifest.claims.get(key)
        if claim is None or claim.status not in allowed_states.get(block.use, set()):
            raise ValueError("claim_not_publishable")
        # 绑定同一身份、Run、快照及断言修订，并重验所有父叶子。
        if not reader.may_read_current_closure(claim, manifest):
            raise ValueError("source_unavailable")
        # 复核原文/计算/反证，按用途渲染事实、假设、冲突或纠正。
        text = reader.render_verified_block(claim, block.use)
        if key not in numbers:
            numbers[key] = len(numbers) + 1
        output.append((text, numbers[key], key))
    return output
```

`manifest` 由服务端从私有记录构造，模型只能选键。`may_read_current_closure` 核对作用域、定位、固定内容和当前 ACL，缺任何一项即拒绝。`render_verified_block` 使用已校验的结构化值、原文或显式标注的缺口，清除草稿中模型自填的数字、链接和角标。支撑失效则拒绝该块。反驳用已核相反事实表达，争议块需有双方，假设块必须显示证据缺口。调用方按正文顺序计算码点偏移，将内容摘要与清单一并提交。在下次打开详情时重复完整权限检查。旧问答无清单时继续按普通 Markdown 展示。
