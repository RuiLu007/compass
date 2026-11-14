# 第 17 课 · 混合 Workflow 关键代码设计

[返回技术方案](TECH_DESIGN.md) · [有效 PRD](../spec/PRD.md)

下列片段按已实现的调用链整理。JS 为示例，Python 为节选或带说明的伪代码。可信身份、Retail 读取、验证和 Run 收口复用现有模块。

## 1. Dynamic Workflow · JS 控制流与独立 SubAgent

### 1.1 生成后可保存的源码

以下是 Agent 可能生成的脚本。`args` 传本次两段日期。子任务收到角色和结构化入参，实际提示由可信 profile 根据这些入参构造。证据引用只在本次工作流执行内有效。

```javascript
const lead = await agent("channel_compare", args);
if (lead.status !== "ok") {
  return { status: "needs_data", evidenceRefs: lead.evidenceRefs };
}

const input = {
  baseline: args.baseline,
  current: args.current,
  channelCode: lead.channelCode,
  leadEvidenceRef: lead.evidenceRef,
};
const [products, review] = await parallel([
  () => agent("product_breakdown", input),
  () => agent("scope_review", input),
]);

if (products.status !== "ok" || review.status !== "ok") {
  return {
    status: "needs_review",
    evidenceRefs: [lead.evidenceRef],
  };
}

return {
  status: "ok",
  evidenceRefs: [lead.evidenceRef, products.evidenceRef, review.evidenceRef],
};
```

`parallel` 接收惰性函数列表，由 Runtime 一起发起并按输入顺序返回。独立 SubAgent 之间只传脚本显式选取的参数和同一次执行的证据引用。脚本返回 JSON 后，Agent Host 根据 `channel_attribution@1` 校验引用归属、任务角色、渠道、日期以及复核状态，再用可信证据构造结果。

### 1.2 Python Host 的运行与收据

以下伪代码概括 `HybridWorkflows.dynamic` 与 JS runner 的职责，收据保留实际执行的源码、入口契约、本次日期和核验结果。

```python
async def run_dynamic_workflow(request: NewScript | SavedScriptRef, parent):
    parent.require_running()
    source = await scripts.resolve(request, scope=parent.scope, actor=parent.actor_id)
    contract = entry_contracts.require(source.entry_contract, source.runner_api_version)
    args = contract.validate_args(request.args)

    async def run_isolated():
        async with WorkflowExecution(parent, source, contract) as workflow:
            value = await workflow.run_script(args)
            return workflow.id, workflow.verify_final_result(value)

    execution_id, result = await parent.control.perform(run_isolated)
    if source.is_new and result.status == "ok":
        parent.workflow_receipts.put_verified_source(execution_id, source)
    return result


async def dispatch_workflow_agent(frame: AgentRequest, workflow):
    profile = workflow.contract.require_profile(frame.profile)
    data = profile.validate_input(frame.input, workflow.evidence, workflow.args)
    async with workflow.admit_child(frame.request_id, profile.budget) as child:
        # Child 创建全新的 AgentState、Verifier 和子阶段控制。
        result = await child.run(profile.task_from(data), allowed_tools=profile.tools)
        return workflow.verify_child_result(child.id, profile, result)
```

`resolve` 对新脚本校验 UTF-8 和字节上限，统一换行。对资产读取按所有者和当前工作区过滤，核验启用状态、版本与摘要。`admit_child` 原子预留父预算，检查停止状态、子任务总量和并发。`WorkflowExecution` 离开作用域前停止派发，收回待完成子任务和脚本进程。保存成功的只是源码、版本和业务契约；记录位置见[技术方案](TECH_DESIGN.md)。

### 1.3 Chat 确认后保存

以下片段对应 `runtime/executor.py` 的 Run 收口与 `workflow_asset_api.py` 的确认路由。前序 `submit_workflow_report` 已检查收据归属、新编排来源和核验状态，符合条件才会设置 `save_offer`。

```python
result = control.commit(candidate)
hybrid = state.hybrid if state is not None else None
if result.status == "completed" and self.workflow_drafts and hybrid and hybrid.save_offer:
    draft_id = self.workflow_drafts.offer(context, hybrid.save_offer)
    hybrid.event("dynamic", "save_offer", "completed", str(draft_id))


@router.post("/drafts/{draft_id}/confirm", response_model=WorkflowAssetView)
def confirm_draft(
    draft_id: UUID,
    value: WorkflowDraftConfirm,
    context: RunContext,
    assets: Assets,
    drafts: Drafts,
    response: Response,
) -> WorkflowAssetView:
    response.headers["Cache-Control"] = "no-store"
    draft = drafts.get(context, draft_id)
    return assets.save(context, value.name, draft.script_source, draft.entry_contract)
```

