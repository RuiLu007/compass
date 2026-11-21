针对 Compass 电商 DataAgent，我建议把 Agentic Search 从单纯的“多轮自主搜索”升级为一套 分级上下文供给 + 自适应搜索 + 统一资源治理 + 证据验证 的架构。

核心不是让 Agent 每次都从零开始搜索，而是：

把稳定、常用、体量小的业务知识预置到上下文；把变化快、体量大、与当前问题强相关的信息按需检索；让 Agent 只为真正的证据缺口付出搜索成本。
一、上下文分成六级，而不是简单的静态与动态
Compass Context Hierarchy
(L0) 平台级	全局安全规则、工具使用原则、引用规则	默认注入
(L1) 租户级	租户身份、组织范围、默认指标口径、权限摘要	默认注入
(L2) 场景级	GMV 分析方法、业务诊断框架、常用指标与维度	按场景注入
(L3) 任务级	本次问题、时间范围、当前假设、搜索计划与预算	任务开始生成
(L4) 检索级	指标结果、SQL、知识片段、广告与库存数据	动态检索
(L5) 证据级	已验证事实、冲突、反证、引用和证据缺口	动态装配


这里的 L0–L5 是上下文供给层级，不是全部都属于 Prompt，也不代表固定的信任等级。

尤其要区分：
- L0–L2 主要提供稳定规则和业务理解。
- L3 提供本次任务的执行状态。
- L4 提供检索得到的原始信息。
- L5 提供经过验证、适合分析和生成报告的证据视图。
L5 不是比 L4 更权威的数据源，而是对 L4 进行组织和验证后的结果。
二、哪些内容应该提前预置？
我建议 Compass 采用“默认小上下文 + 场景上下文包”的设计。
1. L0：全局平台上下文
所有 Agent 都适用，但保持精简。
内容	是否默认注入	建议
Agent 身份与总体职责	是	极简
安全与数据访问原则	是	简要声明；实际权限由 Runtime 强制执行
工具使用原则	是	优先 Semantic Query，复杂查询才使用 SQL
Evidence 与引用规则	是	事实、推断、因果分级
搜索终止原则	是	证据足够、预算耗尽、边际收益不足
全部工具说明	否	仅暴露本场景可用工具
全部企业知识	否	必须按需获取


全局 Prompt 不应该塞进大量电商知识。
2. L1：租户级业务上下文
这是企业 DataAgent 与通用 Agent 的一个关键区别。

不同租户可能有不同的 GMV 定义、组织结构、商品分类和业务权限。

建议默认注入：
tenant_context:
  tenant_id: contoso
  business_domain: ecommerce
  organization_scope: 华东区域
  timezone: Asia/Shanghai
  currency: CNY

  default_metrics:
    - paid_gmv
    - order_count
    - refund_rate
    - conversion_rate

  semantic_profile: contoso_ecommerce_v3
  metric_catalog_ref: catalog_2026_11
  access_scope_ref: policy_scope_102

但不建议注入租户所有指标定义。

只放高频指标的名称、简要含义、默认口径和可检索的引用；复杂计算规则和完整指标血缘需要时再加载。

这里的 access_scope_ref 是上下文提示和引用，不能代替实际执行时的权限校验。
3. L2：场景级上下文包
这是我认为最值得建设的部分。

以“GMV 异常归因”为例，Agent 在开始搜索前，就应该知道：
- GMV 的常见拆解维度：区域、品类、门店、渠道、时间。
- 常见诊断指标：流量、转化、客单价、退款、库存、广告。
- GMV 的口径约束。
- 哪些指标可以做算术分解，哪些只能作为候选解释。
- 推荐的查询顺序。
- 什么条件下需要补查。
- 什么条件下不能做因果结论。
可以将其定义为一个 Scenario Context Pack：
scenario_id: gmv_diagnosis

core_metrics:
  - paid_gmv
  - order_count
  - average_order_value

diagnostic_dimensions:
  - category
  - store
  - channel

hypothesis_catalog:
  - traffic_decline
  - conversion_decline
  - stockout
  - refund_change
  - advertising_change

query_strategy:
  first: semantic_query
  fallback: readonly_sql
  knowledge: on_demand

evidence_requirements:
  - metric_scope_alignment
  - calculation_verification
  - source_traceability
  - causal_claim_requires_additional_validation

这不是要把诊断步骤写死成 Workflow，而是给 Autonomous Agent 一个业务领域的先验知识和默认策略。

Agent 仍然可以根据搜索结果自主调整路径。
4. 预置上下文的原则
一份信息满足以下条件时，才值得考虑预置：
- 高频使用；
- 内容较小；
- 变化不频繁；
- 适用于整个场景或当前租户；
- 对决策正确性影响大；
- 权限范围明确。
例如 GMV 的基本定义适合预置；完整 SQL、过去一年的全部经营报告、数十万条商品信息则不适合。

