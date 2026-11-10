# 第 15 课 · Agent 执行内核代码设计

[返回技术方案](TECH_DESIGN.md) · [需求简报](../BRIEF.md)

片段为设计伪代码，按拟修改的模块组织。DTO 字段、状态、限制和部署规则以主方案为准。省略构造器、异常消息映射和序列化样板。实现时沿现有接口编写并运行对应测试。

## 0. Runtime 类型与模块装配

`AgentDefinition` 描述代码内的 Agent。`RunRoute` 是一次运行内确定的模型绑定和工具授权。`AgentState` 由 Runner 持有，生命周期由 `RunControl` 持有，HTTP 取消入口只操作后者。`AgentExecutor` 直接组合现有模型服务与固定 Retail 工具。下列片段展示类型的变化点，状态转换在后续片段。

```python
# 拟议 · runtime/context.py 与 runtime/executor.py
@dataclass(frozen=True, slots=True)
class AgentDefinition:
    system_prompt: str

    def tool_definition(self) -> ToolDefinition:
        return retail_tool_definition()  # 返回独立 DTO，Schema 真源归 Retail 模块。


@dataclass(frozen=True, slots=True)
class RunRoute:
    provider_id: UUID
    binding_version: int
    stream: bool
    thinking: bool
    retail_binding: RetailBinding | None = field(repr=False)

    @property
    def tools_enabled(self) -> bool:
        return self.retail_binding is not None


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    status: Literal["ok", "empty", "error"]
    observation_json: str = field(repr=False)
    summary: ToolSummary


@dataclass(slots=True)
class Step:
    id: UUID
    index: int
    model_call_id: UUID
    status: StepStatus = "running"
    tool_calls: tuple[ToolCall, ...] = field(default=(), repr=False)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass(slots=True)
class AgentState:
    messages: list[ChatMessage] = field(repr=False)
    steps: list[Step] = field(default_factory=list)
    continuation_token: str | None = field(default=None, repr=False)
    export_candidate: ExportCandidate | None = field(default=None, repr=False)

    def start_step(self, index: int) -> Step:
        step = Step(id=uuid4(), index=index, model_call_id=uuid4())
        self.steps.append(step)
        return step

    def record_tool_result(self, step: Step, result: ToolResult) -> None:
        index = len(step.tool_results)
        if index >= len(step.tool_calls) or step.tool_calls[index].id != result.tool_call_id:
            raise RunFailure("internal_error")
        step.tool_results.append(result)
        self.messages.append(
            ChatMessage(role="tool", tool_call_id=result.tool_call_id, content=result.observation_json)
        )
```

`AgentExecutor` 由 `run_api.py` 装配，注入 `definition: AgentDefinition`、现有 `RoutingService`、`ModelService`，以及 `query_retail: Callable[[PreparedRetailCall, RunControl], Awaitable[RetailQueryResult]]`。`emit: Callable[[RunProgress], None]` 属当前请求，写入有界进度队列。`retail_tool_definition()` 与 `prepare_retail_call()` 共用固定参数定义。`RunRegistry` 在 Agent lifespan 装配，`register(owner)` 创建控制器，`lookup(run_id)` 返回活动控制器、终态摘要或空值，`finish(run_id, owner, status)` 原子移除活动项并登记摘要。`RunResult` 复用 `schemas.py` 的终态分支，序列化时追加事件信封。

**评审问题**　核对角色指令、外部依赖和 Run 可变数据的生命周期。每个工具观察只按一个 ID 追加一次，查询原始行只在当前调用与有界导出候选中保留。

## 1. Run 所有权与终态裁决

拟新增 `runtime/control.py` 与 `run_api.py`。当前模型入口以模型调用为生命周期单位。本期 Run 使用自己的控制器和 ASGI 响应，复用模型服务及其容量门。Runner 是结果唯一写入者。请求流、取消路由、期限定时器只可通知控制器。

