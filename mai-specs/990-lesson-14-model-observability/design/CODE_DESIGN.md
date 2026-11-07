# 第 14 课：供评审的代码设计

[返回技术方案](TECH_DESIGN.md)

> 以下片段保留设计职责示意，用于阅读类型、所有权和关键分支。本期实现已将计时、终态与回执收敛为 [Observation](../../../apps/agent/src/compass_agent/observability/observer.py)，实际 API 与测试以源码和[实施记录](../IMPLEMENTATION.md)为准。学员阅读版使用当前实现收敛后的片段。

## 1. 模块与依赖

```text
apps/agent/src/compass_agent/
├── observability/                         [新增] 只接收安全元信息
│   ├── observer.py                       [新增] 关联编号、计时、回执、终态、父子关系与 W3C carrier
│   ├── privacy.py                        [新增] 元信息白名单与安全日志过滤
│   ├── export.py                         [新增] OTel 装配、出口校验、导出自诊断
│   ├── summary.py                        [新增] 固定模型对比与 Langfuse 公开 API 查询
│   ├── litellm_receipt.py                 [新增] 动态函数回调，回执无网络
│   └── README.md                         [新增] 模块职责与字段维护入口
├── main.py                               [调整] lifespan 装配/关闭
├── settings.py                           [调整] 服务端启用、采样与目标配置
├── model_api.py                          [调整] 根观测、响应头、管理状态 API
├── routing/service.py                    [调整] 分类/路由/主模型关联
├── models/service.py                     [调整] prepare 到 finish 的逻辑阶段
└── llm/
    ├── executor.py                       [调整] 取槽、退避与实际 Attempt
    ├── runtime.py                        [调整] 结果/清理安全提取
    ├── litellm_chat.py                    [调整] 安全回调和标准用量，不自动导出
    └── systemone_decision.py              [复用] 原类型化决策结果，由共同执行路径提取安全用量
apps/web/src/
├── server/http/model-handler.ts          [调整] 平台状态路由与权限
├── server/model-agent-client.ts          [调整] 安全透传 Trace 关联头
├── features/models/
│   ├── observability-panel.tsx           [新增] 平台入口与进程状态
│   ├── use-model-call.ts                 [调整] 先读取头，取消保留关联编号
│   ├── output.tsx / types.ts             [调整] 编号展示及类型
│   └── pages.tsx                         [调整] 平台观测小节
├── components/analysis-composer.tsx      [调整] 本轮关联信息入口
└── generated/                            [生成] 新状态 DTO
contracts/                                [生成] 从 Agent DTO 生成
apps/agent/pyproject.toml / uv.lock         [调整] OTel 同版本锁定
apps/agent/tests/test_observability.py      [新增] 隐私、采样、状态、生命周期
apps/agent/tests/test_observability_integration.py [新增] 后端/金标验证
apps/agent/scripts/verify_observability.py  [新增] Langfuse 公开查询金标，非后台服务
apps/web/tests/domain/model-bff.test.ts    [调整] 头与状态权限
apps/web/tests/e2e/                         [调整] 取消、复制与管理员入口
scripts/observability.mjs / 对应测试       [新增] 本地栈初始化与管理
package.json                              [调整] 拟交付 observability 命令
.env.example                              [调整] 无真实凭据的配置说明
README.md / apps/{agent,web}/README.md     [调整] 新能力、边界与真实入口
contracts/README.md / scripts/README.md    [调整] 契约与命令
 deploy/compose/compose.yaml               [调整] Agent 可选私有 env_file
 deploy/compose/observability.yaml          [新增] Langfuse 六服务 overlay/profile
 deploy/observability/README.md             [新增] 固定源、私有配置、卷与恢复
 deploy/README.md                          [调整] 观测栈入口与现有五应用关系
```

`ModelService` / Executor 只依赖观测门面，不依赖 Langfuse SDK。观测门面依赖 OTel；Langfuse 特有映射集中在 `export.py` / `privacy.py`。没有跨 app 源码导入、业务库变更或新共享包。

## 2. 安全类型与出口规则

设计中的白名单以字段语义构造。`SafeFields` 不接收任意 kwargs；映射字段明细以主方案 §2.2 为准。以下展开最容易泄露的错误/状态与数值路径，其余固定枚举字段同样逐项登记。

