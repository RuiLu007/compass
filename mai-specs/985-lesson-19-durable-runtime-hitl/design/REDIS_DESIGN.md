# 第 19 课 · Redis 流与本地 Docker 部署

[返回主方案](TECH_DESIGN.md) · [数据结构](DATA_DESIGN.md) · [代码设计](CODE_DESIGN.md) · [PRD](../spec/PRD.md)

> 2026-11-12 · 用户已确定引入本地 Docker Redis。以下为待实施设计，尚未修改 Compose、安装客户端或启动容器。

## 1. 存储分工

本课采用 **PostgreSQL 持久业务数据 + Redis Streams 短期增量 + 数据库完整展示投影**。Redis 为运行必需依赖，使用 Compass 基础 Compose 中的独立实例；与 observability 等其他用途的 Redis 分离。单 Agent 实例负责写流、投影和读取，不引入独立投影服务、消费组、集群、Sentinel 或任务队列。

| 内容                                                          | 写入位置与时机                                 | 读取用途                                 |
| ------------------------------------------------------------- | ---------------------------------------------- | ---------------------------------------- |
| Session、原问题、运行配置、命令幂等                           | 受理事务写 PostgreSQL                          | 会话列表、首问恢复、重复请求             |
| 文本 delta、实时公开对象更新                                  | 合并后写 Redis；持久确认后对外输出             | Run SSE、断线续传、待落库投影            |
| 完整公开消息、Run 展示头、过程对象                            | 每 500 ms 或 64 KiB 合并投影；关键边界强制提交 | Message JSON、侧栏状态、完成后的全部历史 |
| Step/Attempt、上下文、预算、Checkpoint、Wait/Action、产物字节 | 按执行边界直接写 PostgreSQL                    | 执行恢复、审批、效果核对、下载           |
| 关键公开变化的待发布批次                                      | 与业务变化同事务写 agent_outbox                | SQL 提交后 Redis 发布失败的补发          |

事件指业务数据包；SSE 心跳、注释和 HTTP 网络分片不存储。高频文本不建立 SQL 事件行。agent_outbox 保留创建、步骤结果、控制、审批和终态等关键操作的发布载荷及回执。

## 2. 流键与游标

| 项           | 合同                                                                                         |
| ------------ | -------------------------------------------------------------------------------------------- |
| 键           | `compass:agent:run:<runId>:events`；每 Run 一条 Stream                                       |
| ID           | `XADD *` 产生原生 `毫秒-序号` 字符串，起点 `0-0`，仅在当前 Run 内排序                        |
| 必需字段     | `packetId`、`requestId`、`occurredAt`、`event`（校验后的 JSON）                              |
| 关键批次字段 | `outboxId`、`batchIndex`、`batchSize`；普通 delta 无这些字段                                 |
| 公开响应     | Redis ID 编码为 AgentPacket.seq；内部批次字段不进入前端协议                                  |
| SQL 投影位置 | `agent_run.projected_seq`；与消息和 public_view 在同一事务提交                               |
| 活跃流       | created/running/waiting/paused/cancelling 及终态投影未完成期间无 TTL，无 MAXLEN/XTRIM        |
| 终态流       | 投影追到流尾、Outbox 全部投递后，整键 EXPIRE 3600 秒                                         |
| 容量         | 遵守现有单帧/单 Run 预算，Redis 总量默认 maxmemory 256mb、noeviction；关键收口包计入预留预算 |

每个浏览器按自己的 `after` 使用 XREAD/XRANGE 读取，不使用分摊消息的消费组。Redis ID 的两段分别按整数比较；没有 `seq+1` 的连续性要求。稳定 packetId 识别发布重试；对象 revision 识别重复更新。浏览器游标在整帧应用成功后推进。

活跃流禁止局部裁剪，因此 `after` 必须是 `0-0` 或该 Run 的真实保留 ID；伪造、超前的游标返回 `cursor_invalid`。只有公开投影已终态且 Outbox 全部投递时，缺失流才按保留期结束返回 `history_reload_required`，前端重载 Message 即可。活跃流缺失、流尾落后于 SQL 投影或关键批次不完整时停止自动续接；普通未完成批次先由 Outbox 补齐。经核对确认数据丢失后标记 recovery_required，保留已存历史，避免循环重载和静默跳过内容。

同一 Run 的流只初始化一次。提交已受理但首批 Outbox 尚未投递、projected_seq 仍为 `0-0` 时可以初始化；首批投影完成后才允许模型/工具派发。此后缺失的活跃流进入恢复核对，禁止创建同名空流直接推进。本课无需额外的 Session 流序号、Redis INCR 计数或流代际字段。

## 3. 发布、投影与一致性

每 Run 使用一个进程内串行发布通道，前序 delta、关键 Outbox 和后续内容保持顺序。持有本课既有单实例执行权的 Agent 才能发布；所有者连接失效即停止发布和派发。多实例接管由后续课程扩展。

