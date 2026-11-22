# Web 主数据库

Owner 为 `apps/web`，key 为 `web/main`；PostgreSQL 17，业务 schema `web`，版本 schema `web_meta`。工程基座、语义内容、授权与发布表及迁移元数据表；历史 SQL 在共享后不可修改。

迁移唯一源为 [migrations](migrations/)。迁移 `0007_semantic_cleanup` 核对旧发布权限后收缩目录结构，保留资产、范围授权、执行绑定和发布清单。`0008_semantic_asset_grants` 增加工作区内的资产授权。`0009_document_vectors` 保存已发布的章节向量，运行账号只读，供应账号在 public 安装 vector 扩展。[0008 → 0009 增量](sql/upgrades/0008_semantic_asset_grants__0009_document_vectors.sql)。先备份并停用旧应用；存在尚未撤销且显式禁用能力的旧发布时，先在旧版本复核替代授权、撤销相关发布后重试。完整与增量 SQL 由受管工具生成。[交付清单](sql/manifest.json) · [0006 → 0007 增量](sql/upgrades/0006_semantic_search__0007_semantic_cleanup.sql) · [0007 → 0008 增量](sql/upgrades/0007_semantic_cleanup__0008_semantic_asset_grants.sql)

运行连接使用 `WEB_DATABASE_URL`；迁移/受管账号工具使用 `WEB_MIGRATION_DATABASE_URL`，仅本地供应使用 `WEB_PROVISION_DATABASE_URL`。owner 为 NOLOGIN 的 `compass_web_owner`，迁移登录角色为 `compass_web_migrator`，应用为 `compass_web_runtime`。运行进程不持有管理连接。

从 Compass 根执行：

```bash
pnpm db:provision -F web
pnpm db:migrate -F web
pnpm db:sql -F web
pnpm db:sql:check -F web
pnpm db:apply-sql -F web -- --from empty --to 0010_knowledge_library
```

供应仅操作明确登记的本地目标，拒绝未知既有库/角色；不会操作 Agent 或零售库。迁移对目标持有独占 advisory lock，检查数据库标记、角色、源摘要与实际版本。已经达到目标且校验一致时返回 already_applied；拒绝未知起点和对象。应用启动不会迁移。

恢复使用 `pg_dump` 备份到受控位置，向新供应的同所有权数据库执行 `pg_restore`，核对数据、版本与运行权限，撤销恢复库全部会话后切换运行连接并重启。保留旧库供复核；不使用 down 或 DROP 代替数据恢复。实际验证见 [995 实施记录](../../../../mai-specs/995-web-management-foundation/IMPLEMENTATION.md)。

新增 slug 不修改主键、成员关系和现有权限。租户全局唯一、空间在租户内唯一；教学数据回填可读标识，其他旧记录分配短标识。发布顺序为备份、迁移、启动新 Web；旧版本仍可使用保留的 UUID 主键。版本链、迁移源摘要在每次执行前后检查。

## 零售语义教学包

执行 `pnpm db:seed -F web -- --fixture teaching` 建立课堂身份后，使用 `pnpm db:seed -F web -- --fixture retail-semantics` 导入候选资产。两端已有独立数据库时，直接对 Web 迁移库用 `psql -X -v ON_ERROR_STOP=1 -f apps/web/db/main/seeds/retail-v1.sql` 执行同一事务。SQL 所需的教学身份、发布边界及数据源映射见[默认语义资产设计](../../../../mai-specs/974-lesson-22-enterprise-semantic-governance/design/RETAIL_SEMANTIC_ASSETS.md)。

可维护内容在 [retail-v1](seeds/retail-v1/)，[单文件 SQL](seeds/retail-v1.sql) 由同一装配器导出。向新的文件路径执行 `pnpm db:seed -F web -- --fixture retail-semantics --output-sql /tmp/retail-v1-check.sql` 可复核交付字节。结构迁移与内容安装独立。

第 22 课管理页使用 v2 定义及 v3 术语修订。安装 v1 后，运行 `pnpm db:seed -F web -- --fixture retail-semantics-v2`。升级在同一事务写入 121 个新版本和三个范围的稳定清单，保留原始版本、其他资产以及成员授权。重复执行同一内容返回 already_installed。实体字段和外键来自冻结零售版本 diagnostic-v3-20261019。执行绑定候选独立于定义发布。随后执行 `pnpm db:seed -F web -- --fixture retail-semantics-v3` 更新两家教学租户的「销售额」释义。已有人工修订或撤下的词条会跳过，既有灰度发布完成后再升级。

## 知识库管理

当前版本 0010_knowledge_library 创建三张工作区管理表，保留原有只读发布表。生产恢复时先在新目标供应 pgvector，再使用 pg_dump 的 --schema=web 与 --schema=web_meta 备份及恢复应用表、索引及版本账本。发布和回读同时需要保留知识文档绑定与 Run 固定记录目录。参见 [0009 → 0010 增量 SQL](sql/upgrades/0009_document_vectors__0010_knowledge_library.sql)。
