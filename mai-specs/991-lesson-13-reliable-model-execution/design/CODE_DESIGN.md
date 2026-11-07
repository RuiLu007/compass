# Feature 991：供评审的代码设计

[返回技术方案](TECH_DESIGN.md)

本文为 Python 设计示意，尚未实现或运行。沿用现有 `ExecutionRequest`、`Runtime.invoke`、`RuntimeCancelled` 与 `ModelService`。省略 DTO 序列化、现有 SDK 协议转换及数据库样板；核心恢复、容量和提交判断在下文展开。实际错误码、默认值与 AC 以主文为准。

## 1. 共享闸门与所有权

拟新增 `llm/admission.py`。Gate 在应用 lifespan 的异步上下文内构造，所有 ModelService 注入同一实例。下列操作只在该事件循环使用；`enter/leave` 无 await，使 N 的检查和计数处于一个调度片段。

```python
class RuntimeOverloaded(Exception):
    pass


class ExecutionGate:
    def __init__(self, capacity: int, max_inflight: int):
        if not 1 <= capacity <= max_inflight <= 256 or capacity > 64:
            raise ValueError("invalid execution limits")
        self.capacity = capacity
        self.max_inflight = max_inflight
        self.inflight = 0
        self.quarantined = 0
        self.slots = asyncio.BoundedSemaphore(capacity)

    def enter(self) -> None:
        if self.inflight >= self.max_inflight:
            raise RuntimeOverloaded()
        self.inflight += 1

    def leave(self) -> None:
        if self.inflight <= 0:
            raise RuntimeError("logical slot released twice")
        self.inflight -= 1

    def finish_attempt(self, *, cleanup_confirmed: bool) -> None:
        if cleanup_confirmed:
            self.slots.release()
        else:
            self.quarantined += 1
```

获取执行槽的等待必须在逻辑 Deadline 中；仅在 acquire 成功后进入对应 try/finally。`asyncio.Semaphore.acquire` 的取消行为负责还原尚未取得的许可，调用者不得额外 release。获得槽后先用捕获的 attempt_started 检查期限，再构造 CallPolicy；Runtime 继续检查实际出站前的期限及取消。

在 `main.py` lifespan 创建 Gate 和应用任务集合，正常退出时按既有期限取消/等待所有登记的执行任务，再结束生命周期。HTTP 根任务中，Chat 流式/非流式登记 RoutingService.run，诊断流式/非流式登记 ModelService.run；当前直接 await 的非流式诊断改由同一登记入口创建 runner。完成回调删除根任务，关闭期间禁止新任务进入，子执行由已有取消所有权链收束。`get_model_service(request)` 将 `request.app.state.execution_gate` 传入 Service，生产构造函数不提供隐式“新建一个 Gate”的兜底。测试覆盖不同 Service 共享实例，以及不同测试事件循环彼此独立。

Review：N 只覆盖执行器，准备事务和终态数据库连接继续按原机制控制；隔离槽在该进程生命周期内不恢复。不能将容量计数解释为供应商远程计算已经停止。

## 2. 逻辑调用与恢复分支

拟新增 `llm/executor.py`。`LogicalRun` 是每次调用私有的累积对象，含原始请求深拷贝、UTC 开始时间、单调时钟、已有摘要和最后一次结果。它的方法契约如下：

| 方法                                      | 明确行为                                                                                                                                                                                                                   |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `observe(attempt_id, result, started_at)` | 向最多两项列表追加安全快照；不保存输出正文或凭据                                                                                                                                                                           |
| `finish(reason, error=None)`              | 计算类别独立的总用量和总耗时，保留最后输出及安全摘要；零 Attempt 时创建空输出、failed、已知零用量且未发送的结果；显式错误覆盖错误码并将逻辑状态置为 failed，已展示片段时为 incomplete；所有非 completed 结果清空续接 token |
| `cancelled()`                             | 形成 canceled 安全结果、已有摘要与未知发送状态，作为 RuntimeCancelled.result 交给应用收尾；无再次出站                                                                                                                      |

下列为协调逻辑伪代码，`finish` 返回现有 RuntimeResult 的逻辑级扩展（追加 execution）；外层 `ModelService.to_response` 统一映射。辅助值构造按上述契约实现，不能在序列化层重新判断恢复资格。