**高频增量：** 模型回调 → 50 ms/8 KiB 合并 → XADD → AOF 持久确认 → SSE。Projector 从 projected_seq 之后读取，按同一业务 reducer 形成完整消息和 public_view，每 500 ms 或累计 64 KiB 提交；阈值达到任一个即尝试刷新。投影失败施加背压并停止后续派发，保留 Redis 尾部。Redis 连接与写入无法确认时不放行该包。

**关键变化：** PostgreSQL 原子保存业务状态和 Outbox → 提交后 Publisher 写 Redis → Projector 强制提交完整关键批次及之前的增量 → 放行该批次 SSE。等待/暂停、审批变化、产物可用、消息完成和 Run 终态都走此路径。控制入口的受理回执可以先返回，前端业务视图仍由共享协议更新。HITL 效果查询与已返回结果的保存允许继续，新的外部派发等待依赖恢复。

Outbox 的所有包使用固定 ID、原时间和内容摘要；一次批次发布期间不交错其他写入。部分 XADD 成功后连接中断，先查该有界流中的包 ID/摘要，补齐缺项；不能把第一次失败直接认定为未写入。Redis 客户端对非幂等写命令关闭自动重试，读取可以有限重试。高频包也复用原 packetId 确认结果。无法核对时停止通道，原记录留待恢复。

Projector 按 Stream ID 顺序处理，只提交完整关键批次；最后一个 batchIndex 到达前可以先提交它之前的普通增量。整批公开消息、public_view、projected_seq 与 Outbox.delivered_at/last_stream_id 在同一 SQL 事务提交。Projector 重启从 SQL 游标继续；已处理 packetId/revision 不重复追加。SSE 对普通增量读取已获持久确认的前缀，对关键批次还需等待投影游标覆盖批次末尾。启动先核验 Redis 持久流和 SQL 游标，再以恢复后的流尾重建进程内确认位置，随后补发 Outbox、补投影并开放执行；无需逐包回写 SQL。

public_view.run 保存与该投影前缀一致的运行展示头。Message 的 Run 状态、消息和过程都从这一读取模型编码；业务控制表先变为 completed 时，尚未投影的完成状态不会提前出现在 Message 或侧栏。Run SSE 只在终态投影完成、关键 Outbox 全部投递且尾部送达后正常结束。下一轮提问同时检查前轮终态与投影完成。

## 4. 本地 Docker 配置

以下片段拟合并到 `deploy/compose/compose.yaml`，保留现有 postgres、migrate、agent 及其他服务。Redis 无 app profile，供本机开发与完整容器部署共用。

```yaml
services:
  redis:
    image: redis:8.2.11-alpine
    restart: unless-stopped
    command:
      - redis-server
      - /usr/local/etc/redis/redis.conf
      - --requirepass
      - ${COMPASS_REDIS_PASSWORD:-compass_redis_local_only}
    environment:
      REDISCLI_AUTH: ${COMPASS_REDIS_PASSWORD:-compass_redis_local_only}
    ports:
      - "127.0.0.1:56379:6379"
    volumes:
      - redis-data:/data
      - ../redis/redis.conf:/usr/local/etc/redis/redis.conf:ro
    mem_limit: 768m
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 3s
      retries: 30

volumes:
  redis-data:
```

拟新增 `deploy/redis/redis.conf`：

```conf
bind 0.0.0.0
protected-mode yes
dir /data
appendonly yes
appendfsync always
no-appendfsync-on-rewrite no
save ""
maxmemory 256mb
maxmemory-policy noeviction
```

