# **Compass Context Runtime 详细设计方案**

**设计定位：企业级 Agent 的模型上下文运行时，支持策略驱动、算法可扩展、Prompt 热更新与多模型适配。**

我建议最终采用：

> **ContextRuntime Facade + Context Pipeline + Strategy Registry + Context Schema + Prompt Runtime Support + Model/Token Budget**

这套设计最重要的原则是：**核心执行流程稳定，处理算法可替换，配置和 Prompt 可动态发布。**

具体而言：

- Context Runtime 不负责 Agent 规划、工具执行和模型推理。
- Context Runtime 负责每次模型调用前的上下文准备，以及必要的上下文生命周期维护。
- Collect、Select、Compact、Assemble 是四个稳定的处理阶段。
- 每个阶段通过 Strategy 接口支持不同算法，而不是把所有算法写死在 ContextManager 中。
- PromptOps 是独立平台，Context Runtime 只负责同步、加载和使用发布配置。
- 现有 Agent Runtime、PostgreSQL、Semantic Layer、Memory、Artifact Store 都应尽可能复用。

这次设计我会把**类型模型、接口、策略扩展、状态归属、协作时序、持久化和错误处理**一起定义清楚。

# **一、总体架构**

## **1. 逻辑组件**

**Agent Runtime**

Run / Step / Plan / Tool / Checkpoint

prepare() / observe()

## **ContextRuntime**

Facade

Context Pipeline

Collect

收集

Select

评价选择

Compact

压缩重构

Assemble

装配

**Context Schema**

统一数据契约

**Strategy Registry**

算法注册与选择

**Prompt Runtime**

同步、加载、渲染

**Model / Token Budget**

模型能力、计数、预算

**Existing Data Sources**

History / State / Tool / Semantic / Memory

**PromptOps Platform**

Version / Release / Evaluation

这里的 Pipeline 是一个**稳定的逻辑处理流程**，不要求所有阶段都拥有独立服务。

`Strategy Registry` 是进程内的算法注册表，不是新的数据库或服务发现系统。

## **2. 最终模块清单**

| 模块                     | 类型       | 核心职责                  |
| ---------------------- | -------- | --------------------- |
| `ContextRuntime`       | Facade   | 统一入口、协调执行、返回模型输入      |
| `ContextSchema`        | 数据契约     | 定义上下文、来源、权限、生命周期      |
| `ContextPipeline`      | Pipeline | 组织四个处理阶段              |
| `ContextCollector`     | Strategy | 从已有系统获取候选上下文          |
| `ContextSelector`      | Strategy | 评价、过滤、排序、选择           |
| `ContextCompactor`     | Strategy | 截断、摘要、卸载、重构           |
| `ContextAssembler`     | 核心组件     | Prompt 渲染、消息组织、模型输入装配 |
| `StrategyRegistry`     | 进程内注册表   | 注册、解析、实例化算法           |
| `ContextPolicy`        | 配置实体     | 定义场景阶段及策略参数           |
| `PromptLoader / Sync`  | 运行时支撑    | 版本加载、本地缓存、同步          |
| `ModelCapabilities`    | 数据契约     | 模型窗口、工具、输出等能力         |
| `TokenBudgetManager`   | 核心能力     | 计数、预算、预留与校验           |
| `RunConfigSnapshot`    | 持久化对象    | 固定发布配置与兼容性            |
| `ContextDecisionTrace` | 运行记录     | 记录选择、压缩和装配决策          |

其中 `ContextDecisionTrace` 本期可以先做轻量元数据记录，完整的 ContextOps 治理平台不在本模块建设范围内。

# **二、统一数据模型：Context Schema**

这里建议把 Schema 分为三层，而不是所有字段都塞进一个 ContextItem。

1. **数据层**：ContextItem、ContextPayload、ContextSource。
2. **运行层**：ContextRequest、ContextSelection、ContextAssembly。
3. **配置层**：ContextPolicy、StrategySpec、ModelCapabilities、RunConfigSnapshot。

## **1. ContextItem**

`ContextItem` 是可供模型消费的候选信息单元。

建议字段如下。

| 字段                 | 类型             | 必需 | 说明       |
| ------------------ | -------------- | -- | -------- |
| `item_id`          | str            | 是  | 唯一标识     |
| `kind`             | ContextKind    | 是  | 信息类型     |
| `payload`          | ContextPayload | 是  | 内容或内容引用  |
| `source`           | ContextSource  | 是  | 来源信息     |
| `tenant_id`        | str            | 是  | 租户隔离     |
| `access_scope`     | AccessScope    | 是  | 资源权限范围   |
| `trust_level`      | TrustLevel     | 是  | 来源信任级别   |
| `created_at`       | datetime       | 是  | 创建时间     |
| `source_version`   | str            | 否  | 来源版本     |
| `data_as_of`       | datetime       | 否  | 业务数据时间   |
| `expires_at`       | datetime       | 否  | 有效期      |
| `estimated_tokens` | int            | 否  | Token 估计 |
| `importance`       | float          | 否  | 可选的先验重要性 |
| `lineage`          | list[str]      | 否  | 派生来源     |
| `metadata`         | dict           | 否  | 扩展元数据    |