```python
RETRYABLE_CODES = {
    "model_unavailable_upstream",
    "model_rate_limited",
}


def can_retry(result, content_exposed: bool) -> bool:
    return (
        result.status == "failed"
        and result.error is not None
        and result.error.code in RETRYABLE_CODES
        and result.error.recovery in {"not_sent_transient", "rejected_transient"}
        and not result.upstream_outcome_unknown
        and not content_exposed
        and result.cleanup_confirmed
    )


async def execute(self, request, logical_policy, emit):
    # logical_policy 由服务端阶段显式传入：主生成=2；分类/诊断/决策=1。
    # CallPolicy 始终表示一次 Attempt，其 max_attempts 保持 1。
    run = LogicalRun(deepcopy(request), logical_policy)
    deadline = request.policy.deadline_monotonic
    loop = asyncio.get_running_loop()
    content_exposed = False

    async def preview(event):
        nonlocal content_exposed
        if event.type in {"text_delta", "tool_delta"}:
            content_exposed = True  # 在 await 前禁止后续换一份输出。
        await emit(event)

    try:
        self.gate.enter()
    except RuntimeOverloaded:
        return run.finish("overloaded", error="runtime_overloaded")

    try:
        for number in range(1, logical_policy.max_attempts + 1):
            if loop.time() >= deadline:
                return run.finish("deadline_exceeded", error="model_timeout")
            queue_started = loop.time()
            try:
                async with asyncio.timeout_at(deadline):
                    await self.gate.slots.acquire()
            finally:
                run.queue_wait_ms += int((loop.time() - queue_started) * 1000)
            # 从这里开始 owns_slot；上面的取消/超时没有 release 权限。
            cleanup_confirmed = True  # 尚未分派，下面无 await 的构造失败可释放。
            try:
                attempt_started = loop.time()
                if attempt_started >= deadline:
                    return run.finish("deadline_exceeded", error="model_timeout")
                policy = replace(
                    request.policy,
                    started_monotonic=attempt_started,
                    deadline_monotonic=min(
                        deadline,
                        attempt_started + logical_policy.attempt_timeout_s,
                    ),
                )
                invocation = replace(request, policy=policy)
                attempt_id, started_at = uuid4(), datetime.now(UTC)
                cleanup_confirmed = False  # Runtime 持有全部资源后，靠结果确认。
                try:
                    result = await self.runtime.invoke(
                        invocation, preview if emit is not None else None
                    )
                except RuntimeCancelled as exc:
                    cleanup_confirmed = exc.result.cleanup_confirmed
                    run.observe(attempt_id, exc.result, started_at)
                    raise
                cleanup_confirmed = result.cleanup_confirmed
                run.observe(attempt_id, result, started_at)
            finally:
                self.gate.finish_attempt(cleanup_confirmed=cleanup_confirmed)

            if not cleanup_confirmed:
                return run.finish("cleanup_unconfirmed", error="resource_cleanup_failed")
            if result.status == "completed":
                return run.finish("completed")
            if not can_retry(result, content_exposed):
                return run.finish("non_retryable")
            if number == logical_policy.max_attempts:
                return run.finish("attempts_exhausted")

            delay = self.jitter(0.1, 0.3)
            if loop.time() + delay + 0.010 >= deadline:
                return run.finish("deadline_exceeded", error="model_timeout")
            async with asyncio.timeout_at(deadline):
                await self.sleep(delay)

        raise AssertionError("attempt policy must contain at least one attempt")
    except RuntimeCancelled:
        raise RuntimeCancelled(run.cancelled()) from None
    except asyncio.CancelledError:
        raise RuntimeCancelled(run.cancelled()) from None
    except TimeoutError:
        return run.finish("deadline_exceeded", error="model_timeout")
    finally:
        self.gate.leave()
```

拟改 `RuntimeResult`：追加 `cleanup_confirmed: bool`，`RuntimeErrorInfo` 追加内部 recovery 枚举（默认 never）。原 Runtime 的 finally 汇总每次 aclose 的成功、超时与遗留任务；任何未确认项置 false。Runtime 对取消继续抛 RuntimeCancelled，包含当次状态及清理标记，执行器追加摘要后向应用继续传播。裸异常由 ModelService 的原有安全失败与收尾兜底处理，Gate 此时保守隔离已分派槽。

