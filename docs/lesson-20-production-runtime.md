# 第 20 课 · 多副本 Agent 与定时任务

Web 和 Chat 共用计划、触发及执行历史。Agent 各副本通过 PostgreSQL 队列领取 Run，经 Redis Streams 发布进度。单实例 Scheduler 扫描到期任务，通过内部 HTTP 调用与即时 Run 相同的受理服务。Scheduler 停机时，Agent 仍可接单、执行和续流。

## 启动与容量

在根目录按 `.env.example` 更新本地配置，保留原 Runtime 和模型密钥。设置 `COMPASS_SCHEDULER_DATABASE_URL` 和专用 `COMPASS_SCHEDULER_TOKEN`。数据库连接使用同库的 `compass_agent_scheduler`，令牌至少 16 字符，独立于 Web 与 Agent 间的服务令牌。

```bash
pnpm start
pnpm run deploy agent
pnpm deploy:status agent
pnpm deploy:logs agent
```

`pnpm start` 加载根环境，供应 Agent 账号并执行迁移，在 8102 上启动默认两个 Agent 进程，应用就绪后启动 Scheduler。Scheduler 独立监督。Docker 部署使用进程环境，默认在 `agent-gateway` 后启动两个 Agent 容器和一个 Scheduler 容器。已有独立 Web 环境通过 8102 调用。指定 `COMPASS_AGENT_REPLICAS` 调整本地副本数。只调试单个 Agent 可运行 `pnpm dev -F agent`，单独启动 Scheduler 可用下面的命令。

```bash
uv run --locked --env-file .env --package compass-agent python -m compass_agent.scheduler.main
uv run --locked --env-file .env --package compass-agent python -m compass_agent.scheduler.main --check
```

| 容量                   | 集群 | 每租户 |
| ---------------------- | ---: | -----: |
| 有效执行租约及回收占用 | 1000 |    200 |
| 已受理积压             | 3000 |     20 |

每个副本默认一个后台消费槽，由 `COMPASS_RUNTIME_CONSUMER_SLOTS` 控制。实际并行还取决于模型、工具与 Sandbox 配额。正在执行、等待确认和可继续的暂停计入积压。已阻塞并停放的 Run 退出积压，未确认资源的回收状态继续占执行槽。配置 `COMPASS_RUNTIME_TENANT_OVERRIDES` 时按可信租户 UUID 指定 `active` 或 `backlog`，积压覆盖最多 20 且只可收紧默认限制。

所有 Agent 副本共享同一策略指纹。首次启动写入指纹，失配副本的 `/readyz` 返回 503。计划变更集群策略时先停止所有 Agent 和 Scheduler，再由迁移所有者将 `agent_runtime_control` 中 `key='execution'` 的 `policy_sha256` 置空，按统一配置重启。Run 优先使用受理时的预算快照和既有消耗记录。单、双副本对比用相同的全局和租户配额及上游依赖。

## 定时任务操作

1. 使用具备「定时任务入口」和分析权限的工作区成员登录。在「定时任务 → 新建定时任务」输入分析问题、模型、Cron 和 IANA 时区。使用预览核对本地时间，再保存。
2. 在 Chat 中查询同名计划，把任务改到另一个时点并确认。普通任务所有者可以批准自己的计划变更。确认后由原 Run 提交计划及工具回执。回到 Web 可查看相同计划及递增的版本。
3. 在「执行记录」检查触发、交付重试和 Run 摘要。打开关联分析会话看 Timeline 和结果。启停和删除影响尚未开始交付的触发，已开始交付或已受理的任务可从原会话处理。删除后可通过「查看已删除任务」查原历史。

五段 Cron 以 IANA 墙钟计算，夏令时跳过不存在的本地时刻，重复时刻取较早 UTC。停机漏过多次只补最近一次。已有未结束交付或 Run 时跳过新时点。Scheduler 默认扫描批量 32，最多 4 个在途请求。单触发在 30 分钟内最多交付 8 次，按 Agent 的 Retry-After 及本地退避继续；失败前核对原受理回执。定时 Run 从保存的输入快照发起新 Session，提交前复核当前计划和分析权限。

Scheduler 日志记录到期发现、Agent 受理和终态回写的延迟，并定期记录待交付数量和最老等待。`schedule.delivery` 记录各次投递的状态和原因码。计划和触发的详细状态从执行记录和授权 Timeline 查询。

## 发布与恢复

迁移归属 `agent/main`，区间 `0014 → 0015`。部署前备份主库、Redis AOF 和 Runtime 密钥，停旧消费者与执行所有者并封存旧 PostgreSQL、Redis 写凭据及连接。运行供应和迁移，先验 Agent 即时受理、旧 Run 与跨副本 SSE，再启 Scheduler。[Agent 数据库交付](../apps/agent/db/main/README.md)

Agent 和 Scheduler 的租约独立。课堂每条 Run 的租约 15 秒，心跳 3 秒，当前持租者按不超过 1 秒的间隔观察共享控制意图。进程失联后由健康副本以有效回收令牌核对 Checkpoint、未确认效果与资源后接续，故障过程保留原 Run ID。Redis 附带包身份索引和发布世代；完整流支持跨副本续接。活跃流丢失或回退时从已确认 Message 视图重载并查看恢复状态。

回退先停 Scheduler、排空或回收执行并隔离新凭据。回到 0014 时将升级前的主库、Redis 和密钥恢复到隔离环境。封存 0015 数据，核对备份之后的受理和外部效果，再开放旧消费者。以 0015 重新上线时保留定时触发关系和未发布记录。

`pnpm check`、`pnpm build`、`pnpm db:sql:check -F agent` 和真实流程见 [本课验收](../mai-specs/984-lesson-20-production-agent-runtime/test/ACCEPTANCE.md)。部署选型及回退边界见 [ADR 0005](adr/0005-production-run-scheduler.md)。
