# LLM Runtime

本目录是 Agent 进程内的单次模型调用内核，设计见 [993 V2 技术方案](../../../../../mai-specs/993-lesson-11-llm-runtime/design/TECH_DESIGN.v2.md)。通过 `Runtime.invoke(ExecutionRequest, emit)` 调用；配置加载、凭据解密、授权和调用记录由相邻 [models](../models/README.md) 负责。

| 文件                    | 职责                                                                                      |
| ----------------------- | ----------------------------------------------------------------------------------------- |
| `contracts.py`          | 配置快照、调用上下文、执行输入/结果、Adapter 与 ContinuationCodec 协议；重导出 CallPolicy |
| `policy.py`             | 单调时钟绝对期限、单次尝试策略、输入与输出限制                                            |
| `runtime.py`            | 固定适配器选择、能力校验、期限、取消、资源收尾和安全结果                                  |
| `capabilities.py`       | 协议能力解析、输入/Schema 校验；Chat 已验证模型准入规则                                   |
| `catalog.py`            | 锁定版本 LiteLLM 离线目录与应用选择字段映射                                               |
| `litellm_chat.py`       | 生成协议、流式分片、工具组装、Thinking 私有状态和结构校验                                 |
| `systemone_decision.py` | 阿里云/JEV 共用 System One 请求、类型化结果及输入/输出用量解析                            |
| `transport.py`          | 单次物理 HTTP 出站、解压后大小限制、真实终止标记和资源登记                                |
| `continuation.py`       | 可见历史摘要与续接绑定验证，调用窄接口加解密                                              |
| `errors.py`             | 安全错误码、RuntimeFailure 与供应商错误归一                                               |

依赖方向为 `model_api → models.service → llm`。本目录不导入 `models`、FastAPI 或 SQLAlchemy；公开消息类型复用 `schemas.py`。目前只有 Agent 使用该内核，代码保留在此应用内。新增适配器须实现同一个 `ProviderAdapter.invoke`，并在应用装配入口显式注册。

每次调用创建独立 `CallSession`，进入执行时深拷贝嵌套 DTO，返回时复制结果。Runtime 接收应用已解密的 `SecretStr`，不接收数据库密文或 SecretStore。适配器仅获得 `ContinuationCodec.open/seal`，续接密文绑定用户、作用域、Provider、binding_version 和可见历史，兼容原有 Fernet 格式及 900 秒有效期。日志、repr、事件和元记录均不输出原始 Thinking。

Chat 的 180 秒预算从路由开始计算，诊断从应用准备开始计算，适配器网络等待和 delta 回压共用该绝对期限；Runtime 清理另有最多 2 秒。成功需要完整协议终止与内容校验；错误保留已知用量，未提供的用量为 null。取消抛出携带安全结果的 `RuntimeCancelled`，应用负责记录后继续传播。每次生成执行调用一次 LiteLLM SDK；认证和协议所需 HTTP 由 SDK 管理，重试和 fallback 均关闭。

从 Compass 根目录运行 `pnpm test -F agent -- tests/test_runtime.py` 验证独立内核；完整受控供应商协议回归运行 `pnpm test -F agent`。测试不要求真实供应商密钥。

## 目录演进

当前实现模块保持单层组织，职责按上表查找。目录随实际实现创建，不预建空包：

- `runtime.py` 负责单次执行；`executor.py` 协调逻辑调用，`admission.py` 管理共享容量；契约、策略和错误分别由 `contracts.py`、`policy.py`、`errors.py` 管理。
- 两个协议适配器继续使用 `litellm_chat.py` 与 `systemone_decision.py`。当适配器需要各自的辅助模块或新增独立协议实现时，再提取 `adapters/`，同步导入和测试。模型供应商配置仍归相邻 `models/` 管理。
- 路由选择、配额治理等能力随对应 Feature 确定归属和边界；当前无需 `execution/`、`governance/`、`providers/`、`routing/` 空目录。
- 上层 Agent 的多步工作流、调度与恢复在实现时组织。`compass_agent/runtime/` 属于这类运行编排的规划位置，和本目录 `runtime.py` 的单次模型调用职责分开；当前无需预建其 `workflow/`、`scheduling/`、`recovery/` 子目录。

`sdk.py` 提供锁定 SDK 的加载与模型标识解析。目录查询只读取 SDK 本地元数据与 Provider 注册表，不触发供应商登录。`catalog.py` 不限制供应商或型号；`chat_supported` 按生成输入类型准入。`capabilities.py` 使用 SDK 参数/能力元数据，具体参数差异交 SDK 原生转换处理。请求私有的 `ProviderAccess.connection_params` 只在调用期间解密，不写日志或公开事件。

原生工具 ID 和供应商签名保留在加密续接内；浏览器使用公开的不透明工具 ID，续接时恢复 SDK 原始消息与 tool result ID。DeepSeek 的已有 SDK 版本兼容守卫保留；其他供应商使用 SDK 自有协议解析和连接池。

## 有界逻辑执行

`executor.py` 在单次 Runtime 外统一重试、排队、退避、Deadline 及安全摘要；`admission.py` 提供 lifespan 共享 Gate。主生成显式使用两次预算，分类/诊断/决策为一次。已展示片段、已发送超时、未知结果、Schema 错误均停止；清理失败隔离容量。普通调用使用 COMPASS_MODEL_MAX_CONCURRENCY=8、COMPASS_MODEL_MAX_INFLIGHT=32，按进程计数。
