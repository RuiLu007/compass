# Agent / main 数据库

Owner 为 `agent/main`，PostgreSQL 17。迁移唯一源为 [Alembic revisions](../../migrations/versions/)，受管工具为 [database.py](../../scripts/database.py)。当前版本 `0004`：保留 `0001` 的演示数据，0002 增加 `model_provider`、`model_change`、`model_call`，0003 将已有 `aliyun_decision` 绑定迁移为通用 `systemone_decision`，保持 ID、型号、凭据、配置版本和历史记录。0004 为调用表追加可空 `execution` JSON，记录至多两次尝试的安全摘要；旧行保持 null。Provider 凭据以 Fernet 密文保存；调用表仅保存元信息。

## 配置与执行

- `DATABASE_URL`：`compass_agent_runtime`，仅运行与 seed 使用。
- `AGENT_MIGRATION_DATABASE_URL`：`compass_agent_migrator`，通过 `SET ROLE compass_agent_owner` 迁移。
- `AGENT_PROVISION_DATABASE_URL`：本地供应管理员，只在 provision 使用。数据库限 `compass_agent` / `compass_agent_*`；既有 `compass` 演示库可显式接管。

从 Compass 根目录执行：

```bash
pnpm db:provision -F agent
pnpm db:migrate -F agent
pnpm db:seed -F agent
pnpm db:sql -F agent
pnpm db:sql:check -F agent
```

供应仅支持本机教学部署，拒绝未知角色与数据库对象；不会重设已有角色密码。新库 owner 为无登录角色，migrator 获得 owner 成员资格；runtime 无 DDL、删除审计或改写版本表权限。已有 `0001` 库须符合原表/约束，供应工具才会接管并登记迁移源摘要。接管前停止写入并备份；将两条 Agent URL 显式指向旧库，供应管理员指向同一实例。

原生迁移与 SQL 执行均核验数据库所有者、版本和迁移源 SHA-256，通过事务 advisory lock 串行化；失败回滚本次事务。版本摘要登记在 `alembic_version` 表注释，历史 revision 保持不可变。API 启动和契约生成不执行迁移。

## SQL 交付

[manifest.json](sql/manifest.json) 登记工具版本、源路径、SHA-256 和固定区间。清单 sources 相对本目录，files 相对 sql/。派生文件为 [bootstrap.sql](sql/bootstrap.sql)（empty → 0004）、[0001__0003.sql](sql/upgrades/0001__0003.sql)、[0002__0003.sql](sql/upgrades/0002__0003.sql)，保留历史 [0001__0002.sql](sql/upgrades/0001__0002.sql)。当前新增区间为 [0001__0004.sql](sql/upgrades/0001__0004.sql)、[0002__0004.sql](sql/upgrades/0002__0004.sql)、[0003__0004.sql](sql/upgrades/0003__0004.sql)。历史区间及 revision 保持原字节。禁止手改，执行使用受管入口：

```bash
pnpm db:apply-sql -F agent -- --from empty --to 0004
# 旧库使用 --from 0001 或 --from 0002；已达目标时安全返回 already_applied。
```

先完成供应与备份，停止模型写入，再迁移 Agent 到 0004、迁移 Web 到 `0003`，随后发布两端应用并配置服务令牌、加密密钥及端点允许列表。数据库各自独立事务；任一步失败保留实际版本并停止功能开放。

恢复方式为：停止模型写入，使用 PostgreSQL 17 `pg_dump` 保存原库，将备份恢复到新供应的 Agent 空库（`pg_restore --no-owner --role=compass_agent_owner`），配置原加密密钥，核验版本与受保护凭据可解密，再切换运行连接。保留旧库；本期不通过 downgrade 删除模型历史。生产备份还须采用部署方的受控存储与账号策略。

## 验证

`pnpm test:integration -F agent` 必须显式提供 `AGENT_TEST_DATABASE_URL`、`AGENT_TEST_MIGRATION_DATABASE_URL`、`AGENT_TEST_ADMIN_URL`，库名限定 `compass_agent_test_*`。运行与迁移使用上述独立角色；已有角色应使用其原凭据。测试创建并清理专用临时库，覆盖空库、旧库、SQL 一致性、权限、并发与恢复。`AGENT_TEST_PG_CONTAINER` 可指定同一实例的容器使用 PostgreSQL 17 备份工具，否则使用本机工具。不得对日常库运行测试。

## 0004 发布与应用回退

停止模型新请求、等待在途调用结束并备份库及加密密钥，执行受管 0004 迁移，再同步发布 Agent/Web 及生成契约。`readyz` 核对 0004。回退时可保留 execution 列，需使用已兼容 0004 的旧行为回退构建；原 0003 应用的 readyz 会拒绝新版本。无兼容构建时将已验证备份恢复至新供应的旧版库，再配套切换两端，保留故障库。本期不删列或改写历史 revision。
