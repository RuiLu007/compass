# DataAgent LLM Application Runtime 架构设计方案

技术基线：Python + LiteLLM Core SDK + Pydantic + OpenTelemetry

这份方案以你的企业级 DataAgent 为实际场景，重点解决四个问题：

1. 每个 LLM Runtime 子模块的职责、边界和核心抽象是什么？
2. 哪些能力必须自建，哪些应该直接复用 LiteLLM Core？
3. 模型路由、Retry、Fallback、Streaming、Budget 等跨层能力，如何避免重复实现？
4. 如何设计一套足够轻量、可扩展、可测试的 Python UML 架构？

## 一、架构定位与关键决策

### 1.1 系统定位

LLM Application Runtime 是嵌入 DataAgent Worker 进程内的模型调用组件，不是独立服务。

其责任是：

> 接受一次具有明确能力需求和执行约束的逻辑模型调用，选择符合约束的 Provider，可靠执行调用，并返回统一的结果、事件、用量和可观测信息。

它不拥有 Agent 的业务决策权，也不负责完整任务生命周期。

例如，DataAgent 的一次经营分析任务可能需要执行：

`理解任务 → 查询指标 → 生成 SQL → 执行 SQL → 分析数据 → 生成报告`

其中，任务规划、SQL 执行、报告校验属于 Harness；每次 LLM 请求的执行属于 LLM Runtime。

### 1.2 六条架构决策

| ADR    | 决策                    | 理由                                |
| ------ | --------------------- | --------------------------------- |
| ADR-01 | 进程内 SDK，不部署 Proxy     | 避免额外网络跳转、运维和治理复杂度                 |
| ADR-02 | 自有稳定调用契约              | 防止 Harness 直接耦合 LiteLLM 数据结构      |
| ADR-03 | LiteLLM 优先执行底层机制      | 不重复开发 HTTP、协议转换、基础 Retry          |
| ADR-04 | Policy 与 Mechanism 分离 | 企业约束自有，底层执行尽量复用                   |
| ADR-05 | Provider 可插拔          | 支持自有 DeepSeek Provider、决策模型特殊协议   |
| ADR-06 | 无 Agent 任务持久状态        | Checkpoint、Memory、Run 状态归 Harness |

### 1.3 首期明确不做

不建设独立 LLM Gateway、通用模型运营后台、复杂的动态模型调度平台、跨进程统一配额中心，也不开发第二套模型 HTTP 客户端。

首期优先支持三类模型能力：复杂推理模型、快速通用模型和 JEV 类决策模型。决策模型的 `choice / score / null` 等输出可以通过扩展契约支持，不必强行塞进 Chat Completion 语义。

## 二、总体架构与模块依赖

我建议采用 4 个核心模块 + 2 个扩展模块 + 1 个内部执行协调器 的结构。

内部协调器 `LLMClient` 是整个 Runtime 的入口，不需要将每一个模块都设计成独立的 Service。

DataAgent Harness

提供 ModelIntent / Messages / Tools / CallConstraints

LLMClient / CallExecutor

统一入口 · 调用生命周期协调 · 异步执行

Python asyncio · anyio（可选）

LLM Contract

请求 / 响应 / 事件契约

Pydantic · typing

Model Registry

模型能力 / 别名 / 路由解析

Pydantic Settings · YAML

Call Policy

Timeout / Retry / Fallback / Cancel

asyncio · LiteLLM Router

Call Telemetry

Trace / Usage / Cost / Errors

OpenTelemetry · LiteLLM

Streaming

流式事件转换 / 输出完整性

AsyncIterator · Pydantic

Usage & Quota

用量统计 / 调用准入

LiteLLM · asyncio

ProviderAdapter

LiteLLMAdapter

标准多模型 API

NativeAdapter

特殊 / 自有协议

LiteLLM Core SDK

Provider 协议 · 请求发送 · 基础 Streaming · Retry / Router（按版本验证）

这里有两项调整：

- Streaming 不一定需要独立 Engine。 首期可以只是 `EventNormalizer`，由 `CallExecutor` 管理生命周期。
- Usage & Quota 不一定需要独立 Service。 首期用量归集和本地并发控制即可，跨 Worker 配额由上层基础设施治理。

## 三、各模块详细设计

### 3.1 LLM Contract：整个 Runtime 最值得自建的模块

设计目标：让 Harness 与 LiteLLM、Provider 协议彻底解耦。

| 应该做                         | 不应该做                            |
| --------------------------- | ------------------------------- |
| 定义稳定的请求、响应、事件模型             | 直接暴露 LiteLLM CompletionResponse |
| 支持消息、工具、结构化输出、Thinking 配置   | 为每个 Provider 设计一套公共 DTO         |
| 统一 FinishReason、Usage、Error | 抹平所有 Provider 特有字段              |
| 保留 Provider-specific 扩展     | 把业务任务类型写进 LLM Request           |
| 统一流式与非流式的结果语义               | 承担 SQL、指标口径等业务校验                |

建议的核心数据模型：

```
class ModelIntent(BaseModel):    model_id: str | None = None    profile: str | None = None    allowed_models: list[str] | None = Noneclass CallConstraints(BaseModel):    timeout_s: float    max_attempts: int = 2    allow_fallback: bool = False    max_cost_usd: float | None = Noneclass LLMRequest(BaseModel):    messages: list[Message]    model: ModelIntent    constraints: CallConstraints    tools: list[ToolSpec] = []    response_format: ResponseFormat | None = None    provider_options: dict[str, Any] = {}
```

实际工程中，`Message` 应采用支持多模态内容块和 Tool Call 关联 ID 的判别联合类型，而非简单的 `role + str`。

`provider_options` 也不能无限制透传，必须有允许字段校验，避免调用者绕过 Runtime 的策略约束。

架构取舍：

- Pydantic v2 足够承担数据验证和 Schema 定义。
- `orjson` 只有在性能测试证明序列化是瓶颈时才引入。
- 不需要自建 JSON Schema 验证器。

### 3.2 Model Registry：轻量能力注册表，不是模型运营平台

Model Registry 最容易过度设计。

对 DataAgent 而言，它主要回答三个问题：

1. 模型支持什么能力？
2. 当前调用允许哪些模型？
3. 如何解析为具体 Provider 和模型标识？

应该做：

- 注册模型 ID、Provider、上下文窗口、能力特征。
- 定义 `reasoning`、`fast`、`decision` 等逻辑 Profile。
- 校验 Tool Calling、Structured Output、Thinking 等硬约束。
- 解析模型别名与候选集合。
- 提供明确的模型选择结果及理由。

不应该做：

- 根据 Prompt 语义自行决定 Agent 任务该用什么模型。
- 建设复杂的动态模型质量排行榜。
- 自行维护一套与 LiteLLM 重复的模型价格数据库。
- 在调用期间未经授权地更改模型。
- 首期引入 PostgreSQL、SQLAlchemy、Alembic，只为了存储几个模型配置。