`WorkflowDrafts.get` 用当前可信主体校验邀请归属与有效期。确认只允许提交名称，数据库用已核验源码及契约去重。SSE 只携带邀请 ID，详情由 Chat 按需读取。

### 1.4 Node runner 与 QuickJS 的 Promise 边界

```javascript
const runtime = quickJS.newRuntime();
runtime.setMemoryLimit(limits.guestBytes);
runtime.setMaxStackSize(limits.stackBytes);
runtime.setInterruptHandler(() => performance.now() >= executionDeadline);
const vm = runtime.newContext();

// Trusted bootstrap installs args, agent and parallel in this context.
const pending = new Map();
function requestAgent(profile, input) {
  const requestId = nextRequestId();
  const reply = vm.newPromise();
  pending.set(requestId, reply);
  writeBoundedFrame({ type: "agent", requestId, profile, input });
  return reply.handle;
}

function receiveResult(frame) {
  const reply = takeExpectedResponse(pending, frame.requestId);
  settleGuestPromise(vm, reply, frame);
  pumpPendingJobs(runtime, limits.jobsPerTurn);
}
```

`requestAgent` 通过受信 JS bridge 注册给 guest。`pumpPendingJobs` 按有限批量驱动 Job Queue，在批次间归还事件循环以接收结果，并受运行截止时间约束。`settleGuestPromise` 只导入限长 JSON 数据，按结果状态 resolve 或 reject。宿主按请求 ID 验证回包并拒绝重复或未知 ID。Runner 退出时释放句柄、VM 和进程。上面的片段省略桥接序列化和进程入口，校验、异常与清理在实施时纳入双向协议验收。

### 1.5 资产注册为模型工具

`runtime/workflow_catalog.py` 的 `RegisteredWorkflow` 从资产 ID、名称、契约与版本生成工具定义，输入 schema 使用 `WorkflowPeriods`。目录按每次 Run 的可信主体装配，模型根据用途选择具名工具。以下为当前核心链路的节选，省略分页和通用工具装配。

```python
@dataclass(frozen=True)
class RegisteredWorkflow:
    asset_id: UUID
    name: str
    definition_version: Literal[1]
    entry_contract: EntryContract

    @property
    def tool_name(self) -> str:
        return f"workflow_{self.asset_id.hex}_v{self.definition_version}"


async def load_catalog(self) -> str:
    page = await self.asset_operation(lambda: self.assets.list(self.context, active=True))
    self.register(page.items)
    return catalog_prompt(list(self.registered.values()), page.next_cursor)


async def invoke_registered(self, call: ToolCall):
    # 从当前 Run 的注册映射取身份，模型只传业务参数。
    registered = self.registered[call.function.name]
    periods = WorkflowPeriods.model_validate_json(call.function.arguments)
    return await self.dynamic(
        ReuseScript(
            assetId=registered.asset_id,
            definitionVersion=registered.definition_version,
            args=periods,
        )
    )
```

`invoke_registered` 在实现中合并于 `HybridWorkflows.tool`。`dynamic` 执行前重新 `get(context, asset_id, active=True)`，校验定义版本及源码摘要，随后运行原 JS。`tool_definitions()` 把具名工作流加入当前模型的 `GenerateInput.tools`。系统目录包含能力描述和分页游标，首批直接工具为 3 个，其他目录项可通过 `reuse_workflow` 调用或经查找提升到下一轮工具列表。注册在模型调用前完成，已有资产无需重新保存。

## 2. 程序固定 Workflow · 两个入口共用一套节点

`IntentEntry` 由服务端意图路由创建。`ToolEntry` 由工具注册表解析，接受同一 Run 的证据引用。浏览器和模型都只提供输入，固定图和课堂活动上限取自服务端。

以下伪代码对应 `HybridWorkflows.budget_proposal`，展示两个入口的证据分支和共同待审出口。

```python
async def run_budget_proposal(periods, refs, workflow):
    require_configured_scope_and_verified_todos(workflow)
    async with asyncio.TaskGroup() as group:
        group.create_task(check_binding(workflow))
        group.create_task(check_periods(periods))

    if refs is None:
        channel = await workflow.child("channel_compare", params_for("channel_compare", periods))
        evidence = [channel]
    else:
        evidence = read_unique_verified_refs(workflow.run.verifier, refs)

    channel = validate_channel_and_product_evidence(evidence, periods)
    require_within_limit(FIXED_AMOUNT_CENT, FIXED_MAX_CENT)
    if refs is not None:
        require_decline(channel, FIXED_DECLINE_THRESHOLD)

    workflow.control.require_running()
    wait_id = uuid5(workflow.control.id, "budget_proposal@1:budget_decision")
    return RunWaiting(
        status="waiting",
        runId=str(workflow.control.id),
        waitId=str(wait_id),
        proposal=build_classroom_proposal(channel, evidence, wait_id),
    )
```