另外，预置内容必须有版本与失效机制。预置不是永久缓存：任务开始时应解析当前有效的 Context Pack，长任务还需处理重要规则变更。
三、哪些内容应该动态检索？
我建议采用三级动态检索，而不是把所有问题都交给向量搜索。
检索层	适用内容	推荐技术	触发方式
D1：精确获取	指标值、维度聚合、订单数据、广告 API	Semantic Query、受控 SQL、业务 API	首轮优先
D2：语义检索	指标详细定义、制度、活动规则、历史报告	Metadata Filter、BM25、Hybrid Retrieval、Rerank	需要解释或发现语义缺口时
D3：自主补查	跨来源验证、异常下钻、假设验证、证据冲突	Agentic Search 多轮规划	发现关键证据缺口时


以 GMV 下滑分析为例：

第一轮先查询 GMV 环比及品类、渠道、门店贡献。

如果发现下降集中在电子品类，第二轮查询电子品类的流量、订单量、客单价、广告支出和库存变化。

如果广告支出下降，但自然流量也下降，第三轮再检查活动、渠道归因、价格或其他可能解释。

如果证据已经足以回答问题，或者继续搜索的预期收益很低，就应停止。

因此，真正的 Agentic Search 并不是每个问题都必须执行多轮搜索。

简单事实问题走确定性查询；复杂诊断问题才启动自适应多轮搜索。
四、搜索预算应该如何设计？
建议采用 Run 级总预算 + Search 子预算 + 单次工具调用限制 三层结构。

不要让 Search SubAgent 自己拥有一套独立于 Agent Runtime 的资源体系。
1. 预算控制的五个维度
预算维度	主要控制对象	超限后的动作
Token	查询规划、结果摘要、验证模型调用	压缩上下文、减少探索
费用	LLM、数据库、外部 API	停止低价值补查
时间	整体搜索耗时、单工具超时	返回部分结果或降级
查询次数	总轮数、SQL 次数、API 调用次数	禁止继续无效探索
资源	SQL 扫描量、并发槽、结果行数	限流、拒绝或改写查询


下面是一套用于 Compass Demo 的初始实验参数，不是行业通用阈值：
search_budget:
  max_rounds: 5
  max_tool_calls: 20
  max_parallel_calls: 4
  max_wall_time_seconds: 90

  per_tool:
    semantic_query:
      timeout_seconds: 10
    readonly_sql:
      timeout_seconds: 20
      max_result_rows: 1000
    knowledge_search:
      timeout_seconds: 8
      top_k: 8
    business_api:
      timeout_seconds: 10

  stop_policy:
    stop_when_evidence_sufficient: true
    stop_on_repeated_query: true
    stop_when_no_material_gap: true

真实生产阈值应由工具延迟分布、数据库负载、租户配额和业务任务 SLA 决定。
2. 预算不应只看剩余 Token
假设已经查询到：
- GMV 下降 12%，已确认。
- 电子品类贡献 50%，已确认。
- 广告缩减可能是原因之一，尚未确认。
- 库存变化对部分门店存在影响线索。
接下来 Agent 可以考虑：

A. 继续搜索十份历史经营报告。

B. 查询广告渠道与订单转化的关联数据。

C. 查询电子品类相关门店的库存与销售时间序列。

通常 B、C 对当前关键证据缺口更有价值。

因此建议 Search Planner 对候选动作进行轻量评估：
\[
\text{Search Priority}
=
\frac{\text{预期证据价值}}{\text{预计时间、成本与资源消耗}}
\]
这是决策启发式，不需要在首期引入复杂的强化学习或精确概率模型。

可以先按高、中、低评估证据价值，优先执行能够验证关键假设、解决冲突、改变最终结论的查询。
3. 什么时候停止？
我建议明确五种终止原因：

SUFFICIENT：关键结论达到证据要求。

BUDGET_EXHAUSTED：时间、费用或调用次数耗尽。

NO_PROGRESS：连续查询没有获得新的有效信息。

BLOCKED：权限、数据源故障或必要数据不可用。

CANCELLED：用户或上层 Runtime 取消。

需要特别强调：搜索停止不等于问题已经解决。

如果因预算耗尽而结束，报告必须明确哪些事实已确认、哪些解释尚未验证。
五、可靠性、并发和多租户资源治理
这里不建议为 Agentic Search 再建一个完整的分布式调度系统。

Compass 已有 Agent Runtime、Tool Dispatcher、Worker 和运行状态体系，搜索层应尽量复用。
1. 职责分工
能力	负责组件	关键原则
搜索任务规划	Agent Runtime / Search Planner	选择下一步查什么
全局预算	Agent Runtime / Scheduler	统一控制 Run 成本
并发与排队	Scheduler / Worker	跨任务资源治理
工具超时与重试	Tool Dispatcher	根据错误类型处理
SQL 资源限制	数据访问层	只读、扫描量、超时、结果上限
API Rate Limit	Connector Adapter	按租户与下游接口限制
缓存与去重	数据访问层 / Search State	相同查询避免重复执行
证据保存	Evidence Store	原始快照与血缘
失败降级	Agent Runtime	调整计划或部分交付