建议的 Pydantic 类型：

```
from datetime import datetimefrom enum import StrEnumfrom typing import Any, Literalfrom pydantic import BaseModel, Fieldclass ContextKind(StrEnum):    INSTRUCTION = "instruction"    USER_MESSAGE = "user_message"    ASSISTANT_MESSAGE = "assistant_message"    TOOL_CALL = "tool_call"    TOOL_RESULT = "tool_result"    METRIC = "metric"    SCHEMA = "schema"    KNOWLEDGE = "knowledge"    MEMORY = "memory"    SUMMARY = "summary"    PLAN = "plan"    TASK_STATE = "task_state"    ENVIRONMENT = "environment"class TrustLevel(StrEnum):    PLATFORM = "platform"    TENANT_CONFIG = "tenant_config"    USER_INPUT = "user_input"    TOOL_DATA = "tool_data"    EXTERNAL_DATA = "external_data"class ContextSource(BaseModel):    system: str    resource_id: str    version: str | None = None    uri: str | None = None
```

这里有三个设计约束：

- `importance` 是算法可用的评分信息，不是 ContextItem 的固有事实。不同 Selector 可以产生不同分数。
- `estimated_tokens` 与模型、Tokenizer 和序列化方式有关，不能长期视为通用固定值。
- `trust_level` 是数据来源标签，不能代替真实的访问授权。

另外，`INSTRUCTION` 可以用于描述 Prompt 资产或内部上下文，但不能因为某个外部来源的 ContextItem 声称自己是 `INSTRUCTION`，就自动提升为模型高优先级指令。

## **2. ContextPayload 的扩展**

建议第一期只支持四种载荷：

| 格式             | 适用对象              |
| -------------- | ----------------- |
| `text`         | 文本、知识片段、摘要        |
| `json`         | 指标定义、Schema、结构化结果 |
| `message`      | 历史对话、工具调用协议组      |
| `artifact_ref` | 大型 SQL 结果、报告、文件   |

对于历史消息，建议在内部保留原始角色、调用 ID、结果 ID 和顺序信息，不要仅转成拼接文本。

## **3. ContextRequest**

这是一次模型调用的上下文准备请求。

```
class ContextRequest(BaseModel):    tenant_id: str    run_id: str    step_id: str    call_id: str    scenario: str    phase: str    principal_id: str    authz_revision: str    release_id: str    state_revision: int    event_watermark: int    model_id: str    output_reserve_tokens: int    variables: dict[str, Any] = Field(default_factory=dict)
```

其中：

- `scenario` 是业务场景，例如 `gmv_analysis`。
- `phase` 是 Agent Runtime 当前确认的阶段，例如 `sql`。
- `release_id` 来自 RunConfigSnapshot。
- `state_revision` 防止使用已经变化的运行状态。
- `event_watermark` 固定本次收集的已提交事件边界。
- `authz_revision` 用于权限变更检测。

正式实现中，`tenant_id` 和 `principal_id` 应来自可信的运行身份上下文，而不是直接相信模型生成或用户提交的字段。

# **三、ContextPolicy：策略配置模型**

这是整个架构的关键。

我建议把 Policy 设计成**配置驱动的 Pipeline Strategy**。

一个 ContextPolicy 不包含具体算法代码，只包含：

- 哪个场景、哪个阶段使用；
- 各阶段选用什么策略；
- 策略的参数；
- Token 预算；
- 必须保留的信息；
- 使用的 Prompt；
- 失败时的行为。

## **1. StrategySpec**

```
class StrategySpec(BaseModel):    name: str    version: str    config: dict[str, Any] = Field(default_factory=dict)
```

例如：

```
selector:
  name: rule_based
  version: "1"
  config:
    required_kinds:
      - metric
      - schema
    max_optional_items: 12
```

未来可以替换为：

```
selector:
  name: reranker
  version: "2"
  config:
    model: rerank-model
    top_k: 20
    min_score: 0.72
```

策略算法需要先在代码中注册。远端配置只能选择已经部署、已批准的算法，不能通过配置动态加载任意 Python 代码。

## **2. ContextPolicy 完整结构**

```
class ContextPolicy(BaseModel):    policy_id: str    version: str    scenario: str    phase: str    collector: StrategySpec    selector: StrategySpec    compactor: StrategySpec    prompt_key: str    max_input_tokens: int | None = None    output_reserve_tokens: int = 4096    safety_margin_tokens: int = 1024    required_kinds: list[ContextKind] = Field(        default_factory=list    )    failure_mode: Literal[        "strict", "optional_degrade"    ] = "strict"
```

建议将不可变的策略内容与具体运行状态分离。`ContextPolicy` 属于发布配置，不能被单次 Run 的算法结果原地修改。