```python types.py
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from threading import Lock
from typing import Literal

class Terminal(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"

@dataclass(frozen=True, slots=True)
class TraceRef:
    trace_id: str
    sampled: bool

@dataclass(frozen=True, slots=True)
class SafeResult:
    status: Terminal
    error_code: str | None
    sent: bool
    outcome_unknown: bool
    cleanup_confirmed: bool
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None

SAFE_ERRORS = frozenset({
    "model_rate_limited", "model_timeout", "model_unavailable_upstream",
    "stream_interrupted", "record_write_failed", "runtime_overloaded",
    "resource_cleanup_failed", "provider_protocol_error", "internal_error",
})

def safe_error(code: str | None) -> str | None:
    # 实施使用既有完整安全错误枚举，未知值归一；不传异常对象。
    return None if code is None else code if code in SAFE_ERRORS else "internal_error"

def token_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 2**53 - 1 else None

class AttemptReceipt:
    """唯一写入安全回执；回调闭包持有该对象，不持有请求或模型响应。"""
    def __init__(self):
        self._lock = Lock()
        self._closed = False
        self._usd = None

    def accept_cost(self, value: object) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        cost = float(value)
        if not isfinite(cost) or cost < 0:
            return
        with self._lock:
            if not self._closed:
                self._usd = cost

    def freeze(self) -> float | None:
        with self._lock:
            self._closed = True
            return self._usd
```

出口只接受本门面手工创建的 span 属性。结构化 usage/cost JSON 也从有限数值生成；不对任意对象 JSON 序列化。错误枚举实施时引用既有真源，避免建立另一份业务错误维护点。

```python privacy.py
import json
from math import isfinite

CONTROLLED_KEYS = frozenset({
    "compass.status", "compass.error_code", "compass.call_id",
    "compass.parent_call_id", "compass.attempt_id", "compass.stage",
    "compass.tenant_id", "compass.workspace_id", "compass.actor_id",
    "compass.provider_id", "compass.model", "compass.adapter",
    "compass.config_version", "compass.binding_version",
    "compass.dictionary_version", "compass.routing_version",
    "compass.reason", "compass.category_id", "compass.scene_id",
    "compass.difficulty", "compass.mode", "compass.purpose",
    "compass.sent", "compass.outcome_unknown", "compass.cleanup_confirmed",
    "compass.first_visible_ms", "compass.queue_wait_ms",
    "compass.usage_complete", "compass.unknown_usage_attempts",
    "compass.cost_state", "compass.cost_source", "compass.simulation",
    "compass.known_input_tokens", "compass.known_output_tokens",
    "compass.known_cost_usd", "compass.reasoning_tokens",
    "langfuse.observation.type", "langfuse.observation.level",
    "langfuse.observation.status_message", "langfuse.observation.model.name",
    "langfuse.observation.usage_details", "langfuse.observation.cost_details",
    "langfuse.observation.completion_start_time", "langfuse.trace.name",
    "langfuse.environment", "langfuse.release",
})

def acceptable_attributes(attrs: dict) -> bool:
    if len(attrs) > 64 or not set(attrs).issubset(CONTROLLED_KEYS):
        return False
    for key, value in attrs.items():
        if type(value) not in {str, bool, int, float}:
            return False
        if isinstance(value, str) and len(value) > 256:
            return False
        if key.endswith("usage_details") or key.endswith("cost_details"):
            try:
                data = json.loads(value)
            except (ValueError, TypeError):
                return False
            allowed = {"input", "output"} if key.endswith("usage_details") else {"total"}
            if not isinstance(data, dict) or not set(data).issubset(allowed):
                return False
            if any(type(x) not in {int, float} or not isfinite(x) or x < 0 for x in data.values()):
                return False
    return True
```

`isfinite` 使用标准库；主方案登记的完整集合在实现单一模块中维护，事件也仅允许固定名称（retry/first_visible/canceled）与同一安全字段，禁止 exception event；不自动挂 baggage。资源属性是单独的 service/version/environment 固定集合。

### Langfuse 顶层 metadata 映射