```python
# 拟议 · runtime/control.py
class RunControl:
    def _mark_deadline(self) -> None:
        if self.stop_reason is None and self.clock() >= self.deadline_at:
            self.stop_reason = "deadline"

    def require_running(self) -> None:
        self._mark_deadline()
        if self.stop_reason is not None or self.terminal is not None:
            raise StopRequested()

    def request_stop(self, reason: StopReason) -> CancelAck:
        if self.terminal is not None:
            return already_terminal(self.id, self.terminal.status)
        if self.stop_reason is None:
            self._mark_deadline()
            self.stop_reason = self.stop_reason or reason
            if self.phase is not None and not self.phase.done():
                self.phase.cancel()
        return accepted(self.id)

    async def perform(self, operation: Callable[[], Awaitable[T]]) -> T:
        # 派发前检查。状态检查与登记在同一事件循环片段完成。
        self.require_running()
        phase_started = False

        async def start_phase() -> T:
            nonlocal phase_started
            self.require_running()
            phase_started = True
            return await operation()

        phase = self.phase = asyncio.create_task(start_phase())
        try:
            result = await phase
            self.require_running()  # 已受理停止时丢弃迟到结果。
            return result
        except PhaseCanceled as error:
            self.cleanup_confirmed &= error.cleanup_confirmed
            raise StopRequested() from None
        except asyncio.CancelledError:
            # 受控 operation 尚未开始时也可能收到取消，此时没有资源句柄。
            # operation 开始后必须自己归还资源或抛出带回执的 PhaseCanceled。
            if phase_started:
                self.cleanup_confirmed = False
            raise StopRequested() from None
        finally:
            self.phase = None

    def commit(self, candidate: RunResult) -> RunResult:
        assert self.terminal is None and self.phase is None
        # 提交前检查。截止时间与终态登记在同一事件循环片段裁决。
        self._mark_deadline()
        if self.stop_reason is not None:
            candidate = stopped_result(self.stop_reason, self.cleanup_confirmed)
        elif not self.cleanup_confirmed:
            candidate = cleanup_unconfirmed_result("execution_error")
        self.terminal = candidate
        self.registry.finish(self.id, self.owner, candidate.status)
        return candidate
```

`phase_started` 用子任务闭包内的布尔标记表示，子任务开始前检查停止。分类、推理、查询、进度等待及导出属于同一 Run。阶段抛出资源未确认错误时，Run 记录确认失败后停止。Runner 统一关闭尚未结束的 Step，最后调用 `commit`。时钟与期限定时器共用事件循环的单调时钟；边界重查覆盖定时器回调尚未获得调度的时点。外部控制不会取消 Runner 自身，避免在连接或模型清理期间重复取消。

```python
# 拟议 · run_api.py
async def cancel_run(run_id, context, registry):
    require_chat_context(context)
    item = registry.lookup(run_id)
    if item is None or item.owner != owner_key(context):
        raise safe_not_found()
    # 活动项和结束摘要使用同一所有者比较，调用间没有 await。
    if isinstance(item, TerminalRunSummary):
        return json_ack(already_terminal(run_id, item.status), status_code=200)
    return json_ack(item.request_stop("user"), status_code=202)
```

`RunStreamResponse` 从一个 ASGI `receive` 任务消费断开通知。启动帧写成功并再次检查控制状态后启动 Runner。监听任务与出站写入独立，断开时向 Run 请求停止并停止出站。若首帧未写出或派发前已停止，响应所有者通过控制器提交零阶段终态并回收注册条目。Agent 自己持有 Runner 的引用，断开后仍等待其清理终态。正常出站从小型有界队列依序发送进度，最后读取 Runner 的终态槽并发送 SSE 结束；心跳用于唤醒间歇性断开的连接，定时器与读写任务均在 `finally` 收尾。进度队列按事件总数上限一次分配容量，单条进度有小体积上限，入队无需等待。队列和结果槽一起受总流预算约束。