# **四、Strategy Registry：支持未来不同算法**

这里我建议使用 Python Protocol，而不是复杂的插件框架。

## **1. 三类可扩展策略**

```
class CollectStrategy(Protocol):    async def collect(        self,        request: ContextRequest,        policy: ContextPolicy,    ) -> list[ContextItem]:        ...class SelectStrategy(Protocol):    async def select(        self,        request: ContextRequest,        items: list[ContextItem],        policy: ContextPolicy,    ) -> "ContextSelection":        ...class CompactStrategy(Protocol):    async def compact(        self,        request: ContextRequest,        selection: "ContextSelection",        budget: "TokenBudget",        policy: ContextPolicy,    ) -> "ContextSelection":        ...
```

本期先实现最小算法：

| 阶段       | M1 策略                             | 后续扩展                  |
| -------- | --------------------------------- | --------------------- |
| Collect  | `default_collect`：按来源和阶段收集        | 按需检索、并行收集、增量更新        |
| Select   | `rule_based`：必要性、权限、优先级、预算        | Reranker、语义相关性、学习排序   |
| Compact  | `truncate_and_project`：完整组裁剪、结果投影 | LLM Summary、分层摘要、语义压缩 |
| Assemble | 固定的确定性装配器                         | 按模型协议适配，不建议任意替换核心安全规则 |

为什么 Assembler 不完全开放成任意算法？

因为角色权限、工具调用配对和模型请求协议属于系统不变量，不能被业务策略随意绕过。

未来可以扩展装配布局、消息模板和模型适配器，但必须保留统一的安全与协议校验。

## **2. Registry 最小实现**

```
class StrategyRegistry:    def __init__(self):        self._collectors = {}        self._selectors = {}        self._compactors = {}    def register_collector(self, name, version, factory):        self._collectors[(name, version)] = factory    def register_selector(self, name, version, factory):        self._selectors[(name, version)] = factory    def register_compactor(self, name, version, factory):        self._compactors[(name, version)] = factory    def resolve_selector(self, spec: StrategySpec):        factory = self._selectors[(spec.name, spec.version)]        return factory(spec.config)
```

其他类型的解析方法类似。

建议在启动时注册策略，加载 Release 时验证策略存在性、参数 Schema 和兼容性。未知策略必须失败，不能静默回退到默认算法。

# **五、Context Pipeline：四阶段协作**

## **1. Collect：收集**

收集阶段的输入是 ContextRequest 和 ContextPolicy。

输出是候选 ContextItem 集合。

典型来源：

- Run History；
- Task State；
- Tool Results；
- Semantic Layer；
- Schema；
- Knowledge；
- Memory；
- Summary。

这里不必建立独立 ContextProvider 框架。Collector 可以直接使用现有 Repository 和业务接口。

但要为每个来源提供轻量适配函数，将返回结果转换为统一 ContextItem。

## **2. Select：评价与选择**

建议将选择分成两个部分：

**不可绕过的约束过滤：**

- 租户和权限；
- 数据有效期；
- 来源可信度和使用边界；
- 必需信息是否齐全；
- 重复项与来源版本。

**可替换的选择算法：**

- 规则排序；
- 相关性评分；
- Top-K；
- Token 预算感知；
- Reranker；
- LLM 辅助选择。

建议返回的不只是 Item 列表，而是包含决策记录的 `ContextSelection`。

```
class ContextDecision(BaseModel):    item_id: str    action: Literal[        "selected", "dropped", "deferred"    ]    reason: str    score: float | None = Noneclass ContextSelection(BaseModel):    selected: list[ContextItem]    decisions: list[ContextDecision]    estimated_tokens: int
```

这样未来更换 Selector 时，可以比较不同算法到底选择了哪些信息。

## **3. Compact：压缩与重构**

这是最值得预留算法扩展的部分。

建议至少区分以下几类：

| 策略                     | 工作方式                         | 特点             |
| ---------------------- | ---------------------------- | -------------- |
| `truncate`             | 删除低优先级历史                     | 简单、确定性强        |
| `project`              | 将大型结构化结果转成必要字段和统计            | 适合 SQL、工具结果    |
| `summarize`            | 使用模型生成阶段摘要                   | 压缩率高，但存在事实丢失风险 |
| `hierarchical_summary` | 多级阶段摘要                       | 适合长任务          |
| `artifact_offload`     | 大内容保存在 Artifact，模型只接收必要视图与引用 | 适合大型结果         |
| `hybrid`               | 规则投影、裁剪、摘要组合                 | 企业复杂场景         |

注意，压缩不应只返回新文本，还需要保留：

- 原始 Item 引用；
- 压缩策略与版本；
- 压缩前后 Token；
- 必须保留的事实；
- 生成摘要使用的 Prompt 版本；
- 结果内容哈希；
- 必要的来源血缘。

建议增加一个数据类型：