建议首期使用 Pydantic Settings + YAML，模型能力采用显式配置，并在启动时校验。

```
models:
  deepseek-pro:
    provider: litellm
    model: deepseek/...
    capabilities:
      tool_calling: true
      structured_output: true
      thinking: true

profiles:
  reasoning:
    candidates: [deepseek-pro]
```

模型 ID 和能力字段应由实际 API 文档与接入测试确定，上面仅展示配置结构。

关键设计： Model Registry 不应直接执行网络请求。它只负责解析出 `ResolvedModel`，交给 Adapter 调用。

### 3.3 Call Policy：企业级可靠性控制的核心

这个模块最需要克制设计。

它负责定义一次逻辑调用的执行约束，但不应该重新实现 LiteLLM 已经提供的通用网络重试机制。

### Call Policy 的职责分界

| 企业 Runtime 自建 | 能力约束、Fallback 授权、Deadline、取消传播、重试预算、流式提交边界     |
| ------------- | ---------------------------------------------- |
| 委托 LiteLLM    | HTTP 重试、指数退避、已支持的 Router / Deployment Fallback |
| Harness 负责    | Step 重试、Prompt 修正、任务重新规划、全局预算                  |

建议采用 `CallPolicy` 不可变配置 + `CallExecutor` 执行协调的设计。

必须落实的工程约束：

- 同一次调用使用统一 Deadline，不能每次 Retry 都重置完整超时时间。
- Retry 与 Fallback 的总物理尝试次数有统一上限。
- 流式输出一旦交付上层，默认禁止透明重试。
- Fallback 只能在能力兼容、策略授权的候选模型中执行。
- Cancel 应尽可能终止正在进行的请求，并阻止后续重试。
- 取消和超时不代表 Provider 侧一定停止计费，必须允许用量状态为未知或估计值。

不应该做：

- 再引入 Tenacity 实现一套与 LiteLLM Router 重叠的重试链。
- 维护复杂的全局熔断集群。
- 在 LLM Runtime 中实现 Agent Step 的重新规划。
- 将所有异常都转换成可重试错误。

如果 LiteLLM Core 当前安装版本没有满足需求的 Router 能力，才考虑在 `CallExecutor` 增加最小重试实现。

### 3.4 Streaming：事件语义统一，不重造传输层

这个模块应尽量薄。

应该做：

- 将 Provider 的 Delta 转换为统一事件。
- 区分 `text_delta`、`reasoning_delta`、`tool_call_delta`。
- 保留 Tool Call ID 与增量参数。
- 统一 `completed`、`failed`、`cancelled` 等终态。
- 对流式中断返回明确的部分输出状态。

不应该做：

- 重新实现 SSE/HTTP Client。
- 负责前端 WebSocket 连接。
- 在 Runtime 中拼装 Agent 的完整对话历史。
- 在不具备稳定协议保证时，假设所有 Provider 的 Thinking 都可无损映射。

建议：

```
class LLMEvent(BaseModel):    type: EventType    call_id: str    sequence: int    payload: EventPayload
```

其中 `EventPayload` 使用判别联合类型，不建议所有事件共用一个无约束的 `dict`。

Runtime 对 Tool Call 参数可以增量组装，但是否执行工具属于 Harness。

### 3.5 Usage & Quota：调用级计量与资源约束

对 DataAgent 首期，建议拆分成两个概念：

Usage Accounting

- 收集 Provider 返回的 Token Usage。
- 记录逻辑调用下的每次物理尝试。
- 汇总 Retry / Fallback 的成本。
- 区分真实计量、估计计量和未知计量。

Call Admission

- 检查模型调用并发限制。
- 接受 Harness 传入的剩余预算约束。
- 对明显超过上下文窗口的请求提前拒绝。
- 可选进行单 Worker 限流。

不应该做：

- 管理租户全局预算账户。
- 在多个 Worker 之间自行实现强一致配额。
- 在 Runtime 中决定某个分析任务值得花多少钱。
- 依赖 Token 预估值来保证绝对不超预算。

首期使用 `asyncio.Semaphore` 控制进程内并发即可。Redis 只有在确实需要跨 Worker 协调时才引入，而且需要明确分布式限流的原子性与故障策略。

### 3.6 Telemetry：调用链可观测，而不是 Agent 评测系统

应该做：

- 为每次逻辑调用创建 LLM Call Span。
- 为每次物理 API 请求记录 Attempt。
- 记录模型、Provider、耗时、TTFT、Token、成本、错误类别。
- 传播 Harness 的 Trace Context。
- 对 Prompt、工具参数及响应内容实施脱敏与采样策略。

不应该做：

- 自建第二套 Trace 系统。
- 在 Runtime 内实现 Agent 任务成功率评测。
- 默认完整记录用户 Prompt 和模型响应。
- 让日志系统直接依赖 DataAgent 的业务数据库。

技术栈优先使用 OpenTelemetry API/SDK，接入现有 Collector。

日志可使用 Python 标准 `logging`，不必为了 LLM Runtime 单独引入 Loguru。Prometheus Exporter 也可以复用现有应用基础设施。

### 3.7 Provider Adapter：可替换性的最后一道边界

建议定义一个极简接口：

```
class ProviderAdapter(Protocol):    async def generate(        self, request: ProviderRequest    ) -> ProviderResponse: ...    def stream(        self, request: ProviderRequest    ) -> AsyncIterator[ProviderEvent]: ...
```

其中：

- `LiteLLMAdapter`：调用 LiteLLM Core。
- `NativeAdapter`：对自有模型或特殊协议模型直接适配。
- `ProviderRequest`：是已解析的模型调用，不再包含 Harness 的任务意图。

注意： 不建议为每个模型都写一个 Adapter 类。只在协议或语义确实不同的时候新增适配器。

## 四、UML 架构设计

前面已经完成了总体架构与核心模块职责。下面从 4.3 开始，重点进入类设计、执行时序、模块依赖、异常处理及工程落地。

### 4.3 核心类图（Class Diagram）

建议采用 Facade + Strategy + Adapter 的轻量组合，而不是引入复杂的框架体系。

设计重点：

- `LLMClient`：对 Harness 暴露的唯一公共入口。
- `CallExecutor`：组织一次逻辑模型调用的执行生命周期。
- `ModelRegistry`：负责模型解析与能力约束校验。
- `CallPolicy`：承载调用约束，不直接执行网络请求。
- `ProviderAdapter`：隔离 LiteLLM 与 Native Provider。
- `Telemetry`：通过组合方式注入，不侵入核心业务逻辑。

### 核心类 UML

实线表示调用或依赖，虚线表示接口实现。