本地采用固定版本标签；实施锁定镜像摘要并同步部署清单。命名卷保存 AOF 数据，Redis 内存上限之外保留容器缓冲及 AOF 重写余量。AOF always 的每批写入持久确认是本方案的发送门禁；维持 50 ms/8 KiB 合并控制刷盘频率。配置选择参照 [Redis 持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)和[官方镜像](https://hub.docker.com/_/redis)。运行中不得自行降为 everysec 后继续承诺相同的已展示内容恢复边界。

现有 `agent` 服务拟增补以下 environment / depends_on，保留原有迁移依赖与其他配置；Compose 对 `service_healthy` 的等待见[启动顺序说明](https://docs.docker.com/compose/how-tos/startup-order/)。

```yaml
services:
  agent:
    environment:
      COMPASS_REDIS_URL: redis://redis:6379/0
      COMPASS_REDIS_PASSWORD: ${COMPASS_REDIS_PASSWORD:-compass_redis_local_only}
    depends_on:
      redis:
        condition: service_healthy
```

根 `.env.example` 拟新增宿主机开发值：

```dotenv
COMPASS_REDIS_URL=redis://127.0.0.1:56379/0
COMPASS_REDIS_PASSWORD=compass_redis_local_only
```

URL 与口令分别传给客户端，避免 URL 编码和日志混入口令。示例口令仅用于本地开发；Redis 宿主端口绑定回环地址。Agent 容器显式使用服务名 redis，保持与宿主机 URL 分开。Web/BFF、浏览器和 Memory 不读取这两个变量。

Agent 使用 `redis` Python 包的 asyncio 客户端，安装版本随 `uv.lock` 固定。设置连接/读写超时、有限连接池，普通命令和阻塞 XREAD 使用分开的池，避免浏览器订阅耗尽写入/投影连接。仅新增 URL/口令配置，批次阈值、TTL 和容量先采用本文默认值，满足真实需要后再增加配置项。

## 5. 启动、停止与故障

实施后的本机启动顺序如下；当前 `pnpm db:up` 仅启动 postgres，保持该语义。本次新增依赖使用已有 Docker Compose 命令，无额外根命令别名。

```bash
pnpm db:up
docker compose -f deploy/compose/compose.yaml up -d --wait redis
docker compose -f deploy/compose/compose.yaml exec -T redis redis-cli ping
docker compose -f deploy/compose/compose.yaml exec -T redis redis-cli CONFIG GET appendfsync
pnpm dev -F agent
```

完整本机部署仍走 `pnpm run deploy`，Agent 的 Redis 健康依赖保证启动顺序。`pnpm db:down` 当前调用整个 Compose down，增加 Redis 后也会停止该实例，命名卷继续保留；部署说明须同步这个影响。临时仅停 Redis 可使用 `docker compose -f deploy/compose/compose.yaml stop redis`。不在常规清理中使用 `down -v` 或 FLUSHALL。

Agent `/healthz` 检查进程；持久执行 `/readyz` 校验数据库、Redis ping/AOF 配置和执行所有者。Redis 不可用时新 Run 提交/恢复返回可重试的依赖不可用错误，Message 仍可从 PostgreSQL 读取已提交历史，控制决定继续按数据库事务记录。依赖恢复后先补发和投影，再接受新的执行派发。

| 故障或操作                       | 约定结果                                                                                      |
| -------------------------------- | --------------------------------------------------------------------------------------------- |
| 浏览器断网、刷新、切换           | 原 Run 继续；从最后已应用 ID 补读，执行任务与连接独立                                         |
| Agent 在 XADD 前退出             | 未确认包不发送；恢复已持久业务边界，未知模型结局按原规则核对                                  |
| Agent 在 XADD 后、投影前退出     | Redis AOF 保留已确认包；重启从 projected_seq 补投影，再公开恢复状态                           |
| SQL 关键提交后 Redis 发布失败    | Outbox 保留原批次；不发布该成功状态、不派发下一步骤，恢复后补齐                               |
| 投影 commit 后回执/网络中断      | SQL 游标及投递回执原子保留，重复处理幂等，Message 可直接读取                                  |
| Redis 进程/容器重启，原卷完整    | AOF 恢复已确认记录；Agent 重连、补投影，已完成调用不重跑                                      |
| Redis 不可用、内存写满或磁盘错误 | 无成功写确认则不发送；停止新派发，已到达结果存 SQL/Outbox，依赖恢复后续处理                   |
| 终态流过期                       | Message 独立恢复完整结果，运行中最后视图与刷新视图相同                                        |
| 活跃流缺失、AOF 损坏或数据卷丢失 | 保留 SQL 最近投影与关键业务记录，标记恢复受阻；未投影尾部可能无法找回，禁止静默自动重建并续跑 |

本课保证正常进程/容器重启且卷完整时的恢复。单机 AOF 不覆盖磁盘永久丢失；上述损坏边界必须明确报告。若 Redis 服务仍故障，读取保留历史即可，Run 接口返回可重试错误；发现已确定的数据缺失时返回 `stream_data_lost`，客户端只重载一次 Message 并显示恢复受阻，避免重复刷新循环。

## 6. 实施与验收接缝

落地修改 Compose、redis.conf、根环境示例、Agent settings/依赖/连接池、lifespan、readyz、redis_stream/outbox_publisher/public_projection 以及部署文档。PostgreSQL 迁移仍为 agent/main 的 0008 → 0009，字段合同在 DATA_DESIGN 唯一维护；Redis 无独立 SQL 迁移。

验收归 PRD AC-10、AC-11、AC-17、AC-19：真实 Redis/SQL 下验证发布和投影各故障窗口、容器保卷重启、noeviction 写满、Outbox 部分发布恢复、终态过期后 Message 还原及双接口同 Schema。验证使用隔离测试 Run/Compose 项目，不清空共享 Redis 或删除现有卷。额外检查数据库行数随业务边界增长，长文本增量只更新消息投影，不产生等量 Outbox 行。