```
class CompressionRecord(BaseModel):    strategy: str    strategy_version: str    source_item_ids: list[str]    result_item_id: str    before_tokens: int    after_tokens: int    prompt_version: str | None = None    model_id: str | None = None    source_hashes: list[str]    result_hash: str
```

如果使用 LLM 压缩，压缩器可以通过现有 LLM Runtime 发起内部调用，但必须：

- 使用独立的压缩预算；
- 限制递归压缩；
- 记录调用成本；
- 校验必要事实；
- 不产生真实业务工具副作用。

## **4. Assemble：装配**

Assembler 接收：

- 已选择和压缩的 ContextItem；
- 当前阶段 Prompt；
- Prompt 变量；
- 模型能力信息；
- 工具定义；
- 输出契约；
- Token 预算。

输出：

```
class AssembledContext(BaseModel):    model_input: Any    selected_item_ids: list[str]    prompt_versions: dict[str, str]    estimated_input_tokens: int    assembly_hash: str
```

这里的 `model_input` 应在正式实现中使用强类型的内部模型请求对象，而不是长期使用 `Any`。

Assembler 负责把信息组织成统一请求；具体模型 API 的编码仍由 LLM Runtime 负责。

# **六、Model & Token Budget：必须成为独立的基础能力**

这是你上一轮补充的关键点。

**ContextPolicy 不能脱离具体模型能力独立决定上下文预算。**

同一个任务，使用不同模型时，可能有不同的上下文窗口、输出限制、工具协议和 Token 计数方式。

但我不建议 Context Runtime 再维护一套完整的 Model Registry。Compass 已经有 LLM Runtime，模型能力的权威来源应该在那里。

## **1. ModelCapabilities：模型能力契约**

```
class ModelCapabilities(BaseModel):    model_id: str    provider: str    config_revision: str    context_window_tokens: int    max_output_tokens: int    supports_tool_calling: bool    supports_parallel_tool_calls: bool    supports_structured_output: bool    tokenizer_id: str | None = None    tokenizer_revision: str | None = None    # 模型可能还存在单独的输入上限    max_input_tokens: int | None = None    # 用于核算成本的价格配置版本    pricing_revision: str | None = None
```

这里要注意：

- `context_window_tokens` 是模型支持的上下文总窗口。
- `max_output_tokens` 是模型允许的最大输出长度。
- `max_input_tokens` 如果存在，是独立输入限制。
- 不同供应商对推理 Token、工具消息、缓存 Token 的计费和计数方式不同。

因此，Context Runtime 只能通过 LLM Runtime 的能力接口获取这些信息，不能把模型窗口硬编码在 ContextPolicy 里。

## **2. TokenCounter：模型相关的计数接口**

```
class TokenCounter(Protocol):    async def count(        self,        model: ModelCapabilities,        model_input: "ModelInput",    ) -> "TokenEstimate":        ...
```

返回：

```
class TokenEstimate(BaseModel):    input_tokens: int    method: Literal[        "provider_exact",        "local_tokenizer",        "estimated"    ]    uncertainty_tokens: int = 0
```

建议支持三级能力：

| 方式            | 说明                  | 使用优先级 |
| ------------- | ------------------- | ----- |
| Provider 原生计数 | 模型厂商提供请求 Token 计数接口 | 优先    |
| 本地 Tokenizer  | 对指定模型和消息格式进行计数      | 其次    |
| 估算            | 使用近似算法并保留安全余量       | 兜底    |

这里的“精确”是相对于当前供应商计数接口及请求编码而言，不应假设不同模型的 Tokenizer 可以互换。

尤其是工具定义、结构化输出 Schema、图片和其他多模态内容，简单地对字符串计数并不可靠。

## **3. TokenBudget：每次调用动态计算**

建议使用：

\\[ B\_{\text{input}} = \min(W-R-M,\ I\_{\max},\ P\_{\max}) \\]

其中：

- \\(W\\)：模型上下文窗口；
- \\(R\\)：本次输出 Token 预留；
- \\(M\\)：安全余量；
- \\(I\_{\max}\\)：模型独立输入上限，若不存在则忽略；
- \\(P\_{\max}\\)：ContextPolicy 配置的输入预算上限。

进一步拆分：

\\[ B\_{\text{input}} = B\_{\text{instructions}} + B\_{\text{tools}} + B\_{\text{history}} + B\_{\text{evidence}} + B\_{\text{task}} + B\_{\text{overhead}} \\]

注意这不是要求每部分固定配额，而是用于预算核算和分配。

建议预算分配采用：

1. 优先保证协议开销、必要指令和工具契约。
2. 保证任务目标、关键约束和必要业务事实。
3. 根据阶段分配历史和证据的可变预算。
4. 对可选内容执行排序、裁剪和压缩。
5. 装配后重新计算整个请求的 Token。

## **4. TokenBudgetManager 的接口**

```
class TokenBudgetManager:    async def plan(        self,        model: ModelCapabilities,        policy: ContextPolicy,        request: ContextRequest,    ) -> "TokenBudget":        ...    async def validate(        self,        model_input: "ModelInput",        budget: "TokenBudget",    ) -> "BudgetValidation":        ...
```