Runtime 仍负责检查 Attempt Deadline、绑定/输入、正常协议终止与输出有效性。进入 Adapter 前检查任务的 `cancelling()`；截止时间已到或取消已请求时不出站。其错误分类保留当次已知 output/usage，但 execution 汇总和提交不读取失败正文作为成功结果。

Review：取消与内部 Timeout 的区分必须沿现有结构化取消语义验证；不要在 executor 外再套用会丢弃 RuntimeCancelled.result 的 wait_for。`CallPolicy` 在每次 Attempt 构造时调整单次开始时间，逻辑总开始时间只由 LogicalRun 持有。

## 3. 安全故障分类

拟改 `llm/errors.py`、两类 Adapter 和 Transport 的异常捕获。SDK/HTTP 原始异常在最接近边界时提取安全信息，禁止把响应文本放入错误或日志。

```python
def classify_recovery(*, code, status_code, sent, outcome_unknown, has_partial):
    if outcome_unknown or has_partial:
        return "never"
    if code not in {"model_rate_limited", "model_unavailable_upstream"}:
        return "never"
    if status_code == 429 or (
        isinstance(status_code, int) and 500 <= status_code <= 599
    ):
        return "rejected_transient"
    if sent is False and code == "model_unavailable_upstream":
        return "not_sent_transient"
    return "never"
```

这里 `sent=False` 必须来自确认的发送边界，资源默认未初始化不构成“未发送”的分类依据。原生 SDK 在进入推理入口前保守置 sent=True；错误状态不明时 recovery=never。明确 429/5xx 应保留“终止拒绝”事实，Runtime 的 unknown 计算要按该事实排除旧的宽泛归类，避免把可恢复拒绝误设 unknown。取消优先，已存在 text/tool 输出优先停止；HTTP 500 带生成片段或收到 2xx 后再读失败不授予 rejected_transient。

能力测试仍只调用一次；在 SDK 调用参数和连接配置校验中持续禁止 num_retries/max_retries/fallback 被用户连接参数覆盖。SDK 认证与协议 HTTP 单独验证，不能假设一次 SDK 调用只会发生一次认证 HTTP。

## 4. 用量与最终提交

用量汇总按输入、输出、推理三个类别分别调用下列纯函数。明确未发送的尝试先规范为三个 0；已发送但未提供的字段维持 None。

```python
def aggregate_usage(attempts, field):
    values = [getattr(attempt.usage, field) for attempt in attempts]
    if any(value is None for value in values):
        return None
    return sum(values)
```

`models/repository.py:finish` 保留现有 `WHERE call_id=:id AND status='running'`，将安全 execution 与现有终态字段放入同一 UPDATE；同终态确认也比较 execution。现有版本的终态不可被新尝试覆盖。

ModelService 成功路径先组装带摘要的响应并检查 2 MiB 上限，再 `_finish`。准备取消、执行取消和终态写入失败继续使用既有有界短事务；所有非 completed 对外结果清除 continuation_token。数据库失败返回 record_write_failed，保留原 call_id 查状态，不调用 execute 第二次。

Web `use-model-call.ts` 同时处理 Chat 路由及 Provider 诊断。以下 TypeScript 片段分别对应 onEvent 的 final 分支及既有 modelRequest await 之后；start、classification_status、route_selected、delta 累积、调用 ID 校验和大小限制保持原分支。final 映射只在整体读取成功后执行一次。

