# Worker

独立 HTTP 消费进程，分发包 `compass-worker`、导入包 `compass_worker`。通过 Agent 内部接口领取一个执行槽、续租并逐 Checkpoint 推进；模型、工具、队列和数据库事务由 Agent 持有。Worker 不监听 HTTP，不读取数据库配置，不跨 app 导入源码。

```bash
pnpm dev -F worker
pnpm lint -F worker
pnpm typecheck -F worker
pnpm test -F worker
pnpm build -F worker
```

完整本地环境使用根目录 `pnpm start`，默认启动两个 Worker；`pnpm start --langfuse` 同时启用观测。Worker 的 Turbo 开发任务显式透传身份、令牌和租约配置。

根 `.env` 配置 `AGENT_API_URL`、`COMPASS_WORKER_ID`、`COMPASS_WORKER_TOKEN`。Agent 的 `COMPASS_WORKER_CREDENTIALS` 是 worker ID 到独立令牌的 JSON 映射；每次启动生成新的 bootId。第二个本地进程用 `COMPASS_WORKER_ID=worker-2 COMPASS_WORKER_TOKEN=<第二份令牌> pnpm dev -F worker` 启动。运行环境为 macOS / Linux，Windows 使用 WSL2。

课堂默认两个独立进程，每个进程一个槽。租约 TTL 15 秒，独立心跳间隔 3 秒，空队列轮询最长 1 秒；网络重试保留原 grant 和 expectedCheckpoint。续租未在本地保守截止前确认即停止推进。SIGINT / SIGTERM 后停止领取，有界等待当前边界并请求释放；Agent 确认资源释放后重新调度。

`pnpm run deploy worker` 构建并启动 Compose 的 `worker`、`worker-2` 两个服务。依赖已运行的 Agent；完整环境使用 `pnpm run deploy`。容器只注入 Agent URL、独立 Worker 令牌与租约配置，不注入数据库凭据。依赖未就绪时记录安全错误并重试；日志中的 `queue idle` 表示已完成一次成功领取请求。

配置、恢复边界与验收入口见 [第 20 课运行说明](../../docs/lesson-20-production-runtime.md)。业务数据所有权遵守 [数据库规范](../../docs/database-migrations.md)。