建议数据结构：

```
class TokenBudget(BaseModel):    model_id: str    context_window: int    input_limit: int    output_reserve: int    safety_margin: int    required_tokens: int = 0    optional_tokens: int = 0    estimated_total: int = 0    remaining_tokens: int = 0
```

这里的 `remaining_tokens` 是当前装配估计值，不能当成真实 API 剩余窗口。

对于模型厂商提供的特殊推理预算，需要在 Model Adapter 中映射，不能简单假设所有模型都遵循相同公式。

## **5. Usage 反馈如何参与下一轮决策**

模型调用完成后，LLM Runtime 返回实际 Usage：

```
class ModelUsage(BaseModel):    input_tokens: int    output_tokens: int    cached_input_tokens: int | None = None    reasoning_tokens: int | None = None    cost: float | None = None    latency_ms: int | None = None
```

这里应区分三个指标：

| 指标           | 作用               |
| ------------ | ---------------- |
| 当前请求估计 Token | 判断本次能否发送         |
| 实际模型 Usage   | 统计本次调用成本，校准估算误差  |
| Run 累计 Usage | 判断后续任务是否继续、降级或停止 |

**不能用累计 Usage 直接推断下一次请求的上下文长度。**

例如，一个 Run 已消耗 100 万 Token，不代表下一次请求需要 100 万 Token；每次模型调用的输入窗口是独立计算的。

累计 Usage 应进入 Agent Runtime 的预算治理，Context Runtime 可以读取剩余额度并调整可选上下文预算。

## **6. 推荐的预算协作机制**

1. Agent Runtime 提供当前模型配置和 Run 剩余额度
2. Context Runtime 向 LLM Runtime 查询 ModelCapabilities
3. TokenBudgetManager 计算本次输入预算
4. Selector 根据预算选择候选上下文
5. Compactor 对超预算内容执行压缩
6. Assembler 构造完整 ModelInput
7. LLM Runtime 完成实际请求编码和最终 Token 校验
8. 调用结束后，Usage 回写 Agent Runtime，并供后续 Context 决策使用

对于超预算的处理，建议设置有界重试，例如最多两轮“选择/压缩/重新装配”。

如果必需内容仍然放不下，返回 `ContextBudgetExceeded`，而不是无限循环压缩。

# **七、补齐运行时数据实体**

前面定义了 ContextItem、ContextRequest、ContextPolicy 和 StrategySpec。为了真正支撑可扩展算法，还需要明确策略执行的输入输出契约。

## **1. ContextCandidateSet**

Collector 不直接返回最终上下文，而是候选集。

```
class ContextCandidateSet(BaseModel):    items: list[ContextItem]    source_status: dict[str, str]    collected_at: datetime    state_revision: int    event_watermark: int
```

它解决三个问题：

- 哪些来源成功返回？
- 哪些来源超时或失败？
- 候选信息对应哪个运行状态边界？

M1 可以直接在内存中构造，不需要持久化整份候选集。

## **2. ContextSelection**

建议不要只返回选中的 Item。

```
class ContextSelection(BaseModel):    selected: list[ContextItem]    decisions: list[ContextDecision]    strategy_name: str    strategy_version: str    estimated_tokens: int
```

原因是未来你需要比较：

- Rule-based Selector 和 Reranker 选中了什么？
- 为什么指标口径被丢弃？
- 某次算法升级是否导致关键事实缺失？

有了决策记录，后续 ContextOps 才有可追踪的基础。

## **3. ContextCompactionResult**

建议把压缩结果与选择结果分开。

```
class ContextCompactionResult(BaseModel):    items: list[ContextItem]    records: list[CompressionRecord]    before_tokens: int    after_tokens: int    required_facts_preserved: bool
```

这里的 `required_facts_preserved` 不能由压缩模型自行声明就认为通过。

对于指标公式、金额、时间范围、SQL 字段等，应优先采用确定性校验。不能可靠验证时，应将结果标记为待验证或直接拒绝替换原内容。

## **4. PreparedContext**

这是 ContextRuntime 对 Agent Runtime 的最终返回对象。

```
class PreparedContext(BaseModel):    model_input: "ModelInput"    release_id: str    policy_id: str    selected_item_ids: list[str]    compression_records: list[CompressionRecord]    estimated_input_tokens: int    context_hash: str    decision_trace_id: str
```

注意 `context_hash` 应明确是内部装配对象的哈希。LLM Runtime 完成实际供应商协议编码后，还需要记录 `wire_request_hash`。

这样才能区分：

- Context Runtime 构造了什么；
- 模型 API 实际接收了什么。

# **八、完整执行时序**

一次 SQL 分析调用的典型执行顺序如下。