**评审问题**　确认启动帧写出前无外部派发；取消与提交在事件循环中的单次决策；无论写端是否被背压，断流都能触发清理。

## 2. 模型接缝与 ReAct 循环

在 `routing/service.py` 将现有分类与选模提为 `select_route`，保持原 API 的输入、事件和响应。内部选择结果同时返回已有 `ProviderView` 的别名、能力与绑定版本，复用该次查询。每次分类仍通过既有授权和逻辑执行，同一 Run 有独立分类调用 ID。Run 路径在路由内各分类调用准备前执行 `control.require_running()`，后续尝试继续沿用模型层的期限和取消。模型阶段在 `models/service.py` 传递 Run 的绝对期限和绑定版本。已有 Repository 事务在锁内验证期望绑定后再创建模型记录。

```python
# 拟议 · llm/contracts.py 与 llm/litellm_chat.py
ToolOutputValidation = Literal["strict", "agent_dispatch"]

def validate_tool_output(request, output, mode: ToolOutputValidation):
    # 适配器先验证原生 ID 非空，再生成公开 ID。空 ID 的哈希仍会是非空字符串。
    calls = output.tool_calls
    if mode == "agent_dispatch" and bool(calls) != (output.finish_reason == "tool_calls"):
        raise RuntimeFailure("provider_protocol_error")
    if not calls:
        return
    if mode == "agent_dispatch" and any(not call.id or not call.function.name for call in calls):
        raise RuntimeFailure("provider_protocol_error")
    if len({call.id for call in calls}) != len(calls):
        raise RuntimeFailure("invalid_tool_arguments")
    if not request.tools:
        raise RuntimeFailure("invalid_tool_arguments")
    if mode == "strict":
        validate_declared_tools_and_arguments(request.tools, calls)
```

`agent_dispatch` 仅通过服务内 `ExecutionRequest` 设置。上游正常结束标志、体积限制、输出类型及原生 ID 校验仍在适配器；原调用保持 `strict` 和已发布的错误映射。对于模式内放行的未知工具或非法参数，续接仍使用适配器密封的原消息，Agent 单独生成安全错误观察。运行入口、Trace 和可见事件均不携带 token。

模型取消在 `ModelService.prepare/run` 内收集可信回执。若还在 `prepare`，记录写入的任务在限时内退出，晚到的已创建记录只补一次取消收尾。模型调用中取消时，等待逻辑执行退出，读取 `RuntimeCancelled.result.cleanup_confirmed` 与既有 attempt 摘要；记录收尾确认后向上抛携带布尔回执的 `ModelInvocationCanceled(asyncio.CancelledError)`。模型等待队列或退避阶段被取消且无未清理尝试时返回已确认。清理超时、尚未退出的资源或未确认的记录收尾返回未确认。现有调用方按 `CancelledError` 处理。Run 在阶段协程内捕获该子类并转换为 `PhaseCanceled`，保留回执；Run 父任务只观察自己的阶段协议。正常返回也检查 `resource_cleanup_failed` 和已有执行摘要。Run Trace 根与子调用共用内容采集关闭标记，原诊断链路保持已有采集策略。

