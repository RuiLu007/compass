对于 Compass，我建议把语义层做成一个**可运行的轻量业务语义服务**，而不是一个知识库，也不是完整的指标平台。

技术选型可以非常明确：

**PostgreSQL 存结构化语义资产 + JSONB 存扩展规则 + SQL/全文检索查候选 + LLM 辅助消歧 + 确定性执行绑定。**

首期不需要独立向量数据库、知识图谱、通用 SQL Compiler，也不需要独立微服务。

我建议学员最终实现的是：

- 一个 PostgreSQL Semantic Registry。
- 一个 Semantic Resolver。
- 一个 Metric Query Adapter。
- 一个 Semantic Governance Agent 能力。
- 一组供 DataAgent 调用的语义工具。

## 一、先看一个真实的使用画面

假设 Compass 服务两个品牌租户，每个租户都有销售、财务、营销部门。

用户问：

> 查询华东区上个月的销售额，并分析为什么下降。

在 Registry 中，“销售额”可能对应以下定义：

| 租户   | 部门 | 业务术语 | 实际指标      | 执行方式     |
| ---- | -- | ---- | --------- | -------- |
| 品牌 A | 销售 | 销售额  | 支付 GMV    | 参数化 SQL  |
| 品牌 A | 财务 | 销售额  | 确认收入      | 财务数据视图   |
| 品牌 A | 营销 | 销售额  | 广告归因成交额   | 营销指标 API |
| 品牌 B | 销售 | 销售额  | 扣退款后净 GMV | 参数化 SQL  |

假设用户身份是：

```
{
  "tenant_id": "brand_a",
  "department": "sales",
  "role": "analyst"
}
```

Resolver 不应该把所有叫“销售额”的指标都交给模型自由选择。

它应该先根据可信身份、组织作用域和可访问资产过滤，再检索候选，最后结合业务上下文选择支付 GMV。

如果用户的请求跨财务和销售部门，且没有明确口径，则返回澄清选项，而不是猜测。

这就是语义层首先要解决的事情：**让业务语言绑定到正确、可追溯、可执行的业务定义。**

---

# 二、Compass 语义层需要存储什么？

我建议分成四类资产，而不是设计几十种语义对象。

## 1. 业务术语与别名

解决“同名不同义”和“同义不同名”。

例如：

```
{
  "term": "销售额",
  "aliases": ["成交额", "营收"],
  "scope": {
    "tenant_id": "brand_a",
    "domain": "sales",
    "department": "sales"
  },
  "metric_id": "sales.paid_gmv"
}
```

这里要注意：别名不是全局唯一键，同一个别名允许在不同作用域下绑定不同指标。

## 2. 指标定义

这是语义层最重要的数据。

```
metric_id: sales.paid_gmv
version: 1.0.0
name: 支付GMV
description: 已成功支付的订单金额

scope:
  tenant_id: brand_a
  domain: sales
  department: sales

calculation:
  grain: order
  time_field: paid_at
  timezone: Asia/Shanghai
  aggregation: sum
  filters:
    - payment_status = SUCCESS
    - is_test_order = false

allowed_dimensions:
  - region
  - store
  - channel
  - product_category

execution:
  type: parameterized_sql
  ref: query.paid_gmv_v1

governance:
  status: published
  certification: certified
  owner: sales_analytics
```

这个 YAML 只是给学员看的配置示例。

实际系统中，核心字段应该结构化存储在 PostgreSQL，部分计算和时间规则可以放在 JSONB 中。

另外，正式系统应将稳定的 `metric_id` 与租户内定义版本区分开；不同租户的同名指标不应共享一个会被覆盖的数据库主键。

## 3. 数据实体与关联知识

例如：

```
entity: order
physical_table: mart.fact_order
grain: one_row_per_order
primary_key: order_id

relationships:
  - target: store
    join_key: store_id
    cardinality: many_to_one

time_dimensions:
  - paid_at
  - created_at
```

这里不需要一开始开发复杂的通用实体关系引擎。

但至少应该记录事实表粒度、主键、常用 JOIN 关系和时间字段。

这些信息可以供 NL2SQL 的 Schema Linking 使用，也可以用于 SQL 校验。

## 4. 可信执行资产

例如：

```
binding_id: query.paid_gmv_v1
type: parameterized_sql
metric_id: sales.paid_gmv

parameters:
  - region
  - start_date
  - end_date

sql_ref: sql/metrics/paid_gmv_v1.sql
```

实际 SQL 可以保存在 Git 管理的文件中，Registry 保存引用、版本、哈希、参数 Schema 和发布状态。

例如：

```
SELECT SUM(paid_amount) AS paid_gmv
FROM mart.fact_order
WHERE tenant_id = :tenant_id
  AND payment_status = 'SUCCESS'
  AND is_test_order = FALSE
  AND region = :region
  AND paid_at >= :start_date
  AND paid_at < :end_date;
```

