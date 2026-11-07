<!-- diagram:architecture -->

```mermaid
flowchart TD
    B[Web / BFF] --> Q[RoutingService：父期限与阶段策略]
    Q --> S[ModelService]
    B --> L[管理员可靠性模拟]
    L --> E
    L -. ShowcaseReport / progress .-> B
    B -->|Provider 诊断| S
    S --> E[新增 ReliableExecutor]
    E --> G[共享 ExecutionGate]
    G --> R[已有 Runtime.invoke]
    R --> A[ProviderAdapter]
    A --> P[模型供应商]
    R -. 单次结果 .-> E
    E -. 逻辑结果与尝试摘要 .-> S
    S --> D[(ModelCall 元记录)]
    S -. 主生成及分类结果 .-> Q
    Q -. RoutedCallResponse / final .-> B
    S -. 诊断 CallResponse / final .-> B
    classDef added fill:#245847,color:#fff,stroke:#245847;
    class E,G added;
```

<!-- diagram:data -->

```mermaid
classDiagram
    direction LR
    class ModelCall {
        UUID call_id
        UUID provider_id
        str status
        datetime deadline_at
        ModelUsage usage
        ExecutionSummary execution
    }
    class ExecutionSummary {
        int version
        int max_attempts
        int queue_wait_ms
        str stop_reason
        AttemptSummary[] attempts
    }
    class AttemptSummary {
        UUID attempt_id
        int number
        UUID provider_id
        int config_version
        int binding_version
        datetime started_at
        int duration_ms
        str status
        str error_code
        ModelUsage usage
    }
    ModelCall "1" *-- "0..1" ExecutionSummary : execution JSON
    ExecutionSummary "1" *-- "0..2" AttemptSummary : 有序摘要
```

<!-- diagram:components -->

```mermaid
classDiagram
    direction LR
    class ModelService {
        prepare()
        run()
        finish()
    }
    class RoutingService {
        run()
        stage()
    }
    class ReliableExecutor {
        execute()
    }
    class ExecutionGate {
        int inflight
        int quarantined
        enter()
        leave()
        finish_attempt()
    }
    class Runtime {
        invoke()
    }
    class ProviderAdapter {
        invoke()
    }
    class ModelRepository {
        begin()
        finish()
        call()
    }
    RoutingService --> ModelService : 阶段策略与父期限
    ModelService --> ReliableExecutor : 执行
    ModelService --> ModelRepository : 元记录
    ReliableExecutor --> ExecutionGate : 共享容量
    ReliableExecutor --> Runtime : 每次尝试
    Runtime --> ProviderAdapter : 一次推理
```

<!-- diagram:sequence -->

```mermaid
sequenceDiagram
    participant S as ModelService
    participant E as Executor
    participant G as Gate
    participant R as Runtime / Adapter
    participant P as Provider
    S->>S: 准备快照、call_id、Deadline
    S->>E: execute(request)
    E->>G: 占用一个逻辑名额
    E->>G: 等待执行槽
    G-->>E: 获得执行槽
    E->>E: 检查取槽后的剩余期限
    E->>R: Attempt 1
    R->>P: 一次推理
    P-->>R: 无正文的 429 / 5xx
    R-->>E: 故障类别、发送与清理状态
    E->>G: 归还执行槽
    E->>E: 记录 Attempt 1，短暂退避
    E->>G: 重新等待执行槽
    E->>E: 再次检查取槽后期限
    E->>R: Attempt 2：同快照、同 Deadline
    R->>P: 一次推理
    P-->>R: 完整输出
    R-->>E: 完成结果、用量与清理状态
    E->>G: 归还执行槽与逻辑名额
    E-->>S: 一个结果 + 两条尝试摘要
    S->>S: 保存元记录、组装 final
```

<!-- diagram:states -->

```mermaid
stateDiagram-v2
    direction LR
    [*] --> running
    running --> running: 排队 / 尝试 / 退避
    running --> completed: 完整结果与应用收尾
    running --> failed: 结束且无可提交结果
    running --> incomplete: 输出中断或结果不完整
    running --> canceled: 停止当前调用
    completed --> [*]
    failed --> [*]
    incomplete --> [*]
    canceled --> [*]
```

<!-- diagram:retry -->

```mermaid
flowchart TD
    A[单次调用结果] --> B{调用成功且清理完成?}
    B -->|是| OK[汇总完整结果]
    B -->|否| C{恢复资格与执行预算满足?}
    C -->|否| Z[结束当前调用]
    C -->|是| W[退避 0.1–0.3 秒]
    W --> N[同 Provider / 同输入的新 Attempt]
```

<!-- diagram:capacity -->

```mermaid
flowchart TD
    A[ModelService A] --> N{逻辑名额 N = 32}
    B[ModelService B] --> N
    C[ModelService C] --> N
    N -->|满额| X[结束 / runtime_overloaded]
    N -->|有名额| Q[等待执行槽]
    Q --> K[共享执行槽 K = 8]
    K --> R[Attempt + 资源关闭]
    R -->|清理已完成| F[归还执行槽]
    R -->|关闭未确认| H[保留隔离占用]
    F --> D{是否继续尝试?}
    D -->|是| W[退避] --> Q
    D -->|否| O[归还逻辑名额]
    H --> O
```

<!-- diagram:stream -->

```mermaid
sequenceDiagram
    participant M as Agent：路由 / 诊断
    participant B as BFF
    participant U as 浏览器
    M-->>B: start / delta
    B-->>U: 预览分片
    U->>U: 累积 text / tools
    M->>M: 输出契约与元记录收尾
    M-->>B: 唯一 final：路由或诊断 envelope
    M-->>B: EOF
    B->>B: 接受完整流
    B-->>U: final + EOF
    U->>U: modelRequest 成功返回
    alt completed
        U->>U: 提交页面结果
        U->>U: 普通回答进入下一轮上下文
    else failed / incomplete / canceled
        U->>U: 保留预览与结束状态
    end
```
