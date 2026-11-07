## 目标

| 核心问题                                 | 实现目标                                                   | 量化指标（设计目标）                                                                                     |
| ---------------------------------------- | ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 临时故障打断模型调用                     | 主生成在同一次逻辑调用内有限重试，向用户交付一个最终结果。 | 主生成最多 **2 次尝试**；JEV/Flash 分类各 **1 次**，最坏整轮 **4 次推理**，最终回答 **1 份**。           |
| 排队、重试与高并发持续占用资源           | 统一总截止时间与进程并发上限，控制等待时长和资源占用。     | 执行预算 **180 秒**；单进程默认 **8 个执行槽**、**32 个逻辑调用名额**。                                  |
| 模型输出的格式、字段或工具参数不符合约定 | 按声明的输出契约校验，交付可供后续处理的有效结果。         | 可提交结果的契约校验覆盖率 **100%**；校验失败结果提交数 **0**。                                          |
| 流式中断留下不完整回答                   | 片段用于预览；完整成功后提交回答，并加入下一轮上下文。     | 成功结果提交 **1 次**；失败、取消或不完整内容进入下一轮上下文 **0 次**。                                 |
| 多次尝试的状态、耗时与用量分散           | 按每条模型调用汇总尝试、终态、耗时与 Token 用量。          | 主生成 **1 条元记录**、**0–2 条摘要**，分类各有派生 ID 和记录；**3 类用量**分别汇总，未知值保留 `null`。 |
| 课堂故障场景难以稳定复现                 | 管理员在同页运行真实执行链路的固定模拟场景                 | **11 个场景**；单进程最多 **1 场演示**，每场最多 **20 条调用 / 20 秒**；无真实厂商出站。                 |

## 1. 方案概述

### 1.1 设计分层

| 层         | 核心对象           | 负责的设计                               | 交接对象                             |
| ---------- | ------------------ | ---------------------------------------- | ------------------------------------ |
| 路由层     | `RoutingService`   | 分类、选模、父期限，显式传入各阶段策略   | 分类各一次 / 主生成两次              |
| 应用层     | `ModelService`     | 固定配置快照，准备调用，汇总响应与元记录 | `ExecutionRequest`                   |
| 逻辑调用层 | `ReliableExecutor` | 统一截止时间，串行尝试，退避与用量汇总   | `RuntimeResult` + `ExecutionSummary` |
| 并发控制层 | `ExecutionGate`    | 逻辑调用名额与执行槽，进程内共享         | 等待 / 获得槽 / 结束                 |
| 单次执行层 | `Runtime.invoke`   | 一次推理的生命周期、输出契约及资源关闭   | 单次 `RuntimeResult`                 |
| 协议层     | `ProviderAdapter`  | 生成与决策模型的输入、响应和流事件转换   | 规范化输出与用量                     |
| 展示层     | Web / BFF          | 展示 delta，接收 final，提交完整回答     | 页面结果与下一轮上下文               |

### 1.2 整体架构

{{diagram:architecture}}

| 设计位置           | 粒度                             | 状态的所有者                      |
| ------------------ | -------------------------------- | --------------------------------- |
| `RoutingService`   | 一轮 Chat：分类及选定主模型生成  | 父 call_id、父 Deadline、路由结果 |
| `ModelService`     | 一次模型逻辑调用                 | 配置快照、调用元记录、应用结果    |
| `ReliableExecutor` | 一个 `call_id` 下的 0–2 次尝试   | 次数、退避、累计排队时间与用量    |
| `Runtime.invoke`   | 一个 `attempt_id` 对应的一次执行 | 当前输出、发送情况、资源清理结果  |
| `ExecutionGate`    | 同一 Agent 进程内的全部调用      | 逻辑名额、执行槽、隔离槽          |

## 2. 详细设计

### 2.1 核心结构与请求流程

#### 2.1.1 数据模型

{{diagram:data}}

| 对象       | 关键字段                                              | 组织方式                            |
| ---------- | ----------------------------------------------------- | ----------------------------------- |
| 主生成调用 | `call_id`、`status`、`deadline_at`、`usage`           | 客户端 ID 对应主生成 `model_call`   |
| 分类调用   | `uuid5(parent_call_id, role)`                         | JEV/Flash 分类各有独立元记录        |
| Attempt    | `attempt_id`、`number`、`provider_id`、`status`       | 同一调用内按序执行，最多两条摘要    |
| 配置快照   | `config_version`、`binding_version`、模型输入         | 多次尝试使用同一份只读快照          |
| 执行摘要   | `version`、`queue_wait_ms`、`stop_reason`、`attempts` | `model_call.execution` 可空 JSON 列 |
| 输出正文   | `text`、`tool_calls`、`answers`                       | 运行时与响应内存中组织              |