\#chatgpt-mermaid-\_r_4gu\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(13, 13, 13);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_4gu\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4gu\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4gu\_ .error-icon{fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_4gu\_ .error-text{fill:rgb(13, 13, 13);stroke:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4gu\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_4gu\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_4gu\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_4gu\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_4gu\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_4gu\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_4gu\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4gu\_ .marker.cross{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4gu\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_4gu\_ p{margin:0;}#chatgpt-mermaid-\_r_4gu\_ g.classGroup text{fill:rgb(83, 154, 248);stroke:none;font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:10px;}#chatgpt-mermaid-\_r_4gu\_ g.classGroup text .title{font-weight:bolder;}#chatgpt-mermaid-\_r_4gu\_ .cluster-label text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4gu\_ .cluster-label span{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4gu\_ .cluster-label span p{background-color:transparent;}#chatgpt-mermaid-\_r_4gu\_ .cluster rect{fill:rgb(243, 243, 243);stroke:rgba(0, 0, 0, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_4gu\_ .cluster text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4gu\_ .cluster span{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4gu\_ .nodeLabel,#chatgpt-mermaid-\_r_4gu\_ .edgeLabel{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4gu\_ .noteLabel .nodeLabel,#chatgpt-mermaid-\_r_4gu\_ .noteLabel .edgeLabel{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4gu\_ .edgeLabel .label rect{fill:rgb(222, 234, 251);}#chatgpt-mermaid-\_r_4gu\_ .label text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4gu\_ .labelBkg{background:rgb(222, 234, 251);}#chatgpt-mermaid-\_r_4gu\_ .edgeLabel .label span{background:rgb(222, 234, 251);}#chatgpt-mermaid-\_r_4gu\_ .classTitle{font-weight:bolder;}#chatgpt-mermaid-\_r_4gu\_ .node rect,#chatgpt-mermaid-\_r_4gu\_ .node circle,#chatgpt-mermaid-\_r_4gu\_ .node ellipse,#chatgpt-mermaid-\_r_4gu\_ .node polygon,#chatgpt-mermaid-\_r_4gu\_ .node path{fill:rgb(222, 234, 251);stroke:rgb(83, 154, 248);stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ .divider{stroke:rgb(83, 154, 248);stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ g.clickable{cursor:pointer;}#chatgpt-mermaid-\_r_4gu\_ g.classGroup rect{fill:rgb(222, 234, 251);stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_4gu\_ g.classGroup line{stroke:rgb(83, 154, 248);stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ .classLabel .box{stroke:none;stroke-width:0;fill:rgb(222, 234, 251);opacity:0.5;}#chatgpt-mermaid-\_r_4gu\_ .classLabel .label{fill:rgb(83, 154, 248);font-size:10px;}#chatgpt-mermaid-\_r_4gu\_ .relation{stroke:rgb(143, 143, 143);stroke-width:1;fill:none;}#chatgpt-mermaid-\_r_4gu\_ .dashed-line{stroke-dasharray:3;}#chatgpt-mermaid-\_r_4gu\_ .dotted-line{stroke-dasharray:1 2;}#chatgpt-mermaid-\_r_4gu\_ [id$="-compositionStart"],#chatgpt-mermaid-\_r_4gu\_ .composition{fill:rgb(143, 143, 143)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ [id$="-compositionEnd"],#chatgpt-mermaid-\_r_4gu\_ .composition{fill:rgb(143, 143, 143)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ [id$="-dependencyStart"],#chatgpt-mermaid-\_r_4gu\_ .dependency{fill:rgb(143, 143, 143)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ [id$="-dependencyEnd"],#chatgpt-mermaid-\_r_4gu\_ .dependency{fill:rgb(143, 143, 143)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ [id$="-extensionStart"],#chatgpt-mermaid-\_r_4gu\_ .extension{fill:transparent!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ [id$="-extensionEnd"],#chatgpt-mermaid-\_r_4gu\_ .extension{fill:transparent!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ [id$="-aggregationStart"],#chatgpt-mermaid-\_r_4gu\_ .aggregation{fill:transparent!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ [id$="-aggregationEnd"],#chatgpt-mermaid-\_r_4gu\_ .aggregation{fill:transparent!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ [id$="-lollipopStart"],#chatgpt-mermaid-\_r_4gu\_ .lollipop{fill:rgb(222, 234, 251)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ [id$="-lollipopEnd"],#chatgpt-mermaid-\_r_4gu\_ .lollipop{fill:rgb(222, 234, 251)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4gu\_ .edgeTerminals{font-size:11px;line-height:initial;}#chatgpt-mermaid-\_r_4gu\_ .classTitleText{text-anchor:middle;font-size:18px;fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4gu\_ .edgeLabel[data-look="neo"]{background-color:rgb(252, 252, 252);text-align:center;}#chatgpt-mermaid-\_r_4gu\_ .edgeLabel[data-look="neo"] p{background-color:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_4gu\_ .edgeLabel[data-look="neo"] rect{opacity:0.5;background-color:rgb(252, 252, 252);fill:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_4gu\_ .label-icon{display:inline-block;height:1em;overflow:visible;vertical-align:-0.125em;}#chatgpt-mermaid-\_r_4gu\_ .node .label-icon path{fill:currentColor;stroke:revert;stroke-width:revert;}#chatgpt-mermaid-\_r_4gu\_ .node .neo-node{stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_4gu\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_4gu\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].node .neo-line path{stroke:rgb(83, 154, 248);filter:none;}#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_4gu\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_4gu\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4gu\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_4gu\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4gu\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}LLMClient+generate(request) : LLMResponse+stream(request) : AsyncIteratorCallExecutor-registry ModelRegistry-policy_engine PolicyEngine-adapter ProviderAdapter-telemetry CallTelemetry+execute(request) : LLMResponse+execute_stream(request) : AsyncIteratorLLMRequest+messages+model_intent+constraints+tools+response_formatLLMResponse+model_id+content+tool_calls+usage+finish_reasonModelRegistry+resolve(intent, requirements) : ResolvedModel+get_capabilities(model_id)PolicyEngine+validate(request, model)+build_plan(request) : ExecutionPlanExecutionPlan+resolved_model+deadline+max_attempts+fallback_candidates«interface»ProviderAdapter+generate(request)+stream(request)LiteLLMAdapter-sdk_clientNativeAdapter-http_clientCallTelemetry+start_call()+record_attempt()+record_usage()+finish_call()

#### 核心设计考量

第一，PolicyEngine 不应该变成第二个 Router。

`PolicyEngine` 只负责生成执行约束和策略，例如：

```
@dataclass(frozen=True)class ExecutionPlan:    primary_model: str    fallback_models: tuple[str, ...]    deadline_seconds: float    max_attempts: int    allow_fallback: bool
```

真正的网络重试与 Fallback，优先委托 LiteLLM 已有机制。

如果需要跨不同 Adapter 执行 Fallback，才由 `CallExecutor` 负责有限的执行协调。

第二，CallExecutor 应当是轻量协调器。

不建议把所有功能都实现为独立的 Manager 或 Service。

例如：

- `ModelRegistry` 可以只是配置对象和解析函数。
- `PolicyEngine` 可以是无状态的纯函数。
- `CallTelemetry` 可以只是 OpenTelemetry 的薄封装。
- `StreamingNormalizer` 可以是一组事件转换函数。

