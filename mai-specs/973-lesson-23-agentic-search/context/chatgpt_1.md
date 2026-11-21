Agent 搜到了数据，不代表拿到了事实；拿到了事实，不代表能够支撑结论；能够支撑结论，也不代表已经证明因果关系。

我建议不要把 Evidence Verification 理解成一个简单的“让 LLM 判断证据是否可信”的环节。

对于电商 DataAgent，更合理的架构是：

业务语义定义权威口径 → 数据访问层保证来源和查询正确 → Evidence 层验证事实与结论的关系 → Agent 负责分析与推断 → Report Verifier 强制校验最终引用。

这里有几个非常重要的工程经验。
一、先回答根本问题：查到的数据都是权威证据吗？
不是。企业内部数据库、指标平台、知识库，即使都属于企业自己的系统，也不能天然认为其结果正确、完整、适用于当前问题。

我建议把“权威”拆成四个维度：
维度	核心问题	谁来保障
来源可信	结果确实来自声称的系统吗？	Tool Dispatcher、数据适配器
口径正确	查询的指标、组织、时间和过滤条件正确吗？	Semantic Layer、确定性校验
数据有效	数据完整、未过期、未被截断吗？	数据质量与 Evidence Validator
结论成立	这些数据真的能支持 Agent 的这句话吗？	Claim Verifier


这四层应该分别判断。
一个很典型的例子
老板问：
华东区 10 月 GMV 为什么下降了 12%？

Agent 搜索到以下信息：
搜索来源	查询结果	可以直接认定什么
指标平台	GMV 从 1 亿下降到 8800 万	如果口径、范围和数据质量正确，可以确认 GMV 下降
数据库	电子品类 GMV 下降 600 万	可以确认该品类对总下降的算术贡献
广告 API	广告费用下降 20%	可以确认广告费用变化
库存数据库	部分商品缺货率上升	可以确认指定范围内的缺货现象
企业知识库	运营方案计划缩减投放	可以确认文档存在这个计划，不能确认计划实际执行
历史分析报告	去年类似下滑由广告导致	只能作为历史案例和候选假设


这六条材料可能全部真实，但它们的证明能力完全不同。

尤其是最后一条：历史报告可能帮助 Agent 形成搜索方向，但不能直接证明本次下滑的原因。

因此：

Evidence 不等于 Verified Fact，更不等于 Verified Conclusion。
二、电商 DataAgent 最重要的是建立“业务权威来源规则”
这里有一个非常实用的原则：

不要建立一个所有场景通用的来源可信度排行榜，而应该为不同类型的业务问题指定权威来源。

例如：
业务问题	首选权威来源	需要警惕的来源
GMV 到底是多少	经过治理的指标定义及其实际查询结果	历史报表截图、旧 SQL
GMV 怎么计算	当前业务期间适用的指标定义	已过期制度、个人解释
订单实际发生了什么	订单事实表或订单服务的权威记录	人工整理的汇总文档
广告实际花了多少钱	明确口径的广告账单或投放平台数据	广告计划文档
库存是否缺货	库存流水、可售库存及其有效快照	运营人员的主观描述
活动政策是什么	对目标期间有效的正式业务规则	旧活动复盘
为什么下降	多个可比较的事实、假设验证和必要的因果分析	单份报告、单个相关性


这里的“首选权威来源”不是说结果无需检查。

比如，指标平台虽然是 GMV 口径的权威来源，但如果 Agent 错选了组织范围、查询使用了过期快照，结果依然可能是错的。

所以我建议在 Compass 已有 Semantic Layer 上增加轻量的 Evidence Authority Policy，作为证据校验规则，而不是再造一个独立语义平台。

它可以定义：
metric: paid_gmv
authority:
  definition: semantic_layer
  primary_observation: metric_query
  reconciliation: readonly_sql

requirements:
  - metric_version_match
  - organization_scope_match
  - time_window_match
  - snapshot_valid
  - result_complete

historical_reports:
  role: hypothesis_reference
  can_override_metric_result: false

这个策略应该由可信系统配置和执行，而不是让 LLM 临时决定谁是权威。
三、真正的证据验证应该怎么做？
我建议把验证分成三个环节，而不是每次搜索完都让模型做一次完整的“证据审判”。

查询结果

Tool Result

事实校验

Evidence Validation

结论校验

Claim Verification

最终回答再经过 Citation Validation
第一层：查询结果进入 Evidence Store 前，做确定性校验
这是最应该工程化、最不应该完全依赖 LLM 的部分。

例如调用 query_metric 查询华东 GMV，返回：
{
  "metric": "paid_gmv",
  "metric_version": "v3",
  "region": "east_china",
  "period": "2026-10",
  "value": 88000000,
  "unit": "CNY",
  "snapshot": "2026-11-05",
  "query_id": "Q1001",
  "truncated": false
}

适配器需要检查：指标是否存在、版本是否有效、用户是否有权查询该区域、时间窗口是否正确、结果是否完整、快照是否符合要求。

通过之后可以标记：

validation_status = valid_observation