安全键先在内部登记，导出时替换为 Langfuse 可筛选的顶层元信息，不同时保留两套属性。

```python metadata.py
from math import isfinite

def langfuse_fields(attrs: dict, *, is_root: bool) -> dict:
    if not acceptable_attributes(attrs):
        raise ValueError("unsafe_telemetry")
    out = {}
    for key, value in attrs.items():
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("unsafe_telemetry")
        if key.startswith("compass."):
            leaf = key.removeprefix("compass.")
            out["langfuse.observation.metadata." + leaf] = value
        else:
            out[key] = value
    if is_root and "compass.call_id" in attrs:
        out["langfuse.trace.metadata.call_id"] = attrs["compass.call_id"]
    if len(out) > 64:
        raise ValueError("unsafe_telemetry")
    return out
```

出口检查使用由 CONTROLLED_KEYS 推导的上述映射键集合；不接受任意以 metadata 开头的属性。内部自定义业务键均转换为顶层 metadata，状态/模型筛选不依赖 catch-all 的嵌套 attributes。

## 3. 根与逻辑阶段所有权

内部观测作用域必须贯穿原异步根任务。以下是拟议关键集成形状；`observer.root/logical` 是本模块的上下文管理器，默认终态 failed；调用 `complete` 更新安全终态，退出时只 end 一次。`CancelledError` 原样传播，`record_exception=False`，不得用自动异常捕获写入 Trace。

```python orchestration.py
import asyncio

async def observed_root(observer, command, context, execute):
    # 建立后再启动原 root_task；SSE generator 也持有同一个 scope。
    with observer.root(command.call_id, context) as root:
        try:
            result = await execute(root)
        except asyncio.CancelledError:
            root.complete_if_unset(status="canceled", error_code=None)
            raise
        except ModelError as exc:
            root.complete_if_unset(status="failed", error_code=safe_error(exc.code))
            raise
        except Exception:
            root.complete_if_unset(status="failed", error_code="internal_error")
            raise
        else:
            root.complete_if_unset(status=result.status, error_code=safe_error(result.error_code))
            return result

def correlation_headers(ref: TraceRef | None) -> dict[str, str]:
    if ref is None:
        return {}
    return {
        "X-Compass-Trace-Id": ref.trace_id,
        "X-Compass-Trace-Sampled": "1" if ref.sampled else "0",
    }

async def observed_stage(root, role, models, selected, prepare_args, value, policy, emit):
    with root.logical(role) as logical:
        logical.bind_selected(selected.provider_id, selected.model)
        try:
            prepared = await models.prepare(*prepare_args)
            logical.bind_provider(prepared.spec)
            # ModelService.run 原方法在 _finish 后才返回；finally 中的取消也先收尾。
            result = await models.run(
                prepared, value, emit=emit, logical_policy=policy,
                observation=logical,
            )
        except asyncio.CancelledError:
            logical.complete_if_unset(status="canceled", error_code=None)
            raise
        except ModelError as exc:
            logical.complete_if_unset(status="failed", error_code=safe_error(exc.code))
            raise
        except Exception:
            logical.complete_if_unset(status="failed", error_code="internal_error")
            raise
        else:
            logical.complete_if_unset(status=result.status, error_code=safe_error(result.error_code))
            return result
```

ModelService.run 的取消分支可能在抛 CancelledError 前已发生 `_finish` 失败；因此它还需在其 finally 收尾后调用 `observation.final_state(result)`，把 record_write_failed 和 canceled 的实际优先级传回作用域。外层取消处理只有未收到 final_state 时才补 canceled。prepare 失败同样由此作用域覆盖。观测 API 所有方法内部包裹 `Exception` 并计固定失败计数，返回 NoOp/缺失值，不吞取消和不改业务结果。

### 最终状态的优先级

ModelService 是逻辑阶段最终状态的所有者。下列代码使外层取消只补缺，服务收尾结果发布后不可被覆盖；end_once 对同一 span 幂等。

