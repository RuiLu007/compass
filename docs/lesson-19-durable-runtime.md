# 第 19 课：持久运行与人工确认

Compass 的分析页使用服务端 Session、Run 与 Message。首次提交分配稳定会话 URL；刷新、关闭标签页和 SSE 断线保留后台执行。再次打开会话先读取完整 Message，再从 Redis 原生游标订阅。待审批、暂停、执行异常和资源核对都保留在原 Run。

## 本地配置

分析首页「第 19 课 · 持久运行与人工确认」有两个入口。先用 `pnpm start` 启动本地服务，准备可用模型和已授权的 Retail PostgreSQL 连接。

1. **暂停、刷新与继续。** 发送后在执行过程中点「暂停」，确认已暂停再刷新原会话，检查已完成节点仍可见。点击「继续」，检查同一 Run 推进至完成。绘图成功后再次刷新，下载原图。此案例需要 Sandbox。
2. **人工确认后查一个数。** 在当前用户的 MCP 连接中启用能按日查询实付总额的工具，将该工具策略设为 `read_only / required`。发送预填问题，等待审批卡片出现。刷新原会话后由有权限的租户管理员点击批准，观察同一 Run 执行查询并返回金额。另起一次任务点拒绝，可检查查询未派发。

沿用 Web/Agent 数据库与第 16～18 课的模型、授权数据连接和 Sandbox 配置。在根 `.env` 设置独立的 `COMPASS_RUNTIME_STATE_KEY`，使用 `uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` 生成，保存到私有环境及备份密钥库。`COMPASS_MODEL_ENCRYPTION_KEY` 继续负责模型和连接凭据。

```bash
pnpm db:up
docker compose -f deploy/compose/compose.yaml up -d --wait redis
pnpm db:migrate -F agent
pnpm contracts
pnpm dev -F web -F agent
```

默认 Redis 为 `127.0.0.1:56379`，密码见 `.env.example`，教学配置启用 AOF、`appendfsync always`、`noeviction` 和持久卷。Agent `/readyz` 检查 0009 全部表、Runtime 密钥、Redis 持久化配置和 PostgreSQL 执行权。`COMPASS_WEB_AUTHZ_URL` 指向 Web 内部授权入口；Agent 与 Web 使用相同 `COMPASS_AGENT_SERVICE_TOKEN`，每次派发和控制都复查当前账号、工作区和权限。

可信工作流可通过 Journal 的外部事件等待端口保存事件类型、关联键与 JSON Schema；调用 `/v1/internal/analysis/runs/{runId}/events` 必须使用独立 `COMPASS_RUNTIME_EVENT_TOKEN`。该变量留空时入口关闭。事件只匹配已保存的等待点，重复 `eventId` 返回原回执，取消后的新事件返回冲突。

## 运行与恢复

- Agent 主库保存会话、步骤、逻辑和物理尝试、Checkpoint、Outbox、命令回执、工具绑定、产物、草稿、审批和动作。模型实际输入/输出加密保存在模型调用层；Checkpoint 保存私有续接状态。
- 关键状态先提交 PostgreSQL Outbox，再写 Redis 并完成整批投影；Message 与 SSE 使用同一 `AgentResponse v2`。原生 Stream ID 以整数对比较。活动流不设 TTL，终态完成投影后保留一小时；更久的历史从 Message 加载。
- 单 Agent owner 通过数据库 advisory lock 获取执行权。重启后已运行任务显示待恢复，用户显式继续；恢复校验 Checkpoint、绑定、已接受结果与产物摘要。已派发且结果未知的调用进入核对，禁止重复派发。
- QuickJS 从冻结脚本和参数重建宿主调用，已完成调用使用原结果和交付顺序。日期和随机序列固定，模型/工具费用及运行时间保留预算记录。
- CSV、PNG 与工作流保存邀请由主库保存；下载持续校验身份和归属。草稿与成功终态同事务提交，保存沿用工作流资产摘要去重。
- v1 Run 接口保留有限流和断线取消语义，内部适配到同一持久运行服务。

活动流丢失、日志损坏或 Checkpoint 校验失败时保留最近已确认视图，停止后继派发。Redis 临时拒绝写入时仍保存控制命令回执，恢复发布后才允许执行。恢复环境必须同时保留 Agent 数据库、Runtime 密钥、模型密钥、Redis AOF 和尚在保留期的 Sandbox/NAS 资源。

## 人工确认

15 分钟审批期限保存于数据库。当前租户管理员且拥有工作区 `models.invoke` 的用户可批准原提案；普通分析员可拒绝或取消自己的任务。重复决定返回原结果；版本、提案修订或摘要冲突要求重新加载。拒绝/过期直接生成只读结论，保留已有分析。

课堂预算动作使用 `actionId` 唯一效果记录。效果提交后进程退出，恢复只补记已有回执。普通 MCP 写调用失联时保存未知结局，并提供核对入口。取消先阻止后续派发；资源未确认释放时继续显示核对状态。

## 测试与交付

```bash
pnpm test -F agent
pnpm test -F web
pnpm test:integration -F agent
pnpm test:integration -F web
pnpm test:e2e -F web
pnpm check
pnpm build
```

集成测试显式配置专用 `AGENT_TEST_*`、`WEB_TEST_*` 数据库，禁止使用开发库。Agent 还需 Runtime/模型密钥、Redis、`COMPASS_WORKFLOW_E2E_RETAIL_DSN` 和第 18 课的 Sandbox 测试配置。Redis 故障测试创建带 `compass.test=lesson19` 标签的临时容器，验证 AOF 强杀恢复、内存写入拒绝及 owner 连接丢失，结束仅清理其自有容器和卷。

浏览器分别使用受控模型、Workflow 和 Tool 三种测试服务，业务请求经真实 Web、Agent 与 PostgreSQL；UI 状态覆盖使用 v2 协议夹具。测试命令、当前结果与未验项登记于 [实施记录](../mai-specs/985-lesson-19-durable-runtime-hitl/IMPLEMENTATION.md)及 [验收记录](../mai-specs/985-lesson-19-durable-runtime-hitl/test/ACCEPTANCE.md)。本课保持单进程 owner，云端隔离、多副本调度和 EvalOps 留在后续需求。

当前产品通过通用工具审批和执行记录恢复任务。旧预算调整与零售编排已退出生产，历史实现保存在 examples/showcases/retail/reference；既有记录与资产保留。切换前按 Agent README 执行活动 Run 检查。