注意，valid_observation 只代表这是一条在指定来源和范围下通过检查的观察记录，不代表企业的真实世界一定没有错误。

Snowflake Cortex Agents 的官方文档明确指出，SQL 工具调用成功并不意味着返回结果完整，还需要检查 result_set_truncated，必要时通过 query_id 获取完整结果。这个细节非常值得 Compass 借鉴。
第二层：跨来源验证，解决口径冲突和证据缺口
比如：
- 指标平台：支付 GMV 8800 万。
- 财务报表：净 GMV 8300 万。
- 历史分析文件：GMV 8200 万。
如果 Agent 直接让 LLM 判断三个数字哪个正确，非常危险。

应该先做 Semantic Alignment：
1. 8800 万是否是支付 GMV？
2. 8300 万是否扣除了退款？
3. 8200 万是否只统计到 10 月 29 日？
4. 三个结果是否属于同一组织、币种、时间范围和数据快照？
这些问题核对后，可能根本不存在数据冲突，而是三个结果回答了不同的问题。

这也是为什么企业 DataAgent 中，Semantic Layer 不仅负责帮助 Agent 理解业务，还应该为 Evidence Validation 提供统一的比较坐标。

另外，跨源验证不等于每条数据都必须找第二个来源。一个受治理的指标结果经过完整性和口径校验，可能已经足够回答简单事实问题。只有出现异常、矛盾、高风险结论或证据缺口时，才值得增加查询。
第三层：验证 Claim 是否真的被 Evidence 支持
这是最适合采用 规则 + 确定性计算 + LLM 语义判断 的部分。

假设 Agent 准备输出：
电子品类 GMV 下降 600 万，主要由于广告预算减少 20%。

这句话需要拆成三个 Claim。
Claim	验证方式	结果
电子品类 GMV 下降 600 万	指标计算与复算	可验证事实
广告预算减少 20%	广告 API 数据核对	可验证事实
广告预算减少是主要原因	因果识别与替代解释检查	当前证据不足


这里 LLM 很适合做两件事：将自然语言拆成原子 Claim，以及判断文本证据是否在语义上支持某个 Claim。

但金额计算、指标口径、权限、时间对齐和引用 ID 存在性，应由程序检查。

Claim Verifier 的输出不是简单的 true/false，而是 supported / refuted / insufficient / contested，并附带依据和缺口。
四、最终回答到底应该给 LLM 什么上下文？
这是架构里最重要的实践点之一。

不要把所有 SQL Result、搜索 Chunk、历史报告原文全部拼进最终 Prompt。

这样不仅浪费 Token，而且容易造成来源混淆、证据重复计数和注意力污染。

我建议 Context Runtime 给最终回答模型装配四类内容。
1. Business Context：业务口径
例如：
business_context:
  tenant: contoso
  region: 华东
  metric: paid_gmv
  metric_version: v3
  period: 2026-10
  comparison: 2026-09
  timezone: Asia/Shanghai
  definition: 按支付时间统计，不直接扣除事后退款

它回答：这次分析到底在分析什么。
2. Verified Facts：已经验证的事实
verified_facts:
  - id: C1
    statement: GMV环比下降12%
    status: supported
    evidence_ids: [E1, E4, E9]

  - id: C2
    statement: 电子品类GMV下降600万元
    status: supported
    evidence_ids: [E5]

它回答：哪些话可以作为事实写进报告。
3. Hypotheses & Gaps：候选解释和未解决问题
hypotheses:
  - id: C3
    statement: 广告预算缩减是GMV下降的主要原因
    status: insufficient
    related_evidence: [E2, E6]
    missing:
      - 渠道与订单关联
      - 同期促销和价格变化
      - 可用的因果识别方案

它回答：哪些解释有线索，但不能写成已证明的原因。
4. Citation Manifest：允许使用的引用
citations:
  - ref: E1
    type: metric_result
    label: 华东GMV月度查询
    query_id: Q1001

  - ref: E4
    type: semantic_definition
    label: 支付GMV指标定义v3

  - ref: E6
    type: api_result
    label: 广告投放月度记录

这四部分是最小的 Answer Context。

对于复杂报告，再增加已确认的冲突、关键反证、计算方法、数据快照时间和必要的原文片段。

一个重要细节是：不能只给模型事实摘要，而把原始证据彻底隐藏。 对于需要引用具体原文的判断，应该同时提供精确片段；对于数值结论，应该提供可核对的计算值和对应结果定位器。
五、最终 Prompt 应该约束什么？
我建议区分两层。

System Prompt 负责约束回答行为；Runtime Validator 负责强制执行可检查的不变量。

可以采用这样的最小 Prompt：
:::writing{variant="standard" id="58321"}
你是企业经营分析 Agent，必须基于当前任务提供的业务口径、Claim 和 Evidence 生成回答。

**事实与推断规则**

1. 只有 `supported` 的事实性 Claim 可以写成已确认事实。
2. `insufficient` 的 Claim 只能表述为候选解释或未解决问题。
3. `contested` 的 Claim 必须说明冲突，不能擅自选择一方。
4. 不得将相关性、同期变化或历史案例描述为已经证明的因果关系。
5. 不得编造金额、比例、来源、查询结果或证据 ID。