LLM RuntimeContextAssemblerTokenBudgetStrategyRegistryContextManagerPromptLoaderContextRuntimeAgentExecutorLLM RuntimeContextAssemblerTokenBudgetStrategyRegistryContextManagerPromptLoaderContextRuntimeAgentExecutor#chatgpt-mermaid-\_r_ago\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(13, 13, 13);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_ago\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_ago\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_ago\_ .error-icon{fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_ago\_ .error-text{fill:rgb(13, 13, 13);stroke:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_ago\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_ago\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_ago\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_ago\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_ago\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_ago\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_ago\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_ago\_ .marker.cross{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_ago\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_ago\_ p{margin:0;}#chatgpt-mermaid-\_r_ago\_ .actor{stroke:rgb(83, 154, 248);fill:rgb(222, 234, 251);stroke-width:1;}#chatgpt-mermaid-\_r_ago\_ rect.actor.outer-path[data-look="neo"]{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_ago\_ rect.note[data-look="neo"]{stroke:rgb(107, 198, 127);fill:rgb(243, 243, 243);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_ago\_ text.actor>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_ago\_ .actor-line{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_ago\_ .innerArc{stroke-width:1.5;stroke-dasharray:none;}#chatgpt-mermaid-\_r_ago\_ .messageLine0{stroke-width:1.5;stroke-dasharray:none;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_ago\_ .messageLine1{stroke-width:1.5;stroke-dasharray:2,2;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_ago\_ [id$="-arrowhead"] path{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_ago\_ .sequenceNumber{fill:#707070;}#chatgpt-mermaid-\_r_ago\_ [id$="-sequencenumber"]{fill:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_ago\_ [id$="-crosshead"] path{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_ago\_ .messageText{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_ago\_ .labelBox{stroke:rgba(0, 0, 0, 0.1);fill:rgb(252, 252, 252);filter:none;}#chatgpt-mermaid-\_r_ago\_ .labelText,#chatgpt-mermaid-\_r_ago\_ .labelText>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_ago\_ .loopText,#chatgpt-mermaid-\_r_ago\_ .loopText>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_ago\_ .sectionTitle,#chatgpt-mermaid-\_r_ago\_ .sectionTitle>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_ago\_ .loopLine{stroke-width:2px;stroke-dasharray:2,2;stroke:rgba(0, 0, 0, 0.1);fill:rgba(0, 0, 0, 0.1);}#chatgpt-mermaid-\_r_ago\_ .note{stroke:rgb(107, 198, 127);fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_ago\_ .noteText,#chatgpt-mermaid-\_r_ago\_ .noteText>tspan{fill:rgb(13, 13, 13);stroke:none;font-weight:normal;}#chatgpt-mermaid-\_r_ago\_ .activation0{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_ago\_ .activation1{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_ago\_ .activation2{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_ago\_ .actorPopupMenu{position:absolute;}#chatgpt-mermaid-\_r_ago\_ .actorPopupMenuPanel{position:absolute;fill:rgb(222, 234, 251);box-shadow:0px 8px 16px 0px rgba(0,0,0,0.2);filter:drop-shadow(3px 5px 2px rgb(0 0 0 / 0.4));}#chatgpt-mermaid-\_r_ago\_ .actor-man circle,#chatgpt-mermaid-\_r_ago\_ line{fill:rgb(222, 234, 251);stroke-width:2px;}#chatgpt-mermaid-\_r_ago\_ g rect.rect{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_ago\_ .node .neo-node{stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_ago\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_ago\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].node .neo-line path{stroke:rgb(83, 154, 248);filter:none;}#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_ago\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_ago\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_ago\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_ago\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_ago\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}prepare(run, step)load_exact(release_id)Prompt + ContextPolicyget_model_capabilities()ModelCapabilitiesplan(model, policy)TokenBudgetresolve(strategy specs)Collector / Selector / Compactorcollect(request)CandidateSetselect(candidates, budget)Selectioncompact(selection, budget)CompactionResultassemble(prompt, items, budget)ModelInput + Manifestencode + validate requestvalidated requestPreparedContextinvoke(prepared request)response + usagecommit response / usage

这里有一个实现上的取舍：

`ContextManager` 可以直接封装 Collect、Select、Compact 三个阶段，也可以由 `ContextPipeline` 分别调用三个 Strategy。

**我更推荐由 Pipeline 调用 Strategy，ContextManager 负责上下文生命周期和结果协调。**

这样后续新增算法时，不需要持续修改 ContextManager 的内部实现。

# **九、Context Runtime 的核心实现骨架**

下面给出更接近真实工程的接口。

```
class ContextRuntime:        # 强制安全检查，不由可替换算法决定        candidates = await self.manager.authorize_and_validate(            request, candidates        )        selection = await selector.select(            request,            candidates.items,            policy,        )        selection = self.manager.enforce_required_context(            selection, policy        )        compacted = await compactor.compact(            request,            selection,            budget,            policy,        )        self.manager.verify_compaction(            selection, compacted, policy        )        prompt = release.resolve_prompt(            request.scenario,            request.phase,        )        assembled = self.assembler.assemble(            prompt=prompt,            selected=compacted,            request=request,            budget=budget,        )        validated = await self.llm.validate_model_input(            assembled.model_input,            model,            budget,        )        return PreparedContext(            model_input=validated.model_input,            release_id=release.release_id,            policy_id=policy.policy_id,            selected_item_ids=[                item.item_id for item in compacted.items            ],            compression_records=compacted.records,            estimated_input_tokens=validated.input_tokens,            context_hash=assembled.context_hash,            decision_trace_id=assembled.trace_id,        )
```

