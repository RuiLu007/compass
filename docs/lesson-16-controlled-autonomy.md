# 第 16 课 · 受控自主执行

在分析 Chat 中选择第 16 课的预置问题后，可编辑问题、计划开关、最大轮次和时限，也可清除本次约束。发送时，问题和约束一起提交。约束随本次 Run 保存，暂停恢复继续使用提交时的约束和剩余执行预算。输入框上方的浮动条显示环形进度、当前步数和步骤名称，悬停或点击可查看完整清单。如需同时显示品名和 SPU 编码，可在问题中说明。

## 临时 SQL 查询

分析 Chat 可以直接问「当前有哪些可查的数据表」「查看 `core.spu` 的字段」或「执行 SQL：`SELECT spu_code, spu_name FROM core.spu ORDER BY spu_code LIMIT 10`」。也可以用自然语言描述想查的数据。Agent 可先查表结构，再生成 SQL，将实际结果以表格展示。

查询使用当前工作区获授权的 PostgreSQL 只读连接，schema 范围来自运行账号权限。database_query 可列出连接、表和字段，并读取字段注释；多连接通过 connectionId 选择，SQL 表名须带 schema。支持受限单条 SELECT、CTE、JOIN、聚合、窗口、排序和 LIMIT。行数、结果体积、超时和取消边界由工具约束，账号权限与 RLS 继续控制可读范围。

展开查询卡片可以查看入参和结果预览。分析期间显示当前进度，结束后显示用时，最终回答按阅读顺序逐步呈现。

## 本地配置

1. 准备分析工作区、支持工具调用的主模型和有表读取权限的 PostgreSQL 连接。渠道与商品题需要样本数据。课堂对照使用同一只读连接和同一问题。
2. Chat 默认选择 Auto，也可手动选择主模型。课程题目走普通意图路由。普通模式支持 Todo、`maxSteps` 和 `timeoutSeconds`。
3. 演示 Token 或费用计量时，为实际使用的 Flash 和主生成模型在 `COMPASS_AUTONOMY_MODEL_PROFILES` 中填写档案。核验后设置 `COMPASS_CONTROLLED_AUTONOMY_ENABLED=true` 并重启 Agent。

| 字段                                | 含义                                                                                     |
| ----------------------------------- | ---------------------------------------------------------------------------------------- |
| `provider_id`、`binding_version`    | 对应 Agent 模型配置中的 UUID 和绑定版本。更新模型绑定后重新核对档案。                    |
| `sdk_model`、`api_base`             | 模型的实际 SDK 标识和绑定端点。使用 SDK 默认端点时 `api_base` 为空字符串。               |
| `input_tokens`、`output_tokens`     | 经验证的输入计费上界、适配器支持的完整输出上界。分类请求使用 512，主生成请求使用 2,048。 |
| `input_microusd`、`output_microusd` | 最高适用单 Token 费率，单位微美元。必须覆盖失败尝试、推理与其他纳入档案的计费用量。      |

普通任务默认最多 100 个主模型轮次、10 分钟活跃执行时间和 400 次工具派发。工具返回结构化数据及执行状态，模型据此组织正文。启用 Sandbox 时，Python 工具可输出独立 PNG 产物。启用受控自主执行后，默认 Token 和费用上限分别为 10,000,000 Token 和 10,000,000 微美元。每次模型 Attempt 检查余额，未知回执按上界结算。档案缺失、连接覆写、版本或型号不匹配时，Run 在对应模型出站前停止。实际付费档案由部署方核验协议和费率后启用。

部署可通过 `COMPASS_AUTONOMY_TOKEN_BUDGET` 和 `COMPASS_AUTONOMY_COST_BUDGET_MICROUSD` 设置每个 Run 的额度。若模型档案按完整上下文上限预留，额度须覆盖一次最大占额及之前已结算的用量。预算仍按各次实际回执结算。两个变量及模型档案由 Agent 的开发入口和 Compose 读取。

## 课堂路径

| 预置问题 | 观察点                                                                                |
| -------- | ------------------------------------------------------------------------------------- |
| 先列计划 | 请求增加先登记 Todo 的模型指令。选择支持工具的模型，观察实际调用顺序和计划浮条。      |
| 最多两轮 | Run 最多进行两轮模型决策，到达上限后停止。                                            |
| 5 秒超时 | Run 累计执行时间到达 5 秒时停止并清理正在进行的请求。如果任务先执行完毕，将正常返回。 |

在仓库根运行 Agent 受控用例。

```bash
uv run --locked pytest apps/agent/tests/test_run_autonomy.py apps/agent/tests/test_tool_catalog.py apps/agent/tests/test_showcase_isolation.py
```

数据库集成验收需要 `AGENT_TEST_ADMIN_URL`、`AGENT_TEST_DATABASE_URL` 和 `AGENT_TEST_MIGRATION_DATABASE_URL` 指向专用 PostgreSQL。审批恢复和 Python 绘图用例另需 Redis 或 Sandbox。浏览器环境见 [Web 测试说明](../apps/web/README.md)。

```bash
uv run --locked pytest apps/agent/tests/test_generic_database_integration.py
pnpm test:e2e -F web -- analysis-composer.spec.ts
```
