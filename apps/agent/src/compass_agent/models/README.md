# 模型应用服务

本目录管理 Agent 自有的模型接入配置和调用元记录，执行交给 [LLM Runtime](../llm/README.md)。迁移与 SQL 真源见 [agent/main](../../../db/main/README.md)，System One 增量升级 Agent 到 0003，保留已有表、配置及调用历史。

| 文件            | 职责                                                                |
| --------------- | ------------------------------------------------------------------- |
| `__init__.py`   | 只定义 PreparedCall：不可变配置/上下文/策略快照及应用私有密文       |
| `entities.py`   | Provider、配置变更、调用元记录的 ORM                                |
| `repository.py` | 配置幂等与乐观锁、调用占位、作用域查询、终态条件更新                |
| `service.py`    | 装配适配器、准备与解密、执行任务/断连监测所有权、结果映射和记录收尾 |
| `secrets.py`    | Provider 凭据加密、操作摘要、目的受限的 ModelContinuationCodec      |
| `validation.py` | 应用端点允许列表和 URL 安全检查                                     |
| `errors.py`     | HTTP 状态与固定公开错误文案                                         |

`prepare` 在开始时捕获 UTC 与单调时间，使用同一 180 秒预算完成配置读取、快照和 running 占位；确认事务提交后才允许执行。准备取消或超时时有界等待同一事务，已知提交记录执行相应收尾；未确认时不出站，遗留 running 到期后查询显示 incomplete/结果未知。

`run` 持有一个 Runtime 任务及可选断连监测任务。执行结束先收尾模型资源，再写元记录。`finish` 使用独立短事务及最多 2 秒等待，PostgreSQL statement_timeout 为 1.8 秒；同一 call_id 只更新 running，相同终态可幂等确认，冲突终态不覆盖。写入未确认返回 `record_write_failed`，通过原 call_id 查询，不重复调用供应商。

`RuntimeResult.upstream_sent` 仅供应用在写记录失败时计算结果未知，不加入 HTTP DTO。应用组装完整公开结果后检查 2 MiB envelope 上限，再持久化终态；HTTP 层负责 start/delta/唯一 final 的编码与队列。数据库仅存配置与安全元信息，不存对话、工具正文、续接密文或原始 Thinking。

从 Compass 根目录运行 `pnpm test -F agent`。真实 PostgreSQL 的配置、迁移与恢复验证使用 `pnpm test:integration -F agent`，独立测试数据库配置见 agent/main。浏览器联调使用 `tests/e2e_app.py` 的显式测试宿主，生产入口保持 `compass_agent.main:app`。

供应商连接参数与 API Key 一起保存在已有 `api_key_ciphertext` 的 Fernet envelope。旧 envelope 缺少 `connection_params` 时按空参数读取；保存按字段是否提交保留/替换，参数变更更新 binding_version。没有新增 DDL 或 SQL 版本。运行时快照包括整份加密 envelope，避免配置更新影响进行中的调用。

`ModelService` 接收 lifespan 的共享 Gate，由 ReliableExecutor 汇总调用；终态与 execution 在同一条件更新事务保存。`lifecycle.py` 登记 Chat、诊断及演示的 HTTP 根 runner，shutdown 沿原所有权链取消子执行。历史 execution=null 保持可读；失败响应清除工具续接 token。

`generation_profile` 随 0013 保存在 Provider 行。模型目录提供可选的输入和输出 token 上限候选。管理员可通过已有 Provider POST/PATCH 接口提交 `generation_profile`，包含 `limits`、`defaults` 和可选的 `import_catalog`；PATCH 传 null 可清空整个配置。省略单项上限沿用当前目录快照，显式 null 将该项设为未知。修改型号或端点时一并提交新配置或清除旧配置。可见模型接口返回已解析上限和默认输出 token，私有连接参数仍随凭据加密保存。