这是核心流程示意，并非完整可运行实现。实际工程还需要加入状态版本检查、权限修订检查、并发控制、有限预算重试、异步 Trace 写入和异常分类。

尤其要注意：**可替换策略不能绕过系统级安全校验。**

建议将权限检查、协议完整性和必需事实校验设计为固定的 Pipeline Guard，而不是普通策略。

# **十、策略扩展机制：如何真正做到未来可替换算法**

这里我建议采用 **Strategy Pattern + Registry + Declarative Configuration**，而不是直接引入 LangGraph 这样的编排框架。

核心思想是：

**Pipeline 的执行阶段不变，Strategy 的实现可以替换。**

## **1. 三种扩展方式**

| 扩展方式    | 示例                                | 是否需要代码部署 |
| ------- | --------------------------------- | -------- |
| 修改参数    | Top-K 从 10 改为 20，压缩阈值从 0.8 改为 0.7 | 不需要      |
| 切换已注册算法 | `rule_based` 切换到 `reranker`       | 不需要      |
| 增加新算法实现 | 新增 `semantic_compactor_v3`        | 需要       |

这是配置热更新与算法代码部署的边界。

算法版本和 Prompt 版本应该分别管理。一个压缩算法可能使用不同的摘要 Prompt，因此需要支持：

```
compactor:
  name: llm_summary
  version: "2"
  config:
    prompt_key: context.summary
    max_summary_tokens: 2000
    preserve_facts: true
```

这里的 `context.summary` 应当在当前 Release 中解析为不可变的 Prompt 版本。

## **2. 示例：三套不同的上下文策略**

Context Pipeline 策略组合示例

M1 基础M2 增强M3 长任务

Collect

Default

Select

Rule-based

Compact

Truncate + Project

**适合当前 Compass**

确定性规则为主，成本可控，容易调试和验证。

这三套不是三种不同的 Context Runtime，而是同一个 Runtime 使用不同的 Policy 和 Strategy。

## **3. 策略失败时怎么处理**

建议明确失败语义：

- Collector 获取必需来源失败：返回错误，由 Agent Runtime 决定是否重试或补充数据。
- Selector 失败：只有 Policy 明确允许且必需项仍可验证时，才能使用指定的确定性回退策略。
- Compactor 失败：可以保留原文并重新裁剪可选项，但不能把未经验证的摘要当成可信事实。
- Assembler 失败：阻止模型调用，不使用未经校验的消息请求。

策略切换、回退和失败原因都应该写入 Context Decision Trace。

# **十一、持久化设计：只保存必要的运行事实**

Context Runtime 不需要独立维护一套 ContextStore。

建议复用 Compass 现有 PostgreSQL 和 Artifact Store，只增加必要的数据对象。

| 数据对象                   | 存储位置                   | 是否持久化    |
| ---------------------- | ---------------------- | -------- |
| Prompt 版本、发布包          | 独立 PromptOps 平台        | 是        |
| 本地 Prompt 镜像           | Worker 本地文件缓存          | 是，缓存性质   |
| RunConfigSnapshot      | Compass PostgreSQL     | 是        |
| Run History、Task State | 现有 Agent Runtime Store | 是        |
| Tool Results           | 现有 Tool/Artifact Store | 是        |
| ContextItem            | 内存中的统一视图               | 通常不单独持久化 |
| ContextPolicy          | PromptOps 发布包和本地镜像     | 是        |
| ContextSelection       | 当前调用内存                 | 默认不保存全文  |
| CompressionRecord      | Compass PostgreSQL     | 建议保存     |
| ContextDecisionTrace   | Compass PostgreSQL     | 建议保存轻量记录 |
| ContextSnapshot        | 受控对象存储或可重建引用           | 按需保存     |

## **1. 最小新增 PostgreSQL 表**

```
CREATE TABLE run_config_snapshots (
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    release_hash TEXT NOT NULL,
    config_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id)
);

CREATE TABLE context_assemblies (
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,

    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    release_id TEXT NOT NULL,

    state_revision BIGINT NOT NULL,
    authz_revision TEXT NOT NULL,

    input_tokens_estimated INTEGER,
    input_tokens_actual INTEGER,

    assembly_hash TEXT,
    wire_request_hash TEXT,

    decision_manifest JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (tenant_id, call_id, attempt_id)
);
```

这里 `input_tokens_actual` 需要等待模型调用返回 Usage 后更新。

`decision_manifest` 记录引用、选择原因、压缩动作和策略版本，不默认保存所有原始敏感内容。

