# 本地部署与应用镜像

第 14 课的独立 `observability` profile 在同一 Compose 网络部署本机 Langfuse，入口为 `pnpm observability`，UI 使用 loopback 8104。主 Agent / Web 可选加载忽略 Git 的观测配置，停止观测栈保留卷且不停止业务应用。镜像、初始化、数据格式、多实例接入与恢复见 [Langfuse 运行说明](observability/README.md)。

`compose/compose.yaml` 提供带 pgvector 的 PostgreSQL 17 与持久 Redis，镜像以 digest 固定；当前不启用向量扩展或创建向量索引。`app` profile 加入 Agent 迁移与 seed、Web 独立数据库供应/迁移、FastAPI 与 Next.js standalone。`web` profile 只启动 PostgreSQL、Web 迁移和 Web，无 Python 服务依赖。所有宿主机端口绑定 127.0.0.1；演示账号和合成数据仅供本地。

根目录使用以下统一入口，镜像构建上下文始终为 Compass 根目录：

```bash
pnpm run deploy          # 全部：home / web / agent / memory / worker
pnpm run deploy web      # 仅 Web；所需数据库与迁移自动执行
pnpm run deploy home     # 仅官网，无 Python / 数据库依赖
pnpm run deploy agent    # 仅 Agent，包含所属迁移和三天 seed
pnpm run deploy memory
pnpm run deploy worker
pnpm deploy:status       # 可追加 app 名称
pnpm deploy:logs web     # 最近 100 行日志，不持续占用终端
pnpm deploy:down         # 整个项目停止，保留数据卷和镜像
```

`pnpm deploy` 是 pnpm 的包分发命令，项目部署必须使用 `pnpm run deploy`。单应用部署只停止并重建指定应用，其余应用保持现状；数据库按需启动。只需数据库时仍使用 `pnpm db:up`。

## 部署顺序与失败处理

1. 检查参数和本机 Docker socket，将后续 Docker 调用固定到该地址；固定 Compose 文件和 `compass` 项目名。忽略 `COMPOSE_FILE`、`COMPOSE_PROFILES`、`COMPOSE_PROJECT_NAME` 等环境覆盖，禁止 TCP/SSH 远程端点。
2. 获取 `work/local-deploy.lock`，检查所需端口、已有数据库健康；不结束开发进程。运行中的同项目同服务端口允许复用。
3. Web 执行 `pnpm contracts:check`；有漂移时先运行 `pnpm contracts`。home 从规范源准备素材。构建所选应用及迁移镜像，复用 Docker 构建缓存；构建失败时旧应用继续运行。
4. 等待 PostgreSQL 健康（仅 Web / Agent 需要），停止所选应用。每次显式运行所属迁移容器：Agent 执行 Alembic 与幂等 seed；Web 执行已有供应和迁移，不自动写教学账号。
5. 重建所选应用并等待健康检查（最多 120 秒），执行固定本机地址的 smoke；Worker 额外检查两个消费容器处于 running；实际队列领取与推进由运行验收覆盖。

任一步失败即退出非零，不启动后续阶段，不自动清卷或回滚数据库；迁移失败后所选应用保持停止，修复原因后重跑。跨应用迁移不具备原子性，恢复遵循各所有者数据库规范。部署脚本只创建部署锁等临时文件，不记录凭据；异常终止遗留锁时先核对 `owner.json` 的 PID，再删除过期锁。

不主动读取根 `.env`，smoke 使用 `--local` 固定 127.0.0.1 地址；Compose 使用已登记的本地凭据。`COMPASS_NPM_REGISTRY` 可显式传入。Node / pnpm 与 Docker 必需；Web 的契约检查还需要 uv 和已安装 Python workspace。端口分别为 home 8101、Web 8100、Agent 8102、Memory 8103、PostgreSQL 55432。

CI 复用同一 `pnpm run deploy` 入口，之前执行 `pnpm check` 和 `pnpm build`。本机部署默认复用已完成的开发检查，部署仍强制执行契约检查、素材准备、容器健康检查与 smoke；完整发布前可手动先执行项目检查。

## 镜像与 Compose

- `containers/home.Dockerfile`：消费部署入口生成的 public 素材，使用 standalone 构建；复制 static/public，非 root 运行。直接调用 Compose 构建 home 前，先执行 `node apps/home/scripts/prepare-assets.mjs`。
- `containers/agent.Dockerfile`：uv 冻结安装当前 API 所需生产依赖；独立非 root 用户。
- `containers/web.Dockerfile`：BuildKit 缓存依赖下载，pnpm 冻结安装，构建 Next.js standalone，运行时只复制追踪产物与 static，非 root 用户。
- `containers/memory.Dockerfile` / `worker.Dockerfile`：分别安装当前 app 的 Python 生产依赖；Memory 提供健康检查，Worker 通过专用 HTTP 身份消费 Agent 持久队列。
- Python 迁移文件随 API 镜像交付；迁移任务先成功，API 再启动。生产部署需分离数据库迁移凭据。
- Web 镜像消费已生成且通过 CI 校验的契约；本地修改 DTO 后先运行 `pnpm contracts`。

停止所有服务：`pnpm deploy:down`。该入口保留数据卷，不接受 `--volumes`；数据重置需另行明确处理。

已提供独立 `memory` 与 `worker` profiles：`--profile app --profile memory --profile worker up --build -d --wait` 启动后端应用与 Web；`pnpm smoke --memory` 校验 Web → agent → 数据库及 memory 健康检查。memory 使用 8103 端口，可独立启动；worker 无 HTTP 端口，`worker` / `worker-2` 分别持有专用令牌，通过 Agent 领取和推进任务。启动 / 停止日志可通过 `logs worker worker-2` 查看。Worker 无业务数据库凭据，Agent 需要 PostgreSQL、Redis 及 Runtime 加密配置；Redis 由 Agent 的 depends_on 启动。云端隔离留待后续实施。