2. 并发控制必须考虑两层
Run 内并发： 如果查询电子品类广告、库存和流量相互独立，可以并行执行。

跨 Run 并发： 100 个用户同时分析经营数据时，不能让所有任务无限并发查询 PostgreSQL。

推荐：
- 全局工具并发限制。
- 租户级并发配额。
- 数据源级并发限制。
- 高成本 SQL 独立资源池。
- 下游 API 令牌桶或配额控制。
- 公平排队，避免大租户长期占满资源。
一个容易被忽略的问题是：单个 Run 的 max_parallel_calls=4，并不能防止 100 个 Run 同时产生 400 个数据库请求。

因此，真正的资源上限必须由 Scheduler 和数据访问层共同执行。
3. 搜索失败后的处理
不同失败不能统一重试。
故障	推荐策略
API 429	尊重 Retry-After，退避重试
网络瞬断	有界重试，加入随机退避
SQL 超时	检查执行状态，优化范围或降级
SQL 权限拒绝	不重试绕过权限，返回阻断原因
指标不存在	重新解析指标或请求澄清
检索结果为空	改写查询、调整检索策略
数据源不可用	使用合规缓存或部分结果，并明确时效
查询结果冲突	进入证据核对，不直接选择一方


特别注意：查询超时后，不能简单认为底层 SQL 已停止。需要支持取消或检查查询执行状态，避免重试造成重复负载。
4. 查询去重和缓存
建议建立标准化 Query Fingerprint：
tenant_id
+ access_scope
+ tool_name
+ semantic_metric_version
+ normalized_query
+ time_window
+ data_snapshot
+ relevant_connector_version

相同 Fingerprint 可以用于查询去重或缓存候选匹配，但能否复用，还要检查权限、数据有效期和结果完整性。

尤其是跨租户缓存，不能因为查询 SQL 相同就共享结果。

对于正在执行的相同查询，可以采用 Singleflight，多个等待者共享一次底层执行；但每个等待者的取消、权限和预算仍要独立处理。
六、推荐的 Compass 最终架构
建议将整个 Agentic Search 组织为四个逻辑部分：

第一部分：Context Bootstrap

在 Run 开始时装配 L0–L3：
- 平台规则。
- 租户业务画像。
- 场景 Context Pack。
- 本次任务状态与初始预算。
第二部分：Adaptive Search

由主 Agent 驱动：
- 选择查询工具。
- 执行首轮确定性查询。
- 根据 Evidence Gap 决定是否补查。
- 管理假设和搜索进度。
第三部分：Evidence Management

负责：
- 规范化多源结果。
- 保存来源与快照。
- 验证口径和数据质量。
- 识别冲突。
- 关联 Claim 与 Evidence。
- 返回已验证事实及未解决问题。
第四部分：Answer Context & Report Validation

负责：
- 装配最终回答需要的上下文。
- 生成结构化结论。
- 校验事实与引用。
- 渲染角标。
- 输出可追溯经营报告。
物理部署上，前期可以把这些能力放在已有 Python Agent 服务和数据访问服务中，不必拆成四个微服务。
最重要的架构取舍
我不建议 Compass 当前阶段直接引入独立 Search SubAgent 作为所有查询的默认执行者。

原因是 Compass 已有 ReAct、Plan-and-Execute、Tool Dispatcher 和 Context Runtime。

首期只需要增加一个轻量的 SearchController，负责：
Search State
Search Budget
Evidence Gap
Next Search Action
Stop Decision

让主 Agent 保留任务目标与最终决策权。

只有当搜索任务本身已经足够复杂，例如同时处理大量独立资料、长时间跨系统调查、需要隔离搜索上下文时，再把 SearchController 演进为独立 Search SubAgent。

这样可以避免重复建设 Runtime、预算、调度、上下文和 Trace。
七、整体架构信息图

图中重点展示了上下文分层、预置与动态检索的边界、多源搜索闭环、预算分配，以及可靠性和并发治理。

有一个实施细节需要强调：预置上下文可以很轻，但权限控制、数据质量校验和资源配额不能只依赖预置 Prompt。 它们必须由可信执行组件强制保障。

对于 Compass，我建议按三个阶段实施：
1. M1：分级上下文 + 确定性查询。 先建设 Scenario Context Pack、Semantic Query 和基本 Evidence 校验。
2. M2：自适应多轮搜索。 增加 Evidence Gap、查询重规划、搜索预算、缓存与去重。
3. M3：企业级治理。 接入租户公平调度、数据源级限流、故障降级、证据回放和持续评测。
最终要实现的不是“Agent 能搜索更多信息”，而是用尽可能少、尽可能可靠的搜索，获得足以支持当前经营决策的证据。