```python terminal.py
from threading import Lock

class ScopeTerminal:
    def __init__(self, span, root=None):
        self.span = span
        self.root = root
        self.lock = Lock()
        self.value = None
        self.authoritative = False
        self.ended = False

    def complete_if_unset(self, *, status, error_code):
        with self.lock:
            if self.value is None and not self.ended:
                self.value = (status, safe_error(error_code))

    def final_state(self, result):
        with self.lock:
            if not self.ended and not self.authoritative:
                self.value = (result.status, safe_error(result.error_code))
                self.authoritative = True
        # 已输出的内容不能掩盖记录失败；父入口即使收到取消也要保留该错误。
        if result.error_code == "record_write_failed" and self.root is not None:
            self.root.publish_failure("record_write_failed")

    def publish_failure(self, code):
        with self.lock:
            if not self.ended:
                self.value = ("failed", safe_error(code))
                self.authoritative = True

    def end_once(self):
        with self.lock:
            if self.ended:
                return
            self.ended = True
            status, code = self.value or ("failed", "internal_error")
        try:
            self.span.set_attribute("compass.status", status)
            if code is not None:
                self.span.set_attribute("compass.error_code", code)
            self.span.end()
        except Exception:
            # 观测自己的失败只更新安全本地计数；原业务异常/返回值保持。
            return
```

ModelService 在既有 `_finish(result, sent)` 返回后的 finally 中调用 `observation.final_state(result)`，外层 observed_stage 不得重复推断已收尾状态。若收尾失败与取消同时发生，逻辑句柄将 record_write_failed 提升到仍存活的根，根外层也只能补缺。取最终状态与 span.end 处于同一线程的外层收束阶段；异步晚到成本回调只写自己的 Receipt，不能改 ScopeTerminal。

## 4. Attempt、首片段与回调

Attempt 由 Executor 创建和结束。取槽等待在 logical 的 wait 作用域记录；正常取得槽后先检查期限，再建立 Attempt。为了保留已有逻辑，新增观察只围绕原控制分支，不移动 `gate.finish_attempt`。

```python attempt.py
import asyncio
from time import monotonic_ns, time_ns

async def run_observed_attempt(logical, runtime, invocation, emit, attempt_id):
    receipt = AttemptReceipt()
    attempt = logical.attempt(attempt_id, invocation.access.spec)
    # 设计接口：Runtime 将该句柄传给 Adapter；不 deepcopy SDK span 或锁。
    first_seen = False
    started_ns = monotonic_ns()

    async def preview(event):
        nonlocal first_seen
        if not first_seen and event.type in {"text_delta", "tool_delta"}:
            first_seen = True
            at_ns = monotonic_ns()
            attempt.first_visible(at_ns - started_ns, time_ns())
            logical.root_first_visible(at_ns)
        # 事件正文只传回原 sink，不进入观测调用。
        await emit(event)

    try:
        result = await runtime.invoke(
            invocation, preview if emit else None,
            observation=attempt, receipt=receipt,
        )
    except RuntimeCancelled as exc:
        attempt.finish_from_result(exc.result, receipt.freeze())
        raise
    except asyncio.CancelledError:
        attempt.finish_unknown_cancellation(receipt.freeze())
        raise
    except Exception:
        attempt.finish_internal_error(receipt.freeze())
        raise
    else:
        attempt.finish_from_result(result, receipt.freeze())
        return result
    finally:
        attempt.end_once()
```

`finish_from_result` 只选择 `SafeResult` 字段，不序列化 result.output；finished generation 设置 input/output 的已知值，reasoning 作为 metadata。第一次失败的 content_exposed 与是否允许重试由原 Executor 判定。Attempt 结束后 SDK 回调无权重开 span。

```python litellm_receipt.py
from math import isfinite

def dynamic_receipt_callbacks(receipt):
    async def on_success(kwargs, response_obj, start_time, end_time):
        # 只抽一个有限标量；不记录、复制或保存 kwargs/response_obj。
        try:
            receipt.accept_cost(kwargs.get("response_cost"))
        except Exception:
            # 固定本地计数由装配器记录；回调不能影响模型路径。
            return
    async def on_failure(kwargs, response_obj, start_time, end_time):
        # 失败状态由 Runtime 的安全错误与发送状态决定。
        return
    return {"success_callback": [on_success], "failure_callback": [on_failure]}
```