网络受限时，可在 Web 镜像构建时指定可访问的 npm 镜像，锁文件 integrity 校验仍然生效，默认使用官方 registry：

```bash
COMPASS_NPM_REGISTRY=https://registry.npmmirror.com/ docker compose -f deploy/compose/compose.yaml --profile app up --build -d --wait
```

## 独立零售业务库

Feature 998 的 [离线工具](../tools/data-seed/README.md) 复用同一 PostgreSQL 实例，通过 `data:seed` 创建带 release 摘要的独立候选库及专用装载/只读账号。配置模板见 [retail-local.example.json](retail-local.example.json)，操作与受限连接边界见 [业务数据说明](../docs/data-seeding.md)。模板不含凭据，`state_directory` 以 Compass 根目录为基准；工作目录与生成数据不提交 Git。应用库和原三天 seed 不参与零售版本切换。

同库保留旧数据的对照模式使用 [retail-schema.example.json](retail-schema.example.json) 与 `publish-schema`；新 schema 使用独立装载角色和状态目录，详见业务数据说明的“语义版本与旧数据对照”。

## Web 独立部署

```bash
pnpm run deploy web
docker compose -f deploy/compose/compose.yaml --profile web run --rm web-migrate node apps/web/scripts/db-manage.mjs create-user --login-name local.admin --display-name 本地管理员 --platform-admin true
```

`web-migrate` 使用 Web Dockerfile 的 `database-tools` 阶段，拥有独立供应/迁移凭据；运行容器仅得到 Web runtime 连接。迁移成功后 `/readyz` 才就绪；不会自动创建账号或写教学 fixture。账号命令交互输入隐藏密码；打开 <http://127.0.0.1:8100/login>。已有库保留，供应拒绝未知角色/数据库。跨服务旧演示只在 `app` profile 下可用，失败不影响 Web 管理。

完整 SQL 与备份恢复见 [Web 数据库说明](../apps/web/db/main/README.md)。首次发布顺序为供应角色/库、独立迁移、受管建账号、启动 Web；恢复时切换到已验证且已撤销旧会话的新库。此 Compose 为 loopback 教学环境；共享环境自行供应私有数据库凭据、HTTPS 来源和备份/审计保留策略。

## 模型 Runtime 配置

Agent 迁移容器先供应独立 `compass_agent` 库，再迁移至 `0014` 并写入幂等工程样本；运行容器仅持有 `compass_agent_runtime` 凭据。既有旧版 `compass` 演示库保留，接管步骤见 [agent/main](../apps/agent/db/main/README.md)。自定义已存在角色时可通过 `COMPASS_AGENT_RUNTIME_URL` / `COMPASS_AGENT_MIGRATOR_URL` 提供原凭据，供应不会覆盖密码。

`pnpm run deploy` 不读取根 `.env`。启用模型能力时从受控进程环境传入 `COMPASS_AGENT_SERVICE_TOKEN`、`COMPASS_MODEL_ENCRYPTION_KEY`、`COMPASS_MODEL_ALLOWED_ORIGINS`；前两项默认空，模型入口会明确不可用。模型 Key 经平台页面录入；数据库备份与加密密钥分别保存。完整接入见 [Agent 说明](../apps/agent/README.md#模型接入)。

## Agent Run 的 Retail 绑定

第 15 课的固定 Retail 工具连接独立的已发布零售库。要导入默认连接，向部署进程成对提供私有的 `COMPASS_RETAIL_RUN_SCOPE` 与 `COMPASS_RETAIL_RUN_READONLY_DSN`，并提供 `COMPASS_MODEL_ENCRYPTION_KEY`。Compose 将导入参数交给一次性迁移与 seed 服务。DSN 使用 Agent 容器能访问的地址，同一 Compose PostgreSQL 使用 `postgres:5432`。默认 Retail 的允许目标由 `COMPASS_RETAIL_ALLOWED_ENDPOINTS` 指定。通用 PostgreSQL/MySQL 连接可通过 `COMPASS_DATA_ALLOWED_ENDPOINTS` 放行额外的主机和端口。

导入完成后，在对应工作区的「数据连接」页测试和编辑。平台管理员保存的修改用于后续 Run，进行中的 Run 使用已读取的配置。课堂部署保持一个 Agent 副本，使取消路由命中原进程。配置与验证步骤见 [课堂复现](../docs/lesson-15-agent-run.md)。

第 18 课独立计算与 NAS 使用 `deploy/compose/sandbox.yaml`，入口 `pnpm sandbox up`，配置和执行边界见 [工具执行说明](../docs/lesson-18-tool-runtime.md)。

## 持久 Runtime 配置

Agent 部署需显式提供独立 Fernet `COMPASS_RUNTIME_STATE_KEY`，同时保留 Web/Agent 服务令牌、模型密钥。缺失 Runtime 密钥时 readyz 返回 503。部署入口继续只消费进程环境；可用 `node --env-file=.env scripts/deploy.mjs up` 加载私有根环境。Redis 配置见 `redis/redis.conf`，固定 AOF always/noeviction，卷 `redis-data` 在停止后保留，默认 loopback 56379。服务令牌用于当前权限复查，独立 `COMPASS_RUNTIME_EVENT_TOKEN` 用于可信工作流事件。Agent 容器为 SSE 设 5 秒 HTTP 关闭宽限，随后 lifespan 收口任务，超时的外部执行保持待核对状态。恢复与迁移顺序见 [第 19 课](../docs/lesson-19-durable-runtime.md)。

第 20 课队列、两个独立 Worker、0010 迁移与恢复入口见 [运行说明](../docs/lesson-20-production-runtime.md)。