```python
# 拟议 · runtime/executor.py
async def run(
    self, command: AnalysisRunInput, context: TrustedContext,
    control: RunControl, emit: Callable[[RunProgress], None],
) -> RunResult:
    state: AgentState | None = None
    candidate = failed("internal_error")
    try:
        route = await control.perform(lambda: self.select_run_route(command, context, control))
        state = AgentState(context_provider.initial_messages(self.definition, command, route))
        emit_route(emit, route)
        for index in range(1, MAX_STEPS + 1):
            step = state.start_step(index)
            emit_step_started(emit, step)
            request = context_provider.generate_input(self.definition, state, route)
            model = await control.perform(lambda: self.invoke_model(step, request, route, control))
            require_complete_generation(model)
            if not model.tool_calls:
                # 校验成功候选，统一交给控制器裁决后再发布。
                control.require_running()
                candidate = completed(validate_final_answer(model.text), export_candidate(state))
                finish_step(emit, step, "completed")
                break
            validate_batch(model.tool_calls, max_calls=MAX_TOOLS_PER_STEP)
            if index == MAX_STEPS:
                raise RunFailure("step_limit")
            step.tool_calls = tuple(model.tool_calls)
            state.messages.append(
                ChatMessage(role="assistant", content=model.text, tool_calls=model.tool_calls)
            )
            state.continuation_token = require_continuation(model)
            for tool_index, call in enumerate(model.tool_calls, start=1):
                # 同一批也逐条检查；校验通过的查询再进入 perform。
                control.require_running()
                prepared = prepare_retail_call(call, route.retail_binding)
                emit_tool_started(emit, step, tool_index, call.id, prepared.safe_params)
                outcome = (
                    retail_failure(prepared.safe_error)
                    if prepared.safe_error is not None
                    else await control.perform(lambda: self.query_retail(prepared, control))
                )
                control.require_running()
                result = summarize_tool_result(call.id, outcome)
                state.record_tool_result(step, result)
                state.export_candidate = select_export_candidate(
                    state.export_candidate, step.id, call.id, outcome
                )
                emit_tool_finished(emit, step, tool_index, call.id, result.summary)
            finish_step(emit, step, "completed")
    except StopRequested:
        close_open_step(emit, state, "canceled")
    except RunFailure as error:
        close_open_step(emit, state, "failed")
        candidate = failed(error.code)
    except Exception:
        close_open_step(emit, state, "failed")
        candidate = failed("internal_error")
    # 唯一终态出口，提交前再次检查截止时间与清理状态。
    return control.commit(candidate)
```

`require_complete_generation` 检查结果状态、结束原因、内容及清理回执。 `close_open_step` 在分类失败或尚未创建 Step 时直接返回。 `prepare_retail_call` 先比较固定工具名，再做严格 JSON、日期及维度校验，最后核对部署绑定。三个拒绝分支均携原 ID 且不创建连接。合法的查询失败和空结果按观察结果写回。未确认的清理通过阶段异常退出。所有工具的观察消息按批次完整追加后才发下一轮。第六轮若继续请求工具，直接按步数上限失败，省掉无法用于后续模型回答的查询。

**评审问题**　核对路由分类和同批每条工具调用都经过派发前检查。控制器在候选答案准备后重新裁决停止、期限和清理状态。工具失败观察须按原 ID 进入下一轮，并回归原模型接口及续接前缀。

## 3. Retail 查询与清理

`runtime/retail_sql.py` 保持两段固定查询。下面展示商品查询的主体；渠道汇总去掉 SKU / SPU 关联和 channelCode 过滤，改按渠道编码聚合。日期、租户和渠道通过 Psycopg 绑定。单段时传入显式 date 空值，不匹配比较分支。