#### 2.1.2 组件 UML

{{diagram:components}}

| 依赖              | 调用方向                                 | 输入 → 输出                  |
| ----------------- | ---------------------------------------- | ---------------------------- |
| 应用 → 执行器     | `execute(request, logical_policy, emit)` | 一次逻辑请求 → 汇总结果      |
| 执行器 → Gate     | `enter()` / `slots.acquire()`            | 名额与执行槽 → 本次执行许可  |
| 执行器 → Runtime  | `invoke(request, emit)`                  | 单次请求 → 单次结果          |
| Runtime → Adapter | `invoke(request, session)`               | 协议请求 → `ModelOutput`     |
| 应用 → Repository | `finish(response)`                       | 调用终态 + 尝试摘要 → 元记录 |

#### 2.1.3 一次逻辑调用的时序

{{diagram:sequence}}

#### 2.1.4 调用状态

{{diagram:states}}

| 状态         | 设计语义                         | 页面内容             |
| ------------ | -------------------------------- | -------------------- |
| `running`    | 准备、排队、尝试或退避进行中     | 预览内容             |
| `completed`  | 有效输出与应用收尾已完成         | 完整回答             |
| `failed`     | 调用结束，未获得可提交结果       | 错误状态及已有预览   |
| `incomplete` | 输出已产生，调用中断或结果不完整 | 保留片段，标明未完成 |
| `canceled`   | 用户或请求作用域结束调用         | 已停止               |

### 2.2 执行策略

#### 2.2.1 两层策略与同一个 Deadline

| 策略               | 作用范围                      | 关键字段 / 默认值                                        |
| ------------------ | ----------------------------- | -------------------------------------------------------- |
| `LogicalPolicy`    | 选定主模型生成                | `max_attempts = 2`，含首次                               |
| `LogicalPolicy`    | 单次执行的时间上限            | `attempt_timeout_s = 180`                                |
| `CallPolicy`       | 一个 Attempt                  | `max_attempts = 1`，`allow_fallback = False`             |
| 绝对截止时间       | Chat 从路由开始，覆盖全部阶段 | `deadline = routing_started + 180s`；诊断从 prepare 开始 |
| 退避               | 两次尝试之间                  | `uniform(0.1, 0.3)` 秒                                   |
| 分类 / 诊断 / 决策 | 每条模型调用                  | `max_attempts = 1`；JEV/Flash 各一次                     |
| 取槽后到期         | 构造 CallPolicy 之前          | 返回 model_timeout，归还槽，不新增 Attempt               |

```text
一轮 Chat ─────────────────────────────────────────────────→ 同一个父 Deadline
  ├─ JEV / Flash 分类 ── 各一次，阶段期限不超过父 Deadline
  └─ 主生成 call_id ────────────────────────────────────────→ 剩余期限
  │
  ├─ prepare  ── 固定配置、输入与开始时间
  ├─ queue    ── 等待共享执行槽
  ├─ attempt 1 ─ 释放已完成清理的执行槽
  ├─ backoff  ── 逻辑名额仍占用
  ├─ queue    ── 重新获得执行槽
  └─ attempt 2 ─ 汇总为一个最终结果

attempt_deadline = min(logical_deadline, attempt_started + attempt_timeout)
remaining        = logical_deadline - now
```

#### 2.2.2 重试决策

{{diagram:retry}}

| 当前结果                               | 内部分类                 | 下一步                         |
| -------------------------------------- | ------------------------ | ------------------------------ |
| 确认未发送的临时连接失败               | `not_sent_transient`     | 满足次数、期限和内容条件时重试 |
| 无正文的 429 / 5xx 终止响应            | `rejected_transient`     | 同一 Provider、同一输入重试    |
| 已发送后的未知结果或超时               | `never`                  | 结束当前逻辑调用               |
| 401 / 403、其他 4xx、Schema 或协议错误 | `never`                  | 返回当前错误                   |
| 已转发正文 / 工具分片                  | `content_exposed = True` | 保留当前片段并结束             |
| 次数用尽或截止时间已到                 | 预算结束                 | 汇总已有尝试                   |

#### 2.2.3 进程共享的并发控制