这样能够避免为一个进程内 Runtime 引入过多抽象。

第三，NativeAdapter 应该是可选扩展。

如果自有 DeepSeek 模型和决策模型都能通过 LiteLLM 正确接入，完全可以不实现 NativeAdapter。

### 4.4 模型路由 UML：三层决策如何衔接

这是整个设计中最容易发生职责重叠的地方。

建议严格区分：

- Harness：指定模型意图及约束。
- ModelRegistry：解析出符合约束的模型。
- LiteLLM：执行底层 Provider / Deployment 路由。

LiteLLM SDKLiteLLMAdapterPolicyEngineModelRegistryLLMClientAgent HarnessLiteLLM SDKLiteLLMAdapterPolicyEngineModelRegistryLLMClientAgent Harness#chatgpt-mermaid-\_r_4hc\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(13, 13, 13);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_4hc\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4hc\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4hc\_ .error-icon{fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_4hc\_ .error-text{fill:rgb(13, 13, 13);stroke:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4hc\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_4hc\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_4hc\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_4hc\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_4hc\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_4hc\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_4hc\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4hc\_ .marker.cross{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4hc\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_4hc\_ p{margin:0;}#chatgpt-mermaid-\_r_4hc\_ .actor{stroke:rgb(83, 154, 248);fill:rgb(222, 234, 251);stroke-width:1;}#chatgpt-mermaid-\_r_4hc\_ rect.actor.outer-path[data-look="neo"]{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4hc\_ rect.note[data-look="neo"]{stroke:rgb(107, 198, 127);fill:rgb(243, 243, 243);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4hc\_ text.actor>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4hc\_ .actor-line{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4hc\_ .innerArc{stroke-width:1.5;stroke-dasharray:none;}#chatgpt-mermaid-\_r_4hc\_ .messageLine0{stroke-width:1.5;stroke-dasharray:none;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4hc\_ .messageLine1{stroke-width:1.5;stroke-dasharray:2,2;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4hc\_ [id$="-arrowhead"] path{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4hc\_ .sequenceNumber{fill:#707070;}#chatgpt-mermaid-\_r_4hc\_ [id$="-sequencenumber"]{fill:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4hc\_ [id$="-crosshead"] path{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4hc\_ .messageText{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4hc\_ .labelBox{stroke:rgba(0, 0, 0, 0.1);fill:rgb(252, 252, 252);filter:none;}#chatgpt-mermaid-\_r_4hc\_ .labelText,#chatgpt-mermaid-\_r_4hc\_ .labelText>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4hc\_ .loopText,#chatgpt-mermaid-\_r_4hc\_ .loopText>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4hc\_ .sectionTitle,#chatgpt-mermaid-\_r_4hc\_ .sectionTitle>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4hc\_ .loopLine{stroke-width:2px;stroke-dasharray:2,2;stroke:rgba(0, 0, 0, 0.1);fill:rgba(0, 0, 0, 0.1);}#chatgpt-mermaid-\_r_4hc\_ .note{stroke:rgb(107, 198, 127);fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_4hc\_ .noteText,#chatgpt-mermaid-\_r_4hc\_ .noteText>tspan{fill:rgb(13, 13, 13);stroke:none;font-weight:normal;}#chatgpt-mermaid-\_r_4hc\_ .activation0{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_4hc\_ .activation1{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_4hc\_ .activation2{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_4hc\_ .actorPopupMenu{position:absolute;}#chatgpt-mermaid-\_r_4hc\_ .actorPopupMenuPanel{position:absolute;fill:rgb(222, 234, 251);box-shadow:0px 8px 16px 0px rgba(0,0,0,0.2);filter:drop-shadow(3px 5px 2px rgb(0 0 0 / 0.4));}#chatgpt-mermaid-\_r_4hc\_ .actor-man circle,#chatgpt-mermaid-\_r_4hc\_ line{fill:rgb(222, 234, 251);stroke-width:2px;}#chatgpt-mermaid-\_r_4hc\_ g rect.rect{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_4hc\_ .node .neo-node{stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_4hc\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_4hc\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].node .neo-line path{stroke:rgb(83, 154, 248);filter:none;}#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_4hc\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_4hc\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4hc\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_4hc\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4hc\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}generate(ModelIntent, Constraints)resolve(intent, capabilities)ResolvedModel + Candidatesvalidate + build_planExecutionPlangenerate(ProviderRequest)acompletion / RouterProvider ResponseNormalized ResponseLLMResponse

#### 路由优先级

建议采用以下约束顺序：

| 优先级 | 条件             | 处理方式        |
| --- | -------------- | ----------- |
| P0  | Harness 指定固定模型 | 严格使用该模型     |
| P1  | 模型能力硬约束        | 不满足直接拒绝     |
| P2  | 模型白名单与安全策略     | 只在允许集合内选择   |
| P3  | Fallback 授权    | 未授权不允许跨模型切换 |
| P4  | 成本、延迟、可用性偏好    | 在剩余候选中优化    |

这里的 P0–P4 表示判断优先级，而不是建设优先级。

例如 Harness 请求：

```
ModelIntent(    profile="reasoning",    allowed_models=[        "deepseek-v4-pro",        "deepseek-v4-flash"    ])
```

Runtime 可以根据能力约束与路由策略解析模型。

但如果请求是：

```
ModelIntent(    model_id="deepseek-v4-pro")
```

默认不允许自行切换成 Flash。

重要原则：模型路由是一次调用的前置决策；Fallback 是调用失败后的受控决策，两者不应混为一谈。

### 4.5 Retry / Fallback UML：可靠性如何分层

这部分建议明确两个执行层级。

- SDK 内部 Retry：同一 Provider/Deployment 的可重试网络错误。
- Runtime 级 Fallback 协调：在 SDK 无法完整表达企业约束时，切换到经过授权的兼容模型。

对于可以完全委托 LiteLLM Router 的场景，不需要 Runtime 再执行一遍相同逻辑。