```sql
WITH latest AS (
    SELECT DISTINCT ON (d.tenant_id, d.report_date, d.channel_id, d.sku_id, d.region_id)
        d.tenant_id, d.report_date, d.channel_id, d.sku_id, d.region_id, d.paid_amount_cent
    FROM core.channel_product_daily AS d
    JOIN core.channel AS ch USING (tenant_id, channel_id)
    WHERE d.tenant_id = %(tenant)s AND ch.channel_code = %(channel)s
      AND ((d.report_date >= %(start)s AND d.report_date < %(end)s)
        OR (d.report_date >= %(compare_start)s::date AND d.report_date < %(compare_end)s::date))
    ORDER BY d.tenant_id, d.report_date, d.channel_id, d.sku_id, d.region_id, d.report_version DESC
), totals AS (
    SELECT p.spu_code, p.spu_name,
           COALESCE(SUM(d.paid_amount_cent) FILTER (WHERE d.report_date >= %(start)s AND d.report_date < %(end)s), 0) AS paid,
           COALESCE(SUM(d.paid_amount_cent) FILTER (WHERE d.report_date >= %(compare_start)s::date
                                                   AND d.report_date < %(compare_end)s::date), 0) AS compared
    FROM latest AS d
    JOIN core.sku AS k USING (tenant_id, sku_id)
    JOIN core.spu AS p USING (tenant_id, spu_id)
    GROUP BY p.spu_code, p.spu_name
    HAVING SUM(d.paid_amount_cent) <> 0
)
SELECT spu_code, spu_name, paid,
       CASE WHEN %(compare_start)s::date IS NOT NULL THEN compared END AS compared,
       CASE WHEN %(compare_start)s::date IS NOT NULL THEN compared - paid END AS delta
FROM totals
ORDER BY CASE WHEN %(compare_start)s::date IS NULL THEN paid ELSE ABS(compared - paid) END DESC,
         spu_code COLLATE "C"
LIMIT 201;
```

运行查询期间，外层任务拥有连接、游标和原命令 task。原命令由 shield 保护，取消运行阶段只打断外层等待，让 cleanup 能读到数据库端命令结果。使用单独的显式 `BEGIN READ ONLY` 命令建立事务，命令之间重查 Run，避免在终止后从隐式事务准备进入实际业务查询。

```python
# 拟议 · runtime/retail.py
async def query(prepared, control):
    conn = cursor = command = None
    connecting = asyncio.create_task(open_retail_connection(prepared.binding, control.id))
    outcome = None
    interrupted = False
    try:
        conn = await asyncio.shield(connecting)
        control.require_running()
        cursor = conn.cursor()
        command = asyncio.create_task(cursor.execute("BEGIN READ ONLY"))
        await asyncio.shield(command)
        control.require_running()
        command = asyncio.create_task(check_retail_identity(cursor, prepared.binding))
        await asyncio.shield(command)
        control.require_running()
        command = asyncio.create_task(fetch_bounded(cursor, prepared.statement, prepared.values))
        outcome = await asyncio.shield(command)
    except (asyncio.CancelledError, StopRequested):
        interrupted = True
    except (RetailIdentityMismatch, psycopg.Error) as error:
        outcome = safe_retail_failure(error)
    finally:
        receipt = await settle_retail(connecting, conn, cursor, command, interrupted)
    if interrupted:
        raise PhaseCanceled(cleanup_confirmed=receipt.confirmed)
    if not receipt.confirmed:
        raise ResourceUnconfirmed("retail")
    return outcome
```

`open_retail_connection` 设置 3 秒连接限时，校验 libpq 能力，通过 `autocommit=True` 配合显式事务。连接未交给查询时仅清理结果句柄。每条命令在自己的 task 开始时检查停止，身份预检通过后才派发业务 SQL。`fetch_bounded` 只发送一条 SELECT，剩余操作读取已有结果。源端超时适用于事务内 SQL。 `settle_retail` 在独立、受控的清理任务中按绝对期限处理：

1. 连接未建立时等待连接阶段在期限内结束，随后关闭其结果句柄。若时间耗尽，保留晚到关闭回调并标记未确认。
2. 停止时，若已有命令在执行，尝试 `cancel_safe(timeout=1)`，随后等待原命令完成。取消请求失败时仍等待原命令结果。 `QueryCanceled`、正常完成或服务端明确终止错误，并且连接状态回到已知事务状态，才继续清理；本地网络异常或未知事务状态不确认退出。
3. 关闭游标，若存在显式事务则执行 `ROLLBACK`，确认事务为 `IDLE`，关闭连接。命令挂住或回滚失败时关闭本地句柄并标记未确认。消费旧命令的结果或异常，不再复用该连接。清理回执包含安全原因与阶段，数据库错误文本留在驱动边界。