{{diagram:capacity}}

| 计数               | 默认值 | 何时占用                   | 何时释放                   |
| ------------------ | ------ | -------------------------- | -------------------------- |
| `max_inflight = N` | 32     | 逻辑调用进入执行器         | 执行器结束                 |
| `capacity = K`     | 8      | Attempt 获得执行槽         | 本次执行与资源关闭完成     |
| `quarantined`      | 0      | 已分派执行的资源关闭未确认 | 占用对应槽，当前进程内保留 |
| 排队 / 退避        | 计入 N | 调用尚未结束               | 转入执行或结束             |

```text
Agent 进程 / event loop
└── lifespan 创建一个 ExecutionGate(K=8, N=32)
    ├── ModelService A ──┐
    ├── ModelService B ──┼── 共享同一 Gate
    └── ModelService C ──┘

N：执行器内的逻辑调用 = 等待 + 退避 + 活跃执行
K：已占用的执行槽     = 当前尝试 + 资源关闭 + 隔离槽
```

### 2.3 输出、响应与变更文件

#### 2.3.1 输出契约

| 输出类型 | 输入声明                                     | 完整结果的处理                       |
| -------- | -------------------------------------------- | ------------------------------------ |
| Text     | `output_format = "text"`                     | 完整终止、非空正文                   |
| JSON     | `output_format = "json"`                     | JSON 解析后交付                      |
| Schema   | `output_format = "schema"` + `output_schema` | JSON 解析与本地 Schema 校验          |
| 工具建议 | `tools`                                      | 工具名称、参数 Schema 与调用 ID 校验 |
| 决策输出 | `DecisionInput`                              | Choice / Noul / Score 类型化结果     |

#### 2.3.2 流式预览与最终提交

{{diagram:stream}}

| 事件 / 操作                 | 浏览器动作                   | 上下文动作                           |
| --------------------------- | ---------------------------- | ------------------------------------ |
| `start`                     | 建立当前调用状态             | 保留已有成功历史                     |
| `text_delta` / `tool_delta` | 累积并展示预览               | 等待完整结果                         |
| `final`                     | 暂存本次终态与摘要           | 等待整个流读取完成                   |
| 成功 final + 完整 EOF       | 提交本次页面结果             | completed 的普通回答加入下一轮       |
| 失败 / 中断 / 取消          | 保留可见片段及对应状态       | 本轮内容保持在预览区                 |
| 手动重新提交原问题          | 新 call_id，重新生成独立回答 | 使用原成功历史与该问题，排除失败片段 |
| Chat 分类失败且 result=null | 保持路由失败状态             | 无主生成结果进入历史                 |
| continuation_token          | 完整成功工具建议的上下文绑定 | 不携带中断流的位置或恢复游标         |

#### 2.3.3 元记录与用量

| 返回字段                  | 含义                 | 聚合方式                        |
| ------------------------- | -------------------- | ------------------------------- |
| `execution.attempts`      | 每次执行的安全元信息 | 按尝试顺序追加，最多 2 项       |
| `execution.queue_wait_ms` | 等待执行槽的累计时长 | 每次等待耗时求和                |
| `execution.stop_reason`   | 执行器结束原因       | 完成 / 停止 / 次数耗尽 / 到期等 |
| `usage.input_tokens`      | 完整调用的输入用量   | 所有尝试均已知时求和，否则 null |
| `usage.output_tokens`     | 完整调用的输出用量   | 同上                            |
| `usage.reasoning_tokens`  | SDK 提供的推理用量   | 独立汇总，保持原字段语义        |

```json
{
  "call_id": "10000000-0000-4000-8000-000000000001",
  "status": "completed",
  "execution": {
    "version": 1,
    "max_attempts": 2,
    "queue_wait_ms": 18,
    "stop_reason": "completed",
    "attempts": [
      { "number": 1, "status": "failed", "error_code": "model_rate_limited" },
      { "number": 2, "status": "completed", "error_code": null }
    ]
  }
}
```

#### 2.3.4 变更文件与变更内容