\#chatgpt-mermaid-\_r_4i1\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(13, 13, 13);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_4i1\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4i1\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4i1\_ .error-icon{fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_4i1\_ .error-text{fill:rgb(13, 13, 13);stroke:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4i1\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_4i1\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_4i1\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_4i1\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_4i1\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_4i1\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_4i1\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i1\_ .marker.cross{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i1\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_4i1\_ p{margin:0;}#chatgpt-mermaid-\_r_4i1\_ .label{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4i1\_ .cluster-label text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4i1\_ .cluster-label span{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4i1\_ .cluster-label span p{background-color:transparent;}#chatgpt-mermaid-\_r_4i1\_ .label text,#chatgpt-mermaid-\_r_4i1\_ span{fill:rgb(13, 13, 13);color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4i1\_ .node rect,#chatgpt-mermaid-\_r_4i1\_ .node circle,#chatgpt-mermaid-\_r_4i1\_ .node ellipse,#chatgpt-mermaid-\_r_4i1\_ .node polygon,#chatgpt-mermaid-\_r_4i1\_ .node path{fill:rgb(222, 234, 251);stroke:rgb(83, 154, 248);stroke-width:1px;}#chatgpt-mermaid-\_r_4i1\_ .rough-node .label text,#chatgpt-mermaid-\_r_4i1\_ .node .label text,#chatgpt-mermaid-\_r_4i1\_ .image-shape .label,#chatgpt-mermaid-\_r_4i1\_ .icon-shape .label{text-anchor:middle;}#chatgpt-mermaid-\_r_4i1\_ .node .katex path{fill:#000;stroke:#000;stroke-width:1px;}#chatgpt-mermaid-\_r_4i1\_ .rough-node .label,#chatgpt-mermaid-\_r_4i1\_ .node .label,#chatgpt-mermaid-\_r_4i1\_ .image-shape .label,#chatgpt-mermaid-\_r_4i1\_ .icon-shape .label{text-align:center;}#chatgpt-mermaid-\_r_4i1\_ .node.clickable{cursor:pointer;}#chatgpt-mermaid-\_r_4i1\_ .root .anchor path{fill:rgb(143, 143, 143)!important;stroke-width:0;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i1\_ .arrowheadPath{fill:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i1\_ .edgePath .path{stroke:rgb(143, 143, 143);stroke-width:1px;}#chatgpt-mermaid-\_r_4i1\_ .flowchart-link{stroke:rgb(143, 143, 143);fill:none;}#chatgpt-mermaid-\_r_4i1\_ .edgeLabel{background-color:rgb(252, 252, 252);text-align:center;}#chatgpt-mermaid-\_r_4i1\_ .edgeLabel p{background-color:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_4i1\_ .edgeLabel rect{opacity:0.5;background-color:rgb(252, 252, 252);fill:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_4i1\_ .labelBkg{background-color:rgba(252, 252, 252, 0.5);}#chatgpt-mermaid-\_r_4i1\_ .cluster rect{fill:rgb(243, 243, 243);stroke:rgba(0, 0, 0, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_4i1\_ .cluster text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4i1\_ .cluster span{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4i1\_ div.mermaidTooltip{position:absolute;text-align:center;max-width:200px;padding:2px;font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:12px;background:rgb(243, 243, 243);border:1px solid rgba(0, 0, 0, 0.1);border-radius:2px;pointer-events:none;z-index:100;}#chatgpt-mermaid-\_r_4i1\_ .flowchartTitleText{text-anchor:middle;font-size:18px;fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4i1\_ rect.text{fill:none;stroke-width:0;}#chatgpt-mermaid-\_r_4i1\_ .icon-shape,#chatgpt-mermaid-\_r_4i1\_ .image-shape{background-color:rgb(252, 252, 252);text-align:center;}#chatgpt-mermaid-\_r_4i1\_ .icon-shape p,#chatgpt-mermaid-\_r_4i1\_ .image-shape p{background-color:rgb(252, 252, 252);padding:2px;}#chatgpt-mermaid-\_r_4i1\_ .icon-shape .label rect,#chatgpt-mermaid-\_r_4i1\_ .image-shape .label rect{opacity:0.5;background-color:rgb(252, 252, 252);fill:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_4i1\_ .label-icon{display:inline-block;height:1em;overflow:visible;vertical-align:-0.125em;}#chatgpt-mermaid-\_r_4i1\_ .node .label-icon path{fill:currentColor;stroke:revert;stroke-width:revert;}#chatgpt-mermaid-\_r_4i1\_ .node .neo-node{stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_4i1\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_4i1\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].node .neo-line path{stroke:rgb(83, 154, 248);filter:none;}#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_4i1\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_4i1\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i1\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_4i1\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i1\_ .node text{font-size:14px;font-weight:600;letter-spacing:normal;fill:rgb(65, 65, 65);}#chatgpt-mermaid-\_r_4i1\_ .edgeLabels text{font-size:13px;font-weight:600;letter-spacing:-0.08px;fill:rgb(65, 65, 65);}#chatgpt-mermaid-\_r_4i1\_ .node tspan[font-weight="normal"],#chatgpt-mermaid-\_r_4i1\_ .edgeLabels tspan[font-weight="normal"]{font-weight:600;}#chatgpt-mermaid-\_r_4i1\_ .edgeLabel .label rect{opacity:1;rx:13px;ry:13px;fill:rgb(252, 252, 252);stroke:rgb(219, 219, 219);stroke-width:1px;}#chatgpt-mermaid-\_r_4i1\_ .node rect,#chatgpt-mermaid-\_r_4i1\_ .node circle,#chatgpt-mermaid-\_r_4i1\_ .node ellipse,#chatgpt-mermaid-\_r_4i1\_ .node polygon,#chatgpt-mermaid-\_r_4i1\_ .node path{fill:rgb(243, 243, 243);stroke:rgba(0, 0, 0, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_4i1\_ .node rect{rx:16px;ry:16px;}#chatgpt-mermaid-\_r_4i1\_ .mermaid-decision .label-container{fill:rgb(249, 249, 249);stroke:rgb(219, 219, 219);stroke-dasharray:2,2;}#chatgpt-mermaid-\_r_4i1\_ .edgePaths .flowchart-link{stroke:rgb(143, 143, 143);stroke-width:1px;stroke-linecap:round;stroke-linejoin:round;}#chatgpt-mermaid-\_r_4i1\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i1\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}收到 LLMRequest解析模型与执行约束调用 LiteLLM调用成功？归一化响应与用量可重试错误？SDK 重试预算充足？LiteLLM Retry / Backoff允许 Fallback？能力兼容且尚未提交输出？切换授权候选模型返回结构化失败返回 LLMResponse是否是是否否是是否否

图中表达的是逻辑执行流程，不是要求同时开启 SDK 与 Runtime 两套 Retry 循环。

实际实现应当保证同一次调用只有一个明确的重试预算管理者。

#### 需要特别处理的四类情况

| 场景             | 推荐策略                       |
| -------------- | -------------------------- |
| HTTP 429 / 503 | SDK 执行有限重试和退避              |
| 模型不支持指定 Schema | 调用前拒绝，或在授权候选中重新解析          |
| 流式输出中断         | 返回部分输出状态，不透明重试             |
| Harness 主动取消   | 传播取消信号，禁止后续 Retry/Fallback |

还需要注意，LiteLLM 的实际 Router、Fallback 和取消行为应基于锁定版本进行集成测试，不能只依赖 API 文档推断其所有边界行为。

### 4.6 Streaming UML：流式事件如何设计

建议采用 统一事件模型 + AsyncIterator。

不要额外引入事件总线、消息队列或独立 Streaming Service。