直接入口的渠道 SubAgent 实际调用 `retail_sales`。工具入口复用当前 Run 的证据并检查下滑阈值。两条路径成功后由外层 Run 提交待审终态。

## 3. Workflow in Skill · 自主判断与脚本调用约定

下面是随 Agent 打包的 `SKILL.md` 内容。

```markdown
# 每周经营复盘

用户要求例会周报或标准复盘时使用。按需求确定基准周和本周，日期不清楚时先澄清。
分别调用 retail_sales 查询两段单期渠道数据，compareTo 设为 null，结束日不含当日。
查询后调用 build_weekly_review，传入 baseline 和 current。使用脚本收据调用 submit_weekly_review。
若某周查无数据，同样运行脚本以确认缺口。说明可核对的结果，跟踪建议以脚本返回的变化和缺口为依据。
```

脚本输入由工具按两段日期和当前 Run 的查询状态构造。已有渠道查询分别按单期执行，有数据的窗口从 Verifier 获取行；查无数据的窗口从服务端查询状态取得覆盖标记。失败或从未查询的窗口由工具报错。这样一周缺数时，另一周已有验证的结果仍可使用。

```python
from datetime import date


def build_weekly_review(baseline: dict, current: dict) -> dict:
    windows = (baseline, current)
    boundaries = tuple(
        (date.fromisoformat(window["start"]), date.fromisoformat(window["end"]))
        for window in windows
    )
    if (
        any((end - start).days != 7 for start, end in boundaries)
        or boundaries[0][1] > boundaries[1][0]
    ):
        raise ValueError("invalid_week_windows")
    available = [window for window in windows if window["covered"]]
    totals = [
        {"start": window["start"], "end": window["end"], "paidCent": sum(window["rows"].values())}
        for window in available
    ]
    if len(available) < 2:
        return {
            "status": "needs_data",
            "totals": totals,
            "missing": [
                {"start": window["start"], "end": window["end"]}
                for window in windows
                if not window["covered"]
            ],
        }
    before, after = (window["rows"] for window in windows)
    changes = sorted(
        ((code, after.get(code, 0) - before.get(code, 0)) for code in before.keys() | after.keys()),
        key=lambda item: (-abs(item[1]), item[0]),
    )
    return {"status": "ok", "totals": totals, "changes": changes[:3]}
```

现有 Retail 查询以授权范围内有记录定义单期覆盖。脚本复用该语义。金额统一用整数分，某渠道在一个有覆盖的周期内未出现在返回行时计 0。工具使用当前 Run 的绑定、证据与输入行上限，按 Skill 版本登记脚本结果。工具从脚本输出渲染三段式周报，Agent 引用收据提交。

## 统一执行过程节点

以下为公共合同的阅读骨架；完整字段真源为 `apps/agent/src/compass_agent/schemas.py` 的 `RunExecutionNode`。TS 类型由契约生成，前端不另维护枚举。

```typescript
type ExecutionNodeShape = {
  id: string;
  parentId?: string | null;
  revision: number;
  kind: "model" | "narration" | "tool" | "workflow" | "skill" | "subagent" | "group";
  status:
    | "preparing"
    | "running"
    | "completed"
    | "empty"
    | "failed"
    | "canceled"
    | "waiting"
    | "incomplete";
  title: string;
  summary?: string | null;
  startedAtMs: number;
  endedAtMs?: number | null;
  origin?: { kind: "builtin" | "mcp"; serverId?: string; serverName?: string; toolName?: string };
  executor?: { kind: "service" | "quickjs" | "sandbox"; runtime?: string; environmentId?: string };
  scheduling?: "sequential" | "parallel";
  dependsOn?: string[];
  display?: {
    policy: "summary" | "preview";
    inputPreview?: string;
    outputPreview?: string;
    language?: string;
  };
};
```

Workflow、Skill 和 SubAgent 的类型化元数据以及 Step / Model / Tool 关联字段见主方案第 7 节。`ExecutionTrace` 只接收允许公开的内容；工具原始 arguments 和 observation 留在执行侧。前端节点顺序校验的代表性逻辑见 `apps/web/src/contracts/execution-node.ts`，公开组件见 `apps/web/src/components/ui/execution-tree.tsx`。
