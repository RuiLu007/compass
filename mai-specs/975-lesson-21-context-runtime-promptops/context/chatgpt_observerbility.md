你这个问题实际上是在厘清 **Context Runtime 的可观测性，相对于已有 LLM Runtime Observability，究竟有什么不可替代的增量价值。**

我的判断是：

**LLM Runtime Observability 解决“模型调用发生了什么”；Context Runtime Observability 解决“为什么模型最终看到了这些信息，而没有看到另外一些信息”。**

如果底层已经接入 Langfuse、LangSmith 或 OpenTelemetry，Context Runtime 就不应该再造一套 Trace、Token 统计、模型调用日志。

它真正应该补的是三项能力：

1. **Context Decision Trace：上下文决策可解释。**
2. **Context Lineage：上下文来源与变换可追溯。**
3. **Context Policy Replay：上下文策略可复现、可比较。**

这三项能力的共同特点是：它们需要知道 Context Runtime 内部的候选信息、选择过程和策略版本。**底层 LLM Runtime 只接收最终请求，天然无法还原这些中间决策。**

## 一、先看两层可观测性的职责边界

| 维度                    | LLM Runtime Observability（已有） | Context Runtime 增量 |
| --------------------- | ----------------------------- | ------------------ |
| Model Call Trace      |                               | 直接复用               |
| 模型输入 `messages[]`     |                               | 不重复保存              |
| 模型输出                  |                               | 不重复保存              |
| Token、成本、延迟           |                               | 补充装配预算及优化收益        |
| Prompt 版本             | 可记录                           | 补充模块选择、渲染和装配关系     |
| Context 候选集           | 通常不可见                         | **必须补充**           |
| Context 过滤、选择、排序      | 通常不可见                         | **必须补充**           |
| Context 压缩与转换         | 只看到最终内容                       | **补充变换前后关系**       |
| Context 来源与版本         | 不一定可见                         | **补充数据血缘**         |
| Context Policy 版本     | 需要应用上报                        | **必须补充**           |
| Context Policy Replay | 不属于底层默认职责                     | **核心增量**           |
| 权限与安全审计               | 通用调用审计                        | 补充上下文访问及治理决策       |

这里有一个重要前提：这些并不是说 Langfuse 或 LangSmith 在技术上不能保存自定义数据。它们可以。

区别在于：**底层观测平台能够承载数据，但只有掌握上下文装配过程的组件，才能产生这些数据。**

所以更准确的架构是：

> Context Runtime 负责产生上下文决策证据，复用现有 Observability 基建进行存储、关联、检索和展示。

而不是重新建设一个独立的 Context Observability Platform。

## 二、最有价值的增量：从 Call Trace 升级到 Decision Trace

以 Compass 的 GMV 分析为例。

假设 SQL Generator 最终生成了一条错误 SQL：

```
SELECT SUM(order_amount)
FROM orders
WHERE created_at >= '2026-10-01';
```

而正确的 GMV 口径要求使用 `paid_amount` 和 `paid_at`，并排除取消订单。

### LLM Runtime 能告诉我们什么？

- 这次 Model Call 输入了什么。
- 模型输出了什么 SQL。
- 调用的模型和参数。
- 消耗了多少 Token。
- 调用是否成功。

但如果模型输入里没有 GMV 指标口径，它无法解释**为什么没有**。

### Context Runtime 能进一步回答什么？

同一次模型调用的上下文决策记录

**Candidate Context** (8 items)

候选上下文：Schema、GMV 口径、历史摘要、任务状态、工具结果等

Context Selection Policy v12

**Selected · 5**

Schema、用户任务、历史摘要等

**Dropped · 3**

其中 GMV 口径因 Token Budget 被裁掉

**Final Model Input**

交给已有 LLM Runtime，关联 Model Call ID

示例数据，用于说明 Context Runtime 特有的决策证据。

核心增量不在于记录 `messages[]`，而在于记录：

**候选上下文 → 决策过程 → 最终上下文**

这是 Context Runtime 最应该提供的可观测性。

## 三、我建议把增量能力收敛为四个，而不是建设完整的观测平台

| 增量能力                           | 核心回答                | 优先级 |
| ------------------------------ | ------------------- | --- |
| **1. Context Decision Trace**  | 为什么选中、丢弃、压缩这条信息？    | P0  |
| **2. Context Lineage**         | 信息从哪里来，经历了哪些转换？     | P0  |
| **3. Context Policy Replay**   | 固定候选输入后，新旧策略产生什么差异？ | P1  |
| **4. Context Quality Metrics** | 策略是否真正提升任务质量和效率？    | P1  |

### 1. Context Decision Trace：记录决策，而非重复记录内容

这是最小必需能力。

建议在 Context Runtime 每次装配时产生一条 `AssemblyDecision`：

```
AssemblyDecision(    call_id="C-17",    profile_version="sql@4",    policy_version="selection@12",    candidate_count=8,    selected_count=5,    dropped_count=3,    decisions=[        {            "item_id": "gmv-definition-v7",            "action": "DROP",            "reason": "TOKEN_BUDGET",            "priority": "OPTIONAL",        }    ],)
```

这段数据可以作为现有 Langfuse 的自定义 Observation 或 Metadata，也可以映射为 OTel Span/Event。

Langfuse 官方已支持嵌套 Observation 和可查询的 Metadata，因此无须另建追踪系统。

这里还要记录 Profile 如何把 `GMV` 口径标为 `OPTIONAL`，否则只能定位到裁剪动作，无法进一步找到配置错误。

### 2. Context Lineage：建立信息血缘，而不是复制工具日志

例如：

**Tool Result #16**

Metric Provider 返回 GMV Definition v7