**引用规则**

1. 每个关键数据结论必须引用其对应的 Claim ID。
2. 每个引用必须来自提供的 Citation Manifest。
3. 引用只能支持其实际覆盖的断言，不能扩大解释范围。
4. 不得引用已经失效、无权访问或未通过验证的证据。
5. 不得为了补齐引用而创造证据。

**输出要求**

区分已确认事实、候选解释、经营建议和未解决问题。

经营建议应说明依据、前提和预期需要验证的效果。

如果证据不足，应明确表达不确定性，而不是补全一个看似合理的答案。
:::但仅靠 Prompt 不够。即使模型遵循这些规则的概率很高，也不能把它当成可靠的引用安全边界。
六、回答中的角标应该怎么生成和校验？
这里我建议采用一个非常明确的工程原则：

模型负责选择引用关系，系统负责生成最终角标。

不要让模型直接生成最终的 [1]、`[2]`，更不要让它自己拼接数据库链接。
推荐链路
LLM 生成结构化回答

每个原子结论附 Claim ID / Citation Ref

Citation Validator

ID 存在 · 权限有效 · 版本一致 · 语义支持 · 数值正确

Citation Renderer

稳定引用映射 → 展示角标 → 证据详情
第一步：模型输出结构化结果
{
  "sentences": [
    {
      "text": "华东区10月GMV环比下降12%。",
      "claim_ids": ["C1"]
    },
    {
      "text": "广告预算下降可能是原因之一。",
      "claim_ids": ["C3"],
      "uncertainty": "hypothesis"
    }
  ]
}

实际实现可以使用结构化输出或严格 Schema。
第二步：程序执行引用校验
至少包含六项：
校验项	失败处理
Claim ID 是否真实存在	拒绝引用
Claim 是否属于当前 Run 和租户	拒绝引用
Claim 当前状态是否允许对应表述	降级或重新生成
Evidence Link 是否支持该断言	拒绝不匹配引用
数值是否与已验证结果一致	确定性复算，失败则阻止发布
引用来源是否仍可访问	拒绝展开或按策略处理报告


再加一项特别重要的检查：模型是否生成了没有 Claim ID 的新事实性断言。

否则模型可能在已经正确引用的两句话之间，插入一条没有证据的经营结论。
第三步：系统生成角标
例如最终展示：
华东区 10 月 GMV 环比下降 12%。(\(1\)) 电子品类下降 600 万元，占总降幅的 50%。(\(2\))

广告预算下降与电子品类销售下滑同期发生，但目前尚不能确认其因果贡献。(\(3\))

这些角标是演示样式，不是真实可点击引用。

实际系统中：
- [1] 关联 C1 → E1、E4、E9。
- [2] 关联品类拆分 Claim → 对应查询与计算结果。
- [3] 关联候选假设、相关观察和缺失证据，明确显示“尚未证实”。
用户点击角标后，应看到来源名称、查询或文档定位、数据时间、指标口径、验证状态和必要的原始结果。

Snowflake Cortex Agents 已经公开了工具调用 ID、SQL 查询 ID、结果表和文档 citation annotation 等关联字段，说明这类可定位引用具有真实产品实践基础。但角标能够定位来源，不等于来源已经充分支持整句话；后者需要 Compass 自己完成 Claim 级验证。
七、五条最值得落实的业务与工程经验
1. 事实校验优先确定性。 金额、比例、时间、口径、权限和数据完整性，不应该交给模型凭语言判断。

2. 知识库更多承担“解释口径、提供规则、提出假设”的角色。 历史报告可以引导搜索，但不能直接替代本期交易、广告和库存记录。

3. 引用必须绑定原子 Claim。 不要在整段分析最后堆五个角标，让读者猜每条证据支持了哪句话。

4. 搜索过程中的证据和最终回答上下文应该分离。 Evidence Store 保存完整来源，Context Runtime 只装配必要事实、反证、口径、缺口与引用映射。

5. 因果判断设置更高门槛。 业务上可以允许 Agent 给出“优先排查广告和库存”的建议，但不能把它包装成“已证明广告造成 600 万损失”。

这也是 Compass 当前阶段最合适的复杂度：不需要另建一个全能 Evidence Agent，而是把证据验证做成 Tool Adapter、Evidence Store、Claim Verifier 和 Report Validator 之间的清晰契约。
八、完整架构图：从多源搜索到可信回答

这张图强调三条工程边界：证据验证分层执行、最终上下文按需装配、回答角标由程序校验并生成。

需要注意，图中“通过”表示相应检查通过，不代表所有因果假设都已证实；未通过的 Claim 应触发补查、降级表述或明确标注不确定性。

最后，我建议把 Compass 的工程原则浓缩为一句话：

Agent 负责发现信息、形成假设和组织分析；可信系统负责确认事实边界、验证结论支撑关系，并确保每个引用都能回到真实的数据来源。
