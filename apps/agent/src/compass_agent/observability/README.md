# 模型调用观测

Feature 990 在 Agent 内使用独立 OTel 1.45.2 Provider，显式持有父子 Observation；不修改全局 TracerProvider。`model_api` 在服务鉴权与动作授权后创建请求根，模型服务负责 Logical Call / 收尾，可靠执行器负责排队、Attempt 和退避。业务库结构保持 Agent 0004。

| 文件                 | 职责                                                                         |
| -------------------- | ---------------------------------------------------------------------------- |
| `observer.py`        | 句柄、W3C carrier、ParentBased 采样、终态与首片段时间、实例身份              |
| `privacy.py`         | 标量元信息白名单、依赖日志脱敏、安全 JSON 自诊断                             |
| `export.py`          | 出口检查、OTLP HTTP/protobuf、BSP 有界批处理与本实例计数                     |
| `litellm_receipt.py` | 请求级 LiteLLM 回调和本地估价；缺用量或缓存明细不完整时保持未知              |
| `summary.py`         | Langfuse Metrics v2 的两模型、三窗口固定聚合，14 个查询、并发 4、总预算 8 秒 |

父 span 不填写原生用量与费用；只有实际进入 Adapter 的 Attempt 标为 generation。input/output 单独记录，reasoning 只作为元信息，缺失键省略。估价缺失时不发送原生模型名和费用，避免后端按另一套模型价格补价。配置/准备阶段未得到模型快照的失败保留请求与 Provider 编号，不进入某一模型的对比统计。

业务 Prompt、SQL、Schema、工具参数、模型正文、思考正文、原始异常和凭据均不进入遥测。应用日志沿用原入口；本模块自诊断为 JSON，包含 `event`、`level`、`instance_id`、安全 `reason` 和可选的队列/丢弃/失败计数。

`Observation.carrier()` 只返回 W3C `traceparent` / `tracestate`。可信内部 HTTP/消息调用者显式发送，接收者通过已授权入口提取；不透传 baggage，浏览器头由 BFF 丢弃，Provider 请求不携带内部 carrier。多个实例连接同一项目，按 Trace ID 合并关系，按 instance_id 区分进程。状态 API 只报告接收请求的实例；对比 API 查询项目内跨实例样本。

普通单元验证：

```bash
uv run --locked pytest apps/agent/tests/test_observability.py
```

真实 Langfuse / PostgreSQL 验收需先配置专用 `AGENT_TEST_DATABASE_URL`（`compass_agent_test_*`）、对应迁移/管理员配置、模型加密密钥、服务令牌，以及本地观测配置，随后显式运行：

```bash
uv run --locked --env-file work/observability/acceptance.env pytest apps/agent/tests/observability_acceptance.py
```

该文件启动 8014/8015 两个受控 Agent 进程，向同一 Langfuse 导出，验证消息/HTTP 上下文、相反本地采样率、统计金标、真实 Chat 重试及 401/503/慢导出故障；外部模型 HTTP 使用测试夹具。普通 `pnpm test -F agent` 不需要 Langfuse。

部署、数据格式与存储见 [Langfuse 运行说明](../../../../../deploy/observability/README.md)。