```typescript
type TerminalEvent = Extract<ModelEvent | RoutedModelEvent, { type: "final" }>;
let pendingFinal: TerminalEvent | undefined;

// onEvent：start 与 delta 沿用当前分支；final 不在此处标成功。
if (event.type === "final") {
  if (pendingFinal) throw new Error("收到重复的模型结束事件。");
  pendingFinal = event;
}

// await api.modelRequest(...) 成功返回后：流的完整性检查已通过。
if (pendingFinal) {
  if ("result" in pendingFinal) {
    const { type: _type, ...routed } = pendingFinal;
    void _type;
    const result = routed.result ?? undefined;
    if (
      (routed.status === "completed" && result?.status !== "completed") ||
      (routed.status !== "completed" && result?.status === "completed")
    )
      throw new Error("路由结果与模型终态不一致。");
    view = { ...view, status: routed.status, result, routed };
    publish();
    // 分类失败可以没有主生成结果，保持失败展示，不更新 Chat 历史。
    return result ? { ...result, routed } : undefined;
  }
  const { type: _type, ...outcome } = pendingFinal;
  void _type;
  const result: CallResponse = {
    ...outcome,
    kind: "generate",
    text: view.text,
    tool_calls: view.tools,
    answers: null,
    raw_response: null,
  };
  view = { ...view, status: result.status, result };
  publish();
  return result;
}
```

非流式分支分别保持 RoutedCallResponse/CallResponse JSON 返回路径，路由 envelope 同样检查内外成功一致性。异常分支将 pendingFinal 清空，把预览标为 failed/incomplete，并清除旧 `view.result`、`view.routed`，不保留嵌套续接 token。`analysis-composer.tsx` 仍仅在 run 正常返回 completed 且无工具建议时写下一轮历史；工具建议保持待外部处理，禁止从预览触发执行。页面切换、失权和退出遵守已有 DocumentSession 生命周期。

厂商连接在正文/工具片段已转发后断开，执行器依据 content_exposed 结束调用；Hook 继续保留片段并标记未完成。用户手动重新提交原问题会分配新 call_id，使用原成功历史与问题重新生成；失败 assistant 片段不进入 messages，也不拼入新答案。现有 continuation_token 仅服务于完整成功工具建议后的上下文绑定，本期不承载流位置恢复。

Review：浏览器返回 final 仅表示协议完整，随后仍须检查 completed；写记录失败或输出校验失败要能保留预览并阻止历史更新。设计片段不实现持久化输出，也不建立跨服务提交事务。

## 5. 现有路由阶段的显式策略

拟调整 `routing/service.py:RoutingService.run` 内的 stage 函数。下面 run_stage 片段复用既有外层的 self、command、context、deadline、metrics 和 emit；其余分类结果校验、查字典及选模逻辑保持。ModelService.run 的 logical_policy 由生产入口显式传入，既有内部调用省略时保持一次，Provider 诊断入口显式传 `LogicalPolicy(max_attempts=1)`；默认策略对象采用一次，不以 GenerateInput 推断恢复权限。RuntimeResult 内部增加 adapter_started，观察摘要时只计入实际 Adapter 入口；在其之前结束的结果保留零 Attempt。

```python
async def run_stage(role, provider_id, value, stage_deadline):
    if role not in {"intent_jev", "intent_flash", "inference"}:
        raise ValueError("unknown model stage")
    classifier = role in {"intent_jev", "intent_flash"}
    physical_id = uuid5(command.call_id, role) if classifier else command.call_id
    grant = (
        RoutingGrant(context.actor_user_id, context.scope, physical_id, provider_id, value.kind)
        if classifier
        else None
    )
    logical_policy = LogicalPolicy(
        max_attempts=2 if role == "inference" and value.kind == "generate" else 1,
        attempt_timeout_s=180,
    )
    prepared = await self.models.prepare(
        physical_id,
        provider_id,
        value,
        context,
        deadline=min(deadline, stage_deadline),
        grant=grant,
    )
    result = await self.models.run(
        prepared,
        value,
        logical_policy=logical_policy,
        emit=emit if role == "inference" else None,
    )
    if classifier:
        metrics.classification_calls.append(
            CallOutcome.model_validate(result.model_dump(include=set(CallOutcome.model_fields)))
        )
    if result.error_code == "record_write_failed":
        raise ModelError("record_write_failed", 503)
    return result
```

路由父期限仍在 RoutingService.run 开始时创建，分类阶段沿用较短期限；上面只传递已有 deadline，不重新开始 180 秒。主生成内所有 Attempt 共享准备时的配置与输入；分类元记录和主生成 execution 分开保留，最坏一轮模型推理总数为四次。