转换为 Context Item

**Context Item #42**

source_ref=ToolResult#16 · version=v7

Selection Policy：DROP

**Assembly Decision #17**

GMV Definition 未进入最终请求

注意，底层已经记录了 Tool Result，Context Runtime 不需要再保存一份。

只需要建立：

`source_ref → context_item_id → transformation → assembly_decision → model_call_id`

这样才能回答“工具明明返回了正确数据，为什么模型没有收到”。

如果内容被摘要，还需要记录摘要前后的引用与版本；如果没有发生转换，就不必制造额外节点。

### 3. Context Policy Replay：这是最值得单独建设的回放能力

**LLM Replay 重跑模型；Context Replay 重跑上下文装配策略。**

两者不是同一件事。

假设：

- Policy v1：规则过滤 + 固定 Token Budget。
- Policy v2：规则过滤 + 关键事实保护 + 动态预算。

固定同一份候选上下文，分别执行两套策略：

**Context-only Replay 示例** (不调用 LLM)

- Policy v1
- Policy v2\
  当前所选项不可用。

Selected Context

Estimated Tokens

GMV 关键口径

---

必需事实保留率

以上为构造的示意数据，可切换策略观察装配结果差异，不代表真实实验结论。

这种回放有三个非常实际的价值：

- **故障复现**：验证相同输入和策略版本能否重现错误装配。
- **策略回归**：修改 Selection/Compression Policy 后，检查关键事实是否被保留。
- **成本优化**：不调用昂贵的模型，也能比较上下文大小、选择结果和预算消耗。

但它只能证明装配结果的差异。要验证 SQL 正确率和最终经营结论，还需要结合 Model Replay 或 Task Evaluation。

对于有模型驱动的摘要、重排等非确定性环节，应冻结其输出或单独记录依赖，否则不能承诺装配回放完全确定。

### 4. Context Quality Metrics：让观测数据服务于优化

LLM Runtime 已有 Token、Cost、Latency。

Context Runtime 应增加少量与自身职责直接相关的指标：

| 指标                              | 价值                      |
| ------------------------------- | ----------------------- |
| Required Context Retention Rate | 必需信息是否完整保留              |
| Context Drop Reasons            | 为什么上下文被丢弃               |
| Context Budget Utilization      | 预算分配是否合理                |
| Context Compression Ratio       | 压缩带来的体积变化               |
| Context Assembly Latency        | 上下文装配增加多少延迟             |
| Context Contract Violation Rate | 必需信息、权限、Profile 等约束是否违反 |

这些指标本身不能证明任务效果提升。最终仍需与 SQL 正确率、任务成功率、业务结果等下游指标关联。

---

## 四、Context Snapshot 要不要单独建设？

**我倾向于不再建设一套重复的完整输入 Snapshot。**

如果 LLM Runtime 已经可靠保存了最终请求，并具备版本、哈希、受控访问和留存管理，那么 Context Runtime 只需保存：

- Candidate Manifest。
- Assembly Decision Manifest。
- Context Policy Version。
- `final_request_ref`，指向底层保存的最终请求。

只有当底层未保存完整请求、不能覆盖 Adapter 转换后的最终输入，或无法满足权限及回放要求时，才需要补充独立的 Context Snapshot。

这也符合 OTel 当前的设计方向：模型输入输出可按需采集，并可通过外部引用保存，具体存储及访问机制由应用负责。

---

## 五、落到 Compass：最小工程改造方案

建议不新增独立微服务，在现有 Context Runtime 增加三个逻辑接口：

```
class ContextObserver(Protocol):    def record_assembly(        self, decision: AssemblyDecision    ) -> None: ...class ContextLineageRecorder(Protocol):    def link(        self, source_ref, item_id, transformation    ) -> None: ...class ContextPolicyReplayer(Protocol):    def replay(        self, frozen_input, policy_version    ) -> AssemblyResult: ...
```

最小执行链：

```
Agent Runtime
    │
    ▼
Context Runtime
    ├── Context Providers
    ├── Selection / Compression
    ├── Context Assembler
    ├── Decision Trace ──────┐
    └── Lineage Manifest ────┤
                             ▼
LLM Runtime ──────────► Langfuse / OTel
    │                    统一 Trace
    ▼
Model API
```

在生产中，\`record_assembly\` 应通过异步或批量机制实现，避免大 Manifest 阻塞主链路；关键版本和必要证据仍需有明确的持久化保证。回放组件则优先作为离线测试工具，不必进入在线执行路径。

## 六、最终架构判断

我会把 Context Runtime Observability 定义为：

> **在复用 LLM/Agent Runtime 观测基础设施的前提下，对上下文的来源、选择、转换和装配决策提供可追溯、可解释、可评测与可回放的能力。**

它的增量价值不是再记录一次模型输入，而是把最终输入背后的**上下文决策过程**变成可验证的工程事实。

对于你的课程，我建议把重点放在三个问题上：

**第一，为什么模型没有看到本该看到的信息？** —— Context Decision Trace。

**第二，这条信息从哪里来，又在哪一步发生了变化？** —— Context Lineage。

**第三，修改上下文策略后，能否在不重跑整个 Agent 的情况下验证改进？** —— Context Policy Replay。

这三点既与底层 LLM Runtime 清晰分工，也足以构成 Context Runtime 可观测性与回放这一课的核心工程价值。

重新加载回复


- 怎么实现：Context Runtime 增加 OTel 埋点和决策事件，复用 Langfuse/LangSmith 展示 Trace，通过 PostgreSQL 和对象存储保存必要证据。
核心是：不重复建设 LLM Runtime 的观测能力，只补齐上下文决策的可解释、可追溯与可回放能力。