实际建表时还应补充租户隔离策略、外键、查询索引、保留期限和删除机制。

## **2. Context Runtime 是否需要独立的状态？**

需要，但应当非常少。

它只需要管理：

- 当前模型调用的临时 Pipeline 状态；
- 可复用的本地 Prompt 缓存；
- 策略实例和模型能力缓存；
- 装配结果与决策记录。

长期任务状态、计划、消息历史、工具结果继续由 Agent Runtime 持有。

这可以避免 Context Runtime 与 Agent Runtime 出现双写和状态不一致。

# **十二、完整 Python 项目结构**

```
compass/
├── agent/
│   ├── executor.py
│   ├── run_service.py
│   └── state.py
│
├── context/
│   ├── runtime.py
│   ├── pipeline.py
│   ├── manager.py
│   ├── assembler.py
│   │
│   ├── schema/
│   │   ├── item.py
│   │   ├── request.py
│   │   ├── selection.py
│   │   ├── compression.py
│   │   └── assembly.py
│   │
│   ├── policy/
│   │   ├── models.py
│   │   └── resolver.py
│   │
│   ├── strategies/
│   │   ├── protocols.py
│   │   ├── registry.py
│   │   ├── collect.py
│   │   ├── select.py
│   │   └── compact.py
│   │
│   ├── budget/
│   │   ├── models.py
│   │   └── manager.py
│   │
│   ├── prompt/
│   │   ├── loader.py
│   │   ├── sync.py
│   │   ├── renderer.py
│   │   └── cache.py
│   │
│   └── trace/
│       └── recorder.py
│
├── llm/
│   ├── runtime.py
│   ├── capabilities.py
│   └── token_counter.py
│
└── storage/
    ├── run_repository.py
    └── context_repository.py
```

这是**逻辑模块目录**，不是要求第一期创建同等数量的类或文件。

例如，M1 可以把 `strategies/collect.py`、`select.py`、`compact.py` 各实现一个默认策略；`trace/recorder.py` 只记录轻量 Manifest。

# **十三、关键工程不变量**

无论未来替换什么算法，都建议保持以下规则：

1. **策略只能选择已授权的数据**，不能通过评分、压缩或摘要绕过权限。
2. **必需信息不能静默丢失**，尤其是指标口径、Schema、用户约束和工具协议关系。
3. **压缩结果必须保留来源血缘**，不能将低信任数据压缩成高优先级指令。
4. **Prompt 和策略版本必须可追踪**，生产 Run 默认固定不可变发布包。
5. **模型能力来自 LLM Runtime**，Context Runtime 不维护第二套模型权威配置。
6. **Token 预算最终以实际模型请求为准**，不能只对 ContextItem 文本长度计数。
7. **Context Runtime 不负责 Agent 的执行决策**，策略执行失败由 Agent Runtime 处理后续重试、重规划或终止。
8. **扩展算法不能破坏模型协议和安全边界**，系统级 Guard 始终保留。

# **十四、最终推荐的工程分层**

| 层次        | 核心对象                                                 | 设计模式                                |
| --------- | ---------------------------------------------------- | ----------------------------------- |
| 对外入口      | `ContextRuntime`                                     | Facade                              |
| 执行编排      | `ContextPipeline`                                    | Pipeline                            |
| 数据契约      | `ContextSchema`                                      | Typed Schema                        |
| 动态算法      | `CollectStrategy`、`SelectStrategy`、`CompactStrategy` | Strategy                            |
| 算法选择      | `StrategyRegistry`                                   | Registry / Factory                  |
| 上下文生命周期   | `ContextManager`                                     | Domain Service                      |
| 模型输入构造    | `ContextAssembler`                                   | Builder / Composer                  |
| 配置        | `ContextPolicy`                                      | Declarative Configuration           |
| Prompt 支撑 | `PromptLoader`、`PromptSync`                          | Repository Adapter / Local Cache    |
| 模型约束      | `ModelCapabilities`、`TokenBudgetManager`             | Capability Adapter / Budget Service |
| 配置一致性     | `RunConfigSnapshot`                                  | Immutable Snapshot                  |

## **我的最终建议**

**本期真正必须实现的是：**

`ContextRuntime`、`ContextSchema`、`ContextPolicy`、`ContextPipeline`、三个 Strategy Protocol 及其默认实现、`ContextAssembler`、`PromptLoader`、`TokenBudgetManager`。

`StrategyRegistry` 只需要几十行注册与解析代码；`ContextManager` 保持薄实现，不必成为一个复杂的状态管理框架。

未来增加新的收集算法、Reranker、LLM Summary 或分层压缩时，只需要实现对应 Strategy，注册版本，再通过 ContextPolicy 选择。

最重要的是，**把稳定的上下文处理机制与可变化的算法、Prompt 和配置彻底分离**。这样 Compass 第一版能够快速落地，而未来上下文工程能力的演进不会反复侵入 AgentExecutor。