LiteLLM SDKLiteLLMAdapterCallExecutorLLMClientAgent HarnessLiteLLM SDKLiteLLMAdapterCallExecutorLLMClientAgent Harness#chatgpt-mermaid-\_r_4i8\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(13, 13, 13);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_4i8\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4i8\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4i8\_ .error-icon{fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_4i8\_ .error-text{fill:rgb(13, 13, 13);stroke:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4i8\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_4i8\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_4i8\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_4i8\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_4i8\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_4i8\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_4i8\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i8\_ .marker.cross{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i8\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_4i8\_ p{margin:0;}#chatgpt-mermaid-\_r_4i8\_ .actor{stroke:rgb(83, 154, 248);fill:rgb(222, 234, 251);stroke-width:1;}#chatgpt-mermaid-\_r_4i8\_ rect.actor.outer-path[data-look="neo"]{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i8\_ rect.note[data-look="neo"]{stroke:rgb(107, 198, 127);fill:rgb(243, 243, 243);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i8\_ text.actor>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4i8\_ .actor-line{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i8\_ .innerArc{stroke-width:1.5;stroke-dasharray:none;}#chatgpt-mermaid-\_r_4i8\_ .messageLine0{stroke-width:1.5;stroke-dasharray:none;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i8\_ .messageLine1{stroke-width:1.5;stroke-dasharray:2,2;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i8\_ [id$="-arrowhead"] path{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i8\_ .sequenceNumber{fill:#707070;}#chatgpt-mermaid-\_r_4i8\_ [id$="-sequencenumber"]{fill:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i8\_ [id$="-crosshead"] path{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4i8\_ .messageText{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4i8\_ .labelBox{stroke:rgba(0, 0, 0, 0.1);fill:rgb(252, 252, 252);filter:none;}#chatgpt-mermaid-\_r_4i8\_ .labelText,#chatgpt-mermaid-\_r_4i8\_ .labelText>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4i8\_ .loopText,#chatgpt-mermaid-\_r_4i8\_ .loopText>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4i8\_ .sectionTitle,#chatgpt-mermaid-\_r_4i8\_ .sectionTitle>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_4i8\_ .loopLine{stroke-width:2px;stroke-dasharray:2,2;stroke:rgba(0, 0, 0, 0.1);fill:rgba(0, 0, 0, 0.1);}#chatgpt-mermaid-\_r_4i8\_ .note{stroke:rgb(107, 198, 127);fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_4i8\_ .noteText,#chatgpt-mermaid-\_r_4i8\_ .noteText>tspan{fill:rgb(13, 13, 13);stroke:none;font-weight:normal;}#chatgpt-mermaid-\_r_4i8\_ .activation0{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_4i8\_ .activation1{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_4i8\_ .activation2{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_4i8\_ .actorPopupMenu{position:absolute;}#chatgpt-mermaid-\_r_4i8\_ .actorPopupMenuPanel{position:absolute;fill:rgb(222, 234, 251);box-shadow:0px 8px 16px 0px rgba(0,0,0,0.2);filter:drop-shadow(3px 5px 2px rgb(0 0 0 / 0.4));}#chatgpt-mermaid-\_r_4i8\_ .actor-man circle,#chatgpt-mermaid-\_r_4i8\_ line{fill:rgb(222, 234, 251);stroke-width:2px;}#chatgpt-mermaid-\_r_4i8\_ g rect.rect{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_4i8\_ .node .neo-node{stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_4i8\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_4i8\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].node .neo-line path{stroke:rgb(83, 154, 248);filter:none;}#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_4i8\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_4i8\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i8\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_4i8\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4i8\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}loop[Provider Delta]stream(request)execute_stream(request)stream(provider_request)acompletion(stream=True)Raw DeltaProviderEventLLMEventtext / reasoning / tool_deltaFinish + UsageCompletion EventCompleted + UsageFinal Event

#### 统一事件类型

建议首期保留以下事件：

| Event             | 作用          |
| ----------------- | ----------- |
| `message_delta`   | 文本增量        |
| `reasoning_delta` | 推理内容增量      |
| `tool_call_delta` | 工具调用参数增量    |
| `usage`           | Token 与成本信息 |
| `completed`       | 正常结束        |
| `failed`          | 调用失败        |
| `cancelled`       | 调用取消        |

对于 JEV 类型决策模型，可以增加 `decision_result`，但不建议为每种决策元语分别定义一套 Streaming Engine。

这里应保持一个重要约束：

ProviderAdapter 负责协议归一，CallExecutor 负责生命周期；不要让两个模块重复解析和拼装相同的 Delta。

## 五、建议的 Python 工程目录

考虑到 DataAgent 首期规模，建议控制在下面这个结构，不要把每个类都拆成一个文件。

```

dataagent/
└── infrastructure/
    └── llm/
        ├── __init__.py
        ├── client.py             # LLMClient / CallExecutor
        │
        ├── contract/
        │   ├── models.py         # Request / Response / Message
        │   ├── events.py         # Streaming Event
        │   └── errors.py         # Error taxonomy
        │
        ├── registry.py           # ModelRegistry
        ├── policy.py             # CallPolicy / ExecutionPlan
        ├── telemetry.py          # OTel / Usage / Cost
        ├── streaming.py          # EventNormalizer
        │
        ├── adapters/
        │   ├── base.py           # ProviderAdapter Protocol
        │   ├── litellm.py        # LiteLLMAdapter
        │   └── native.py         # Optional NativeAdapter
        │
        └── config/
            ├── models.yaml
            └── settings.py

```

建议把 `LLMClient` 和 `CallExecutor` 放在同一文件，除非后续执行协调逻辑明显增长。

`Usage & Quota` 首期不单独建目录，用量归集由 `telemetry.py` 承担，进程内并发限制由 `client.py` 管理。

### 5.1 技术栈选型

| 组件               | 技术                                        | 建议            |
| ---------------- | ----------------------------------------- | ------------- |
| Python           | Python 3.12+                              | 推荐            |
| 多模型调用            | `litellm-core`                            | 核心依赖，锁版本验证    |
| 数据契约             | `pydantic` v2                             | 必选            |
| 配置               | `pydantic-settings` + YAML                | 推荐            |
| 异步执行             | `asyncio`                                 | 标准库           |
| 取消与超时            | `asyncio.timeout`                         | 优先标准库         |
| Retry / Fallback | LiteLLM Router / SDK                      | 优先复用          |
| Token / Cost     | LiteLLM 内置能力                              | 优先复用          |
| Trace            | `opentelemetry-api` / `opentelemetry-sdk` | 推荐            |
| HTTP             | SDK 内置；Native 可用 `httpx`                  | 按需            |
| Redis            | `redis.asyncio`                           | 首期不引入         |
| 数据库              | PostgreSQL / SQLAlchemy                   | Runtime 首期不需要 |
| JSON 加速          | `orjson`                                  | 性能验证后再引入      |

特别说明：如果实际锁定的 `litellm-core` 分发包不包含某项 LiteLLM Router 功能，应以安装版本的实际 API 为准，而不是假设所有 `litellm` 功能均可直接使用。

## 六、关键执行接口设计

建议公开接口保持极简：

```
class LLMClient:    async def generate(        self,        request: LLMRequest,    ) -> LLMResponse:        ...    def stream(        self,        request: LLMRequest,    ) -> AsyncIterator[LLMEvent]:        ...
```