Adapter 调用 `litellm.acompletion` 时合并这组服务端内部参数；不使用每请求 `litellm.callbacks.append` 或 `callbacks=[CustomLogger实例]`。回调缺失/晚到保持成本未知。回调未到时 Adapter 可复用锁定 SDK 的本地 completion_cost 及归一响应补充安全金额；流式使用 SDK 的标准组装结果。缓存/用量细分类无法定价或目录无模型时保持未知；禁止从输出文字推算费用。受控探索已验证动态异步函数 success_callback 在当前 SDK 并发调用下各命中一次且全局列表不变；failure/stream/晚到仍需实施测试。

## 5. Generation 映射和未知传播

```python mapping.py
import json
from math import isfinite

def generation_attributes(result: SafeResult, model: str, usd: float | None):
    usage = {}
    if result.input_tokens is not None:
        usage["input"] = result.input_tokens
    if result.output_tokens is not None:
        usage["output"] = result.output_tokens
    complete = result.input_tokens is not None and result.output_tokens is not None
    attrs = {
        "langfuse.observation.type": "generation",
        "compass.model": model[:256],
        "compass.status": result.status.value,
        "compass.usage_complete": complete,
        "compass.outcome_unknown": result.outcome_unknown,
        "compass.sent": result.sent,
        "compass.cleanup_confirmed": result.cleanup_confirmed,
        "compass.cost_state": "unknown",
    }
    if usage:
        attrs["langfuse.observation.usage_details"] = json.dumps(usage)
    if result.reasoning_tokens is not None:
        attrs["compass.reasoning_tokens"] = result.reasoning_tokens
    if complete and not result.outcome_unknown:
        if usd is not None and isfinite(usd) and usd >= 0:
            attrs["langfuse.observation.model.name"] = model[:256]
            attrs["langfuse.observation.cost_details"] = json.dumps({"total": usd})
            attrs["compass.cost_state"] = "complete_estimate"
            attrs["compass.cost_source"] = "sdk"
    code = safe_error(result.error_code)
    if code is not None:
        attrs["compass.error_code"] = code
        attrs["langfuse.observation.status_message"] = code
    attrs["langfuse.observation.level"] = (
        "DEFAULT" if result.status == Terminal.COMPLETED
        else "WARNING" if result.status == Terminal.CANCELED
        else "ERROR"
    )
    return attrs
```

未知用量仅保留 `compass.model`，不填平台原生 model 字段以免触发缺数据估价；金额仅从锁定 SDK 的明确估价写入；完整用量但缺价格仍为 unknown。对比视图按 `compass.model` 元信息分组，保留所有失败样本。父级累计只使用 compass.known_* 字段与 unknown count，generation 数据只出现于实际 Attempt。

## 6. OTel 装配与异步出口

```python export.py
import base64
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, SpanLimits
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# SDK 1.45.2 同版本依赖；启动前设置 SDK internal metrics 开关。
def build_pipeline(settings, filtering_exporter_factory):
    reader = InMemoryMetricReader()
    meters = MeterProvider(metric_readers=[reader])
    # Resource(...)，不用 Resource.create(...) 的自动环境探测。
    resource = Resource({
        "service.name": "compass-agent",
        "service.version": "0.1.0",
        "deployment.environment.name": settings.environment,
    })
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.ratio)),
        span_limits=SpanLimits(
            max_attributes=64, max_events=16, max_links=0,
            max_attribute_length=256,
        ),
        shutdown_on_exit=False,
    )
    auth = base64.b64encode(
        f"{settings.public_key}:{settings.secret_key.get_secret_value()}".encode()
    ).decode("ascii")
    raw = OTLPSpanExporter(
        endpoint=settings.base_url.rstrip("/") + "/api/public/otel/v1/traces",
        headers={"Authorization": "Basic " + auth, "x-langfuse-ingestion-version": "4"},
        timeout=2,
    )
    # 该包装只检查安全属性、固定计数和移除不合法 span；不做第二次排队。
    guarded = filtering_exporter_factory(raw)
    processor = BatchSpanProcessor(
        guarded, max_queue_size=512, max_export_batch_size=64,
        schedule_delay_millis=1000, meter_provider=meters,
    )
    provider.add_span_processor(processor)
    return provider, meters, reader
```