这里的 `tenant_id` 必须来自可信执行上下文，而不是由模型填入。

SQL 也不能只靠文本检查来防止越权。数据访问层仍应通过数据库权限、RLS 或受控查询接口强制隔离。

---

# 三、具体用什么存储？我建议 PostgreSQL + JSONB

不需要一开始引入 Elasticsearch、Neo4j 或独立向量数据库。

## 最小数据库结构

**PostgreSQL · Semantic Registry**

结构化元数据 + JSONB + 版本 + 索引

semantic_assets

资产身份、类型、作用域、状态、负责人

semantic_versions

定义正文、时间规则、粒度、版本与哈希

semantic_aliases

术语、别名、语言、作用域、候选绑定

execution_bindings

SQL/API/View 引用、参数 Schema、校验规则

semantic_relations

引用、派生、实体关联、影响关系

semantic_reviews

AI 建议、校验结果、HITL 审核与发布

这六张表已经足够支撑课程第一版。

其中：

- `semantic_assets` 存逻辑资产身份。
- `semantic_versions` 存不可变的版本定义。
- `semantic_aliases` 支持同名不同义。
- `execution_bindings` 负责受控执行入口。
- `semantic_relations` 存引用和依赖，不必使用图数据库。
- `semantic_reviews` 管理候选修改与人工审核。

**注意：真正的租户、用户、角色和权限数据应该复用 Compass 已有 IAM/RBAC 系统，不要在语义库里重新维护一套用户权限体系。**

## 为什么用 JSONB？

因为语义定义中存在大量类型化的扩展字段。

例如：

```
{
  "grain": "order",
  "aggregation": "sum",
  "time": {
    "field": "paid_at",
    "timezone": "Asia/Shanghai"
  },
  "filters": [
    {
      "field": "payment_status",
      "op": "eq",
      "value": "SUCCESS"
    }
  ],
  "allowed_dimensions": [
    "region",
    "store",
    "channel"
  ]
}
```

这些内容用 JSONB 很合适。

但不能把所有字段都塞进 JSONB。