不要把以下方法暴露给 Harness：

```
# 不建议公开client.retry(...)client.fallback(...)client.select_provider(...)client.count_cost(...)client.execute_tool(...)
```

这些要么属于内部实现，要么属于 Harness 的职责。

### 6.1 错误契约

建议定义统一错误分类：

```
class LLMErrorCode(StrEnum):    INVALID_REQUEST = "invalid_request"    MODEL_NOT_FOUND = "model_not_found"    CAPABILITY_UNSUPPORTED = "capability_unsupported"    CONTEXT_OVERFLOW = "context_overflow"    RATE_LIMITED = "rate_limited"    TIMEOUT = "timeout"    PROVIDER_UNAVAILABLE = "provider_unavailable"    STREAM_INTERRUPTED = "stream_interrupted"    CANCELLED = "cancelled"    BUDGET_EXCEEDED = "budget_exceeded"    INTERNAL_ERROR = "internal_error"
```

每个错误至少包含：

- `code`：稳定错误类型。
- `message`：可诊断信息。
- `retryable`：当前错误是否具备重试条件。
- `attempts`：已经执行的物理尝试次数。
- `provider_error`：脱敏后的原始错误信息。

`retryable` 只表示错误本身可能适合重试，不代表 Harness 应当立即重试。是否重新执行 Agent Step，仍由 Harness 决定。

### 6.2 Trace 层级

建议采用：

```
Agent Run Span                 # Harness
└── Agent Step Span            # Harness
    └── LLM Call Span          # LLM Runtime
        ├── LLM Attempt 1      # SDK/Adapter
        └── LLM Attempt 2      # SDK/Adapter
```

这能支持两个不同层面的分析：

- Harness：某个任务为什么失败、为什么消耗大量 Token。
- LLM Runtime：哪次调用发生了重试、切换了模型、产生了多少成本。

## 七、面向 DataAgent 的关键测试与验收

LLM Runtime 的质量重点不在于功能数量，而在于调用语义的正确性和失败行为的确定性。

| 测试维度           | 关键验收场景                                     |
| -------------- | ------------------------------------------ |
| Contract       | Thinking、Tool Call、Structured Output 的转换正确 |
| Model Registry | 固定模型不被擅自替换；不兼容模型被拒绝                        |
| Retry          | 429/503 按策略重试，且总次数不超限                      |
| Fallback       | 未授权不切换；能力不兼容不切换                            |
| Timeout        | 多次物理请求共享逻辑调用 Deadline                      |
| Cancel         | 取消后不继续重试或切换模型                              |
| Streaming      | 部分输出后中断，不产生重复或伪造完成事件                       |
| Usage          | 多次 Attempt 的成本正确归集，未知用量不伪造                 |
| Telemetry      | Call / Attempt Span 正确关联 Harness Trace     |
| Provider       | LiteLLM 与 Native Adapter 的公共契约一致           |

建议测试体系分成三层：

1. 单元测试：使用 FakeAdapter 测试 Registry、Policy、EventNormalizer。
2. 契约测试：验证 LiteLLMAdapter 与各 Provider 的协议转换。
3. 故障注入测试：模拟 429、Timeout、连接中断、部分流式输出、取消、Fallback 失败。

其中，故障注入测试的价值最高，因为它最直接地验证 Runtime 是否真正具备企业级可靠性。

## 八、分阶段实施建议

Phase 1：最小可用 Runtime

LLM Contract、LiteLLMAdapter、静态 Model Registry、Generate/Stream、基础 Telemetry。

验收：三类目标模型能通过统一接口调用。


以第二次输出的第四章（4.3～4.6）作为 UML 设计的基础，但不能原样采用，需要修正几处架构问题。

两次输出的核心原则一致，但第二次在类职责、Retry/Fallback 和 Streaming 的设计上，存在一些不够严谨的地方。

### 一、具体哪些地方有矛盾？

| 设计点                      | 第一次方案                      | 第二次方案                       | 最终裁决                        |
| ------------------------ | -------------------------- | --------------------------- | --------------------------- |
| 核心架构                     | 4 核心 + 2 扩展 + CallExecutor | Facade + Strategy + Adapter | 不冲突，采用第二次的类设计               |
| LLMClient / CallExecutor | CallExecutor 是内部协调器        | LLMClient 委托 CallExecutor   | 首期合并实现，不必拆两个类               |
| Model Registry           | 解析模型、能力校验                  | 解析模型及候选集合                   | 采用第二次，职责更完整                 |
| PolicyEngine             | 定义约束，SDK 执行机制              | 生成 ExecutionPlan            | 采用第二次，但不能重复执行 SDK 策略        |
| Retry / Fallback         | 优先委托 LiteLLM               | Runtime 也控制 Fallback 循环     | 需要修正，避免双重 Retry/Fallback    |
| Streaming                | EventNormalizer 薄封装        | CallExecutor 管生命周期          | 两者兼容，不需要独立 Streaming Engine |
| Usage & Quota            | 归集到 Telemetry / Client     | 类图中没有独立模块                   | 采用第一次，首期不独立建类               |
| NativeAdapter            | 按需扩展                       | 类图默认包含                      | 采用第一次，按需实现                  |

### 二、第二次方案最需要修正的三个问题

1\. Retry / Fallback 的执行权不够明确。

第二次的 4.5 流程图中，LiteLLM 负责 Retry，Runtime 又负责 Fallback，容易让人误解为默认需要两套执行机制。

更合理的原则是：

- PolicyEngine：决定允许什么，不负责执行重试。
- LiteLLM Router：在支持的范围内执行 Retry、Fallback、Backoff。
- CallExecutor：负责 Deadline、Cancel、能力校验和调用生命周期。
- 只有当 LiteLLM 无法满足跨 Adapter Fallback 等特殊需求时，CallExecutor 才承担有限的执行协调。

2\. CallExecutor 和 LLMClient 没必要过早拆分。

首期只是进程内 Python 模块，建议由 `LLMClient` 统一协调 Registry、Policy、Adapter 和 Telemetry。

未来调用生命周期变复杂，再提取 `CallExecutor`。

3\. Streaming 的事件转换职责需要唯一归属。

建议：

- LiteLLM SDK：产生原始 Delta。
- LiteLLMAdapter：转换为统一 `LLMEvent`。
- LLMClient：控制流式调用的生命周期、取消和终态。

不再单独建设 `StreamingEngine`，也避免 Adapter 和 CallExecutor 重复转换事件。

### 三、最终建议采用的 UML

这是我建议作为 DataAgent LLM Runtime 后续实现基线的精简版。