过滤包装在 exporter 后台线程处理 ReadableSpan，不读取私有属性、不修改只读 span；遇到非法属性或事件时丢弃该 span 并增加安全失败计数。`export` 委托一次 raw.export，异常转换固定失败计数与 FAILURE；批次返回值表示传输接受，不表示 UI 已完成可见。关闭先 provider，再 meter provider，不在单次请求调用 force_flush。公共 shutdown 的实际 SDK 预算见主文 §2.6。

## 7. BFF 响应头与权限

```typescript model-agent-client.ts
function readTraceHeaders(response: Response): Record<string, string> {
  const id = response.headers.get("X-Compass-Trace-Id");
  const sampled = response.headers.get("X-Compass-Trace-Sampled");
  if (!id || !/^[0-9a-f]{32}$/.test(id) || !/^(0|1)$/.test(sampled ?? "")) return {};
  return { "X-Compass-Trace-Id": id, "X-Compass-Trace-Sampled": sampled! };
}
```

BFF 将此结果放入成功 JSON 与 SSE 响应头；非成功错误响应同样在原错误映射中携安全头，以便准备失败可排障。Hook 在读取 body 前保存关联元信息。错误头不能绕过原 `ModelErrorResponse` 校验；浏览器不得从头得到后端 URL 或 Key。新状态 API 先沿 `models.authorize(token,{kind:'platform',purpose:'manage'})` 鉴权，再调用 Agent；Agent 再核验 trusted_context 与 require。打开 URL 只来自 Web 固定配置，平台状态失败不影响 Chat。

## 8. 固定对比表的核心计算

查询 Adapter 固定 Langfuse Metrics v2 的 observations 视图、显式顶层 metadata 的 provider_id + 当前 model 双条件过滤和两模型/三时间窗枚举；不接受调用方查询表达式。下列规则在 summary.py 维护，展示与验收使用同一计算。

```python summary.py
from dataclasses import dataclass

@dataclass(frozen=True)
class Aggregate:
    completed: int
    failed: int
    canceled: int
    incomplete: int
    duration_sum_ms: float
    attempt_count: int
    known_input: int
    known_output: int
    known_usd: float
    unknown_usage: int
    unknown_cost: int
    all_queries_ok: bool

def comparison_row(value: Aggregate) -> dict:
    if not value.all_queries_ok:
        return {"data_state": "unavailable", "success_rate": None, "estimated_usd": None}
    logical = value.completed + value.failed + value.canceled + value.incomplete
    return {
        "data_state": "observed" if logical else "empty",
        "logical_count": logical,
        "success_rate": value.completed / logical if logical else None,
        "mean_duration_ms": value.duration_sum_ms / logical if logical else None,
        "attempt_count": value.attempt_count,
        "known_input_tokens": value.known_input,
        "known_output_tokens": value.known_output,
        "unknown_usage_attempts": value.unknown_usage,
        "known_estimated_usd": value.known_usd,
        "estimated_usd": value.known_usd if value.unknown_cost == 0 else None,
        "unknown_cost_attempts": value.unknown_cost,
    }
```

每模型 7 个固定聚合查询；两模型 14 个，硬上限 24；HTTP 并发 4、每次 2 秒、整体 8 秒、每响应 256 KiB / 100 行。任何一个状态查询、用量或未知计数查询失败，整行 unavailable，禁止凑比例或降成 0。空分母保持 null。Adapter 必须校验非负/有限统计值，拒绝负值、额外行和超限响应；错误只影响只读表。费用仅已知 Attempt 合计，未知数来自 metadata.cost_state=unknown 的 count；不把父 span 再求和。

## 9. Review 重点

- 原执行状态优先，特别是取消和 `_finish` 失败的竞争；观测结束不能覆盖已收到的 final_state。
- 不把完整对象交给 SDK，不通过自动异常记录、回调或第三方日志绕开白名单。
- 根、逻辑和 Attempt 的计数、Token、费用口径分别维护；未采样与未导出不冒充成功入库。
- SSE 根任务和 HTTP 响应头在请求早期建立，观测 span 在原后台任务退出时结束。
- BSP 参数、私有配置来源与六服务依赖的实际行为必须实施验证，示意代码不承担执行完成证明。