`tenant_id`、\`asset_id\`、\`version\`、\`status\`、\`owner\`、\`domain\` 等高频过滤和治理字段应该使用普通关系型列，并建立索引。

**原则：高频查询条件结构化，复杂规则 JSONB 化。**

---

# 四、语义检索应该怎么实现？

我建议采用分阶段的混合检索，但第一期不需要 Embedding。

## 第一阶段：作用域过滤 + 精确别名 + PostgreSQL 全文检索

当用户问“销售额”时：

**自然语言请求 + 可信身份上下文**

**1. Scope Filter**

Tenant / Domain / Department / Role / Published Version

**2. Candidate Retrieval**

精确别名 → 关键词 → 全文检索

**3. Semantic Resolver**

LLM 结合候选定义、业务问题和默认口径进行匹配

**4. Deterministic Validation**

合法指标 · 版本 · 维度 · 时间 · 执行参数 · 权限

SemanticQuery

唯一且合法

Clarification

冲突或信息不足

### 为什么首期不需要向量数据库？

因为 Compass 的首期指标规模有限，而且最关键的问题是：

> 同一个“销售额”，在不同部门对应哪个指标？

这种问题首先依赖作用域和精确别名匹配，而不是语义相似度。

Embedding 可以帮助找到“退款后实际成交金额”与“净 GMV”之间的语义关联，但它不能决定哪个部门的口径具有权威性。

因此我建议：

- **MVP：** SQL 精确匹配 + 关键词检索 + LLM 消歧。
- **后续：** 如果自然语言表达多样、别名覆盖不足，再加入 PostgreSQL `pgvector`。
- **更后期：** 只有语义资产规模、吞吐或检索需求明显增长时，再评估独立检索服务。

中文检索也需要注意：PostgreSQL 默认全文检索不能直接视为完整中文分词方案。首期应优先采用显式别名、规范化关键词和可控的模糊匹配；需要中文全文搜索时再选择合适的分词扩展或外部搜索服务。

---

# 五、应该向 Agent 暴露哪些工具？

这是课程工程实现最有画面感的部分。

我建议先提供 **4 个运行时工具 + 3 个治理工具**。

## 运行时工具

| Tool                    | 作用                  | 输入                                      | 输出                            |
| ----------------------- | ------------------- | --------------------------------------- | ----------------------------- |
| `search_semantics`      | 搜索业务术语、指标、实体和定义     | query、业务上下文                             | 候选语义资产                        |
| `resolve_semantics`     | 解析用户意图并处理歧义         | 用户问题、可信上下文                              | SemanticQuery 或 Clarification |
| `get_metric_definition` | 查看指标正式口径、维度、时间规则和来源 | metric_id、version                       | MetricDefinition              |
| `query_metric`          | 执行受控指标查询            | metric_id、dimensions、filters、time_range | QueryResult                   |

其中 `query_metric` 是受控执行入口。

Agent 不应该拿到 SQL 模板后自行修改正式指标的公式。

开放 SQL 则复用已有 `execute_sql` 工具，不需要在语义层重复开发。

## 治理工具

| Tool                           | 作用                        | 典型使用场景  |
| ------------------------------ | ------------------------- | ------- |
| `discover_semantic_candidates` | 从 Schema、SQL、BI 和文档提取候选语义 | 新业务域接入  |
| `review_semantic_change`       | 检查冲突、公式、JOIN、粒度、时间和依赖     | 指标新增或修改 |
| `submit_semantic_change`       | 提交草稿及证据，进入 HITL 审核        | 正式指标发布  |

治理 Agent 不应该直接拥有修改已发布指标的权限。

发布、回滚和版本切换应该由可信治理服务完成，并复用课程已有的 HITL Runtime。

---

# 六、语义治理 Agent 具体怎样工作？

假设营销部门提出：

> 我们希望把广告 ROI 的计算口径改成扣除退款后的收入除以广告花费。

治理 Agent 不应该直接更新 Registry。

正确流程是：

1. **发现候选变更**

   提取用户需求，查找现有广告 ROI 指标及其依赖关系。
2. **检测冲突**

   发现现有 ROI 使用归因成交额，新定义使用退款后收入，两者口径不同。
3. **自动校验**

   检查公式、退款归属、广告花费粒度、时间窗口、合法维度，并执行测试数据验证。
4. **生成治理建议**

   建议创建新版本或独立指标，而不是直接覆盖原定义，并附上影响分析和证据。
5. **HITL 人工确认**

   营销业务负责人确认口径，数据负责人审核执行逻辑。
6. **受控发布**

   发布不可变版本，更新作用域内的默认版本指针，记录审计事件。
7. **回归验证**

   使用 Golden Dataset 验证旧指标、新指标和依赖查询，监控发布后的质量。

对于课程首期，这个治理过程可以用一个简单状态机实现：

`DRAFT → VALIDATED → PENDING_APPROVAL → PUBLISHED → DEPRECATED`

校验失败或审批拒绝应有明确的失败、退回或拒绝状态；已发布版本不可原地修改。

---

# 七、具体工程目录可以长什么样？

```
compass/
├── agent/
│   └── data_agent.py
│
├── semantic/
│   ├── models.py
│   ├── registry.py
│   ├── resolver.py
│   ├── retrieval.py
│   ├── validator.py
│   ├── execution.py
│   │
│   ├── tools/
│   │   ├── search_semantics.py
│   │   ├── resolve_semantics.py
│   │   ├── get_metric.py
│   │   └── query_metric.py
│   │
│   └── governance/
│       ├── candidate_extractor.py
│       ├── reviewer.py
│       ├── approval.py
│       └── publisher.py
│
├── sql/
│   └── metrics/
│       ├── paid_gmv_v1.sql
│       └── net_gmv_v1.sql
│
├── migrations/
│   └── semantic_registry.sql
│
└── tests/
    └── semantic/
        ├── test_scope.py
        ├── test_conflicts.py
        ├── test_metric_query.py
        └── test_governance.py
```

这仍然是一个应用内模块，不需要拆成多个微服务。

真正重要的是内部边界清楚，而不是部署服务数量多。

---

# 八、我建议课程第一期的验收范围

不要一开始做 100 个指标。

我建议用一个很小但足够真实的业务数据集：

- **2 个租户：** 品牌 A、品牌 B。
- **3 个部门：** 销售、财务、营销。
- **5 个核心指标：** 支付 GMV、净 GMV、确认收入、广告 ROI、订单数。
- **3 条执行路径：** Metric Query、Trusted SQL、Open SQL。
- **2 类冲突：** 同名不同义、同义不同名。
- **1 条治理闭环：** AI 发现候选口径 → 自动校验 → HITL 审核 → 发布 → 回归。

这已经足够展示真正的企业级语义层。

学员最终应该能够演示：

同一个“销售额”问题，在不同租户和部门得到不同的正确指标；当用户意图不明确时，系统能够澄清；正式指标通过受控查询执行；当业务口径发生变化时，治理 Agent 能提出建议、经过人工审核并发布新版本。

**最终我建议将 Compass 语义层的职责严格限制为四件事：**

**管理业务定义、解析业务意图、绑定可信执行、治理语义资产。**

它不负责替代数据仓库，不负责实现通用指标计算引擎，也不负责独立维护一套 Agent Runtime、HITL 或 ContextOps。

这样既能保证课程有足够的工程深度，又不会因为建设完整语义平台而失去 DataAgent 的主线。

重新加载回复