\#chatgpt-mermaid-\_r_4kf\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(13, 13, 13);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_4kf\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4kf\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_4kf\_ .error-icon{fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_4kf\_ .error-text{fill:rgb(13, 13, 13);stroke:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4kf\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_4kf\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_4kf\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_4kf\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_4kf\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_4kf\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_4kf\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4kf\_ .marker.cross{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_4kf\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_4kf\_ p{margin:0;}#chatgpt-mermaid-\_r_4kf\_ g.classGroup text{fill:rgb(83, 154, 248);stroke:none;font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:10px;}#chatgpt-mermaid-\_r_4kf\_ g.classGroup text .title{font-weight:bolder;}#chatgpt-mermaid-\_r_4kf\_ .cluster-label text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4kf\_ .cluster-label span{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4kf\_ .cluster-label span p{background-color:transparent;}#chatgpt-mermaid-\_r_4kf\_ .cluster rect{fill:rgb(243, 243, 243);stroke:rgba(0, 0, 0, 0.1);stroke-width:1px;}#chatgpt-mermaid-\_r_4kf\_ .cluster text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4kf\_ .cluster span{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4kf\_ .nodeLabel,#chatgpt-mermaid-\_r_4kf\_ .edgeLabel{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4kf\_ .noteLabel .nodeLabel,#chatgpt-mermaid-\_r_4kf\_ .noteLabel .edgeLabel{color:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4kf\_ .edgeLabel .label rect{fill:rgb(222, 234, 251);}#chatgpt-mermaid-\_r_4kf\_ .label text{fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4kf\_ .labelBkg{background:rgb(222, 234, 251);}#chatgpt-mermaid-\_r_4kf\_ .edgeLabel .label span{background:rgb(222, 234, 251);}#chatgpt-mermaid-\_r_4kf\_ .classTitle{font-weight:bolder;}#chatgpt-mermaid-\_r_4kf\_ .node rect,#chatgpt-mermaid-\_r_4kf\_ .node circle,#chatgpt-mermaid-\_r_4kf\_ .node ellipse,#chatgpt-mermaid-\_r_4kf\_ .node polygon,#chatgpt-mermaid-\_r_4kf\_ .node path{fill:rgb(222, 234, 251);stroke:rgb(83, 154, 248);stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ .divider{stroke:rgb(83, 154, 248);stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ g.clickable{cursor:pointer;}#chatgpt-mermaid-\_r_4kf\_ g.classGroup rect{fill:rgb(222, 234, 251);stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_4kf\_ g.classGroup line{stroke:rgb(83, 154, 248);stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ .classLabel .box{stroke:none;stroke-width:0;fill:rgb(222, 234, 251);opacity:0.5;}#chatgpt-mermaid-\_r_4kf\_ .classLabel .label{fill:rgb(83, 154, 248);font-size:10px;}#chatgpt-mermaid-\_r_4kf\_ .relation{stroke:rgb(143, 143, 143);stroke-width:1;fill:none;}#chatgpt-mermaid-\_r_4kf\_ .dashed-line{stroke-dasharray:3;}#chatgpt-mermaid-\_r_4kf\_ .dotted-line{stroke-dasharray:1 2;}#chatgpt-mermaid-\_r_4kf\_ [id$="-compositionStart"],#chatgpt-mermaid-\_r_4kf\_ .composition{fill:rgb(143, 143, 143)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ [id$="-compositionEnd"],#chatgpt-mermaid-\_r_4kf\_ .composition{fill:rgb(143, 143, 143)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ [id$="-dependencyStart"],#chatgpt-mermaid-\_r_4kf\_ .dependency{fill:rgb(143, 143, 143)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ [id$="-dependencyEnd"],#chatgpt-mermaid-\_r_4kf\_ .dependency{fill:rgb(143, 143, 143)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ [id$="-extensionStart"],#chatgpt-mermaid-\_r_4kf\_ .extension{fill:transparent!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ [id$="-extensionEnd"],#chatgpt-mermaid-\_r_4kf\_ .extension{fill:transparent!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ [id$="-aggregationStart"],#chatgpt-mermaid-\_r_4kf\_ .aggregation{fill:transparent!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ [id$="-aggregationEnd"],#chatgpt-mermaid-\_r_4kf\_ .aggregation{fill:transparent!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ [id$="-lollipopStart"],#chatgpt-mermaid-\_r_4kf\_ .lollipop{fill:rgb(222, 234, 251)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ [id$="-lollipopEnd"],#chatgpt-mermaid-\_r_4kf\_ .lollipop{fill:rgb(222, 234, 251)!important;stroke:rgb(143, 143, 143)!important;stroke-width:1;}#chatgpt-mermaid-\_r_4kf\_ .edgeTerminals{font-size:11px;line-height:initial;}#chatgpt-mermaid-\_r_4kf\_ .classTitleText{text-anchor:middle;font-size:18px;fill:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_4kf\_ .edgeLabel[data-look="neo"]{background-color:rgb(252, 252, 252);text-align:center;}#chatgpt-mermaid-\_r_4kf\_ .edgeLabel[data-look="neo"] p{background-color:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_4kf\_ .edgeLabel[data-look="neo"] rect{opacity:0.5;background-color:rgb(252, 252, 252);fill:rgb(252, 252, 252);}#chatgpt-mermaid-\_r_4kf\_ .label-icon{display:inline-block;height:1em;overflow:visible;vertical-align:-0.125em;}#chatgpt-mermaid-\_r_4kf\_ .node .label-icon path{fill:currentColor;stroke:revert;stroke-width:revert;}#chatgpt-mermaid-\_r_4kf\_ .node .neo-node{stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_4kf\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_4kf\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].node .neo-line path{stroke:rgb(83, 154, 248);filter:none;}#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_4kf\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_4kf\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4kf\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_4kf\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_4kf\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}LLMClient+generate(request) : LLMResponse+stream(request) : AsyncIterator«Pydantic Models»LLMContractLLMRequestLLMResponseLLMEventLLMErrorModelRegistry+resolve(intent, requirements)+get_capabilities(model_id)«Immutable Config»CallPolicytimeoutretryfallbackbudgetPolicyResolver+build_plan(request, model)CallTelemetry+start_call()+record_attempt()+record_usage()+finish_call()«Protocol»ProviderAdapter+generate(request)+stream(request)LiteLLMAdapter+generate(request)+stream(request)«Optional»NativeAdapter«Third-party SDK»LiteLLMCore+Router+acompletion()

### 四、最终架构原则

我建议后续统一以这五条为准：

1. LLMClient 是唯一入口和执行协调者，不提前拆分 CallExecutor。
2. ModelRegistry 管模型能力和解析，PolicyResolver 管调用约束，两者不负责网络请求。
3. LiteLLM 是 Retry/Fallback 等底层机制的首选执行者，Runtime 只补充企业约束和必要的执行协调。
4. ProviderAdapter 统一模型协议与流式事件，不建设独立 Streaming Engine。
5. Telemetry 管调用观测和用量归集，并发限制首期直接在 LLMClient 中实现。

最终结论：采用第二次的 UML 设计思路，以上面修正后的类图为准；采用第一次的模块边界和轻量化原则。

这样既能保持企业级 Runtime 应有的控制能力，又不会因为过度抽象而把一个轻量 Python SDK 包装成复杂框架。