```text
apps/agent/src/compass_agent/
├── llm/
│   ├── executor.py           [新增] Logical Call：尝试循环、退避、摘要、总用量
│   ├── admission.py          [新增] 共享 Gate：逻辑名额、执行槽、隔离计数
│   ├── policy.py             [调整] 增加 LogicalPolicy；CallPolicy 继续描述单次执行
│   ├── contracts.py          [调整] 结果增加清理状态与执行摘要
│   ├── errors.py             [调整] 错误码与 recovery 分类分开表达
│   ├── runtime.py            [调整] 单次调用返回明确的发送与资源清理结果
│   ├── transport.py          [调整] 记录发送事实及无正文的临时拒绝
│   ├── litellm_chat.py       [调整] 保留生成模型故障类别与输出契约
│   └── systemone_decision.py [调整] 返回统一的单次执行信息
├── models/
│   ├── service.py            [调整] 准备请求 → 执行器 → 响应与元记录
│   ├── repository.py         [调整] 终态与 execution 摘要一起保存
│   └── entities.py           [调整] ModelCall 增加可空 execution JSON
├── routing/
│   └── service.py            [调整] 分类/主生成显式策略，共享既有父期限
├── reliability/
│   ├── provider.py            [新增] 内置固定 HTTP 故障，不访问真实供应商
│   └── service.py             [新增] 场景目录，真实 Runtime / Executor 与隔离 Gate
├── models/lifecycle.py        [新增] 登记与收束 HTTP 根任务
├── schemas.py                [调整] 执行摘要与 Showcase 协议
├── model_api.py              [调整] 两类 envelope；登记路由/诊断根 runner
├── main.py                   [调整] lifespan 创建共享 Gate，关闭时收束根任务
└── settings.py               [调整] 并发容量与逻辑调用上限
```

```text
apps/web/src/
├── features/models/
│   ├── reliability-panel.tsx [新增] 同页场景、事件、容量、预览与摘要
│   ├── use-model-call.ts     [调整] 暂存两种 final，完整读取后提交；处理 result=null
│   ├── output.tsx            [调整] 展示尝试次数、摘要与未知用量
│   └── types.ts              [调整] 接入扩展后的调用结果
├── server/model-agent-client.ts
│                            [调整] 处理包含执行摘要的 final
├── contracts/model-errors.ts [调整] 过载与资源结束错误的用户文案
└── generated/                [生成] 从 Agent DTO 派生类型与校验 Schema

contracts/                    [生成] 跟随 Agent DTO 与路由源更新
```

#### 2.3.5 课堂可靠性模拟

| 设计点     | 运行规则                                                                                                  |
| ---------- | --------------------------------------------------------------------------------------------------------- |
| 管理入口   | 平台管理员“Agent 与模型”页；目录 GET reliability/scenarios，运行 POST reliability/runs                    |
| 固定场景   | 正常、429/503 恢复、次数耗尽、401、超时、半途断流、Schema 拦截、取消、并发过载、关闭失败隔离              |
| 执行路径   | MockTransport 模拟供应商 → 生产 SDK Adapter / Runtime / ReliableExecutor；每场隔离 Gate，不改普通模型状态 |
| 展示       | start/progress/final，实时预览、请求次序、尝试与用量、容量峰值及可提交结果                                |
| 输入与权限 | 场景枚举 + call_id；BFF 平台权限与 CSRF，Agent 可信上下文；无自由地址、密钥或脚本                         |
| 容量       | 每进程 1 场、最多 20 条调用、20 秒；并发场景 K=2/N=5，关闭失败只影响本场隔离槽                            |
| 结果       | final 与完整 EOF 后交付；取消/断连结束根任务及子执行；不写正式模型调用表                                  |

### 2.4 核心类型与代码

#### 2.4.1 类型契约

{{types}}

#### 2.4.2 共享 Gate

{{code:0}}

#### 2.4.3 逻辑调用算法

| 局部对象            | 方法 / 字段       | 代码职责                            |
| ------------------- | ----------------- | ----------------------------------- |
| `LogicalRun`        | `observe()`       | 追加一条尝试摘要                    |
| `LogicalRun`        | `queue_wait_ms`   | 累计等待执行槽的时间                |
| `LogicalRun`        | `finish()`        | 汇总最后结果、用量与停止原因        |
| `LogicalRun`        | `cancelled()`     | 形成取消结果并继续向调用者传播      |
| `content_exposed`   | `False → True`    | 第一个 text/tool 分片转发前记录状态 |
| `cleanup_confirmed` | 单次 Runtime 返回 | 决定执行槽归还或保留                |

{{code:1}}

#### 2.4.4 故障恢复分类

{{code:2}}

#### 2.4.5 分项用量汇总

{{code:3}}

#### 2.4.6 浏览器提交逻辑

{{code:4}}

#### 2.4.7 路由阶段策略

{{code:5}}