**评审问题**　确认 RLS 在日汇总事实表上生效，关联使用 tenant 键，取消只作用于当前调用的专用后端；故障连接与下一次查询隔离。

## 4. BFF 与页面提交

在现有 catch-all Node Route Handler 分发分析端点。BFF 复用 `models.authorize`、同源/CSRF 检查及私有请求头。授权事务结束后调用 Agent。两段共用 `compass.run` v1 包信封及逻辑事件契约。完整 DTO、常量、分帧器、顺序校验和投影算法见[流式数据包协议](STREAM_PROTOCOL.md)及[可运行参考](stream-reference/stream.ts)。

以下片段定位 BFF 与页面的调用接缝。BFF 实际接入时复用 195 秒期限，在取消及错误路径终止上游 fetch。`DocumentSession` 负责页面退场、权限版本和下载清理。

```ts
// 拟议 · server/analysis-agent-client.ts 和 client/analysis-run.ts
const output = await projectBody(upstreamBody, {
  requestId,
  parse: parseRunPacket,
  signal: upstreamAbort.signal,
  batch: false,
}); // 首包通过校验后才构造 HTTP 200 SSE 响应。

for await (const { runId, event } of readRun(responseBody, {
  requestId,
  parse: parseRunPacket,
  signal: turnAbort.signal,
})) {
  if (event.type === "run.finished") {
    commitTurn(active, runId, event.payload); // readRun 已确认干净 EOF。
  } else {
    applyRunEvent(active, runId, event); // 批次已展开并完整校验。
  }
}
```

页面按 Run、Step 和 ToolCall 的业务标识更新卡片。包序号只用于当前连接的完整性校验。BFF 可以为安全进度攒包、过滤或转译已登记的可选扩展，随后重新编号。页面的 Run 提交点与传输形态独立。

```ts
// 拟议 · client/analysis-run.ts
function onStarted(active: ActiveTurn, runId: string) {
  if (!isCurrent(active)) return;
  active.runId = runId;
  if (active.stopIntent) void sendCancelOnce(active);
}

async function sendCancelOnce(active: ActiveTurn) {
  if (!isCurrent(active) || active.terminal || active.stopInFlight || active.stopAccepted) return;
  active.stopIntent = true;
  showStopping(active);
  if (!active.runId) return;
  active.stopInFlight = true;
  try {
    const ack = await cancelRun(active.runId, active.scope);
    if (!isCurrent(active) || active.terminal) return;
    active.stopAccepted = ack.status === "accepted" || ack.status === "already_terminal";
  } catch {
    if (isCurrent(active) && !active.terminal) showStopUnconfirmed(active);
  } finally {
    active.stopInFlight = false;
  }
}

function commitTurn(active: ActiveTurn, runId: string, event: RunResult) {
  if (!isCurrent(active) || active.terminal || runId !== active.runId) return;
  active.terminal = event.status;
  if (event.status === "completed") {
    addCompletedHistory(active.question, event.finalAnswer);
    showAnswerAndFile(active, event.finalAnswer, event.file);
    collapseProcessOnce(active);
  } else {
    showFailedOrCanceledTurn(active, event);
  }
}
```

页面在原流中断后封存本轮，取消无用的控制请求，清空临时回答和文件，只保留安全过程及失败/待确认状态。取消请求收到 404 时保留当前页面用于等待原流，权限失效仍由 `DocumentSession` 统一退场。文件 Blob 只在成功后建立，使用 `DocumentSession` 注册的短寿命对象 URL，重置和退场共同撤销。历史存放问题与最终回答，页面活动轮通过本地代际与 Run ID 双重匹配避免迟到事件污染下一轮。

**评审问题**　用乱序、非法文件、断流和迟到停止回执验证页面只有一个提交点，并检查已完成的问答与文件在权限退场时立即清理。
