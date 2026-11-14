# 第 20 课：异步队列与 Worker 驱动

部署使用一个 Agent 执行所有者、PostgreSQL、Redis 和两个独立 Worker 进程。Web 受理后返回 Session / Run，浏览器订阅公开状态；Worker 经专用 HTTP 接口领取执行权并推进至下一持久边界。Agent 继续拥有模型、工具、工作流与业务数据库。

## 启动与容量

从 `.env.example` 创建本地配置，保留现有 Runtime 加密密钥。执行 `pnpm db:provision -F agent`、`pnpm db:migrate -F agent` 后启动 Web / Agent / Worker。Compose 的完整 `pnpm run deploy` 同时启动 `worker` 与 `worker-2`；本地第二个 Worker 的命令见 [Worker README](../apps/worker/README.md)。两份 Worker 令牌分别绑定 worker ID，与 Web 服务令牌分离。

默认全局执行 2、积压 12，每租户执行 1、积压 4。`COMPASS_RUNTIME_TENANT_OVERRIDES` 接受按可信 tenant UUID 配置的 `{"active":1,"backlog":4}` 覆盖。已受理的暂停、等待和回收任务占积压位置，资源未确认释放的 reclaiming 占执行槽。拒绝新增任务返回 429 / Retry-After，不创建空 Run。Chat 保留问题以供手动重试。

## 持久化与恢复

迁移 owner 为 `agent/main`，版本 `0009 → 0010`。真源是 [0010](../apps/agent/migrations/versions/0010_run_scheduler.py)，使用 `pnpm db:sql -F agent` 生成完整/增量 SQL，`pnpm db:sql:check -F agent` 检查一致性。旧的已结束 Run 可直接读取，旧暂停/等待 Run 首次继续时在受理事务内补 job。

升级时先停两个 Worker，再停止旧 Agent，备份数据库和 Runtime 密钥；应用迁移、启动单一 Agent 完成恢复检查，再启动两个 Worker。恢复旧版本时停止新消费者与 Agent，恢复匹配版本的数据库备份和密钥，启动旧服务。迁移不提供删除持久记录的自动降级。

租约 15 秒，心跳 3 秒，监督与空轮询不超过 1 秒。过期先撤销 epoch、排空旧任务与回调，再核对 Checkpoint、当前权限、资源和动作效果。可恢复记录重新排队；未知远端效果或资源继续占槽等待核对，未知模型调用保留保守预算。已提交 advance 回执可按原执行权重读；过期执行权无法派发新步骤。

## 展示与诊断

公开消息与 SSE 沿用 AgentResponse v2。工具原始预览在进入 Outbox 前过滤，读取时按当前权限进一步隐藏细节；权限变化终止原订阅，客户端重新校验并清除旧视图。只有已完成 Run 与已完成助手消息共同构成最终回答，分析过程自动收起一次，用户仍可手动展开。

`GET /v1/analysis/runs/{id}/timeline` 仅供平台管理员诊断，关联调度、逻辑 Attempt、物理模型 Attempt 和内部推进命令。`GET /v1/analysis/runtime/usage` 仅供当前租户有效管理员读取；返回该租户配额、占用、已结算用量和未知调用预留，不返回提示词或模型密钥。Web BFF 对应 `/api/web/v1/analysis/...`，独立 purpose 授权。

## 验证入口

`pnpm check`、`pnpm build`；配置专用测试数据库和 Redis 后执行 `pnpm test:integration -F agent`、`pnpm test:integration -F web`。`pnpm test -F worker` 覆盖独立心跳、请求重试、租约截止和子进程信号退出。浏览器使用专用 Web 测试库及 `pnpm test:e2e -F web`。双租户容量、真实 Worker SIGKILL、恢复时间和浏览器结果记录在 [本课验收](../mai-specs/984-lesson-20-production-agent-runtime/test/ACCEPTANCE.md)。
