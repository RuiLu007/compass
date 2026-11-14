# 第 15 课 Agent Run 课堂复现

本说明对应 [Feature 989](../mai-specs/989-lesson-15-agent-execution-kernel/BRIEF.md)。课堂入口仍是现有分析 Chat；一次发送创建一个 Agent Run，并在页面内展示模型路由、Step、通用查询调用、最终回答和可用产物。

## 前置条件

1. 按根 [README](../README.md)完成 Web、Agent 与两套应用数据库初始化，并创建具备 `module.analysis` 与 `models.invoke` 的课堂账号。
2. 在平台模型管理中启用一个支持 Tools 的生成模型。自动选模还需要现有路由所需的分类模型。
3. 使用支持 `compass-retail-1` 的历史发布工具、配套 DDL 与迁移准备独立 Retail PostgreSQL，或复用匹配的已发布库。当前 `compass-retail-3` 发布工具会拒绝本课的旧包。课堂基线为 release `retail-dev-20261022b`、内容摘要 `7ae26405a71589c98ca8379bec1e1ec1ff77e16232ff2e1bf26b6d4501b8183e`、schema `core`、只读 profile `a_east`（Retail tenant 101 / region 1）。不要使用应用数据库账号、迁移账号或扁平 `retail_course` 导入代替受限身份。
4. 运行 Agent 的 libpq 必须支持有界 `cancel_safe`；运行时预检不满足时不会开放数据库查询工具。

发布、活动清单和受限身份的生成方法见 [业务数据操作说明](data-seeding.md#权限失败与回切)。只读 DSN 属私密部署材料，不写入仓库、日志或浏览器配置。

## 初始化课堂数据连接

准备好 Retail 发布库和 Web 工作区后，在根 `.env`（宿主开发）或 Compose 初始化环境中成对设置：

```dotenv
COMPASS_RETAIL_RUN_SCOPE={"tenantId":"<Web tenant UUID>","workspaceId":"<Web workspace UUID>","release":"retail-dev-20261022b","contentHash":"7ae26405a71589c98ca8379bec1e1ec1ff77e16232ff2e1bf26b6d4501b8183e","role":"<private record accounts.a_east.role>","retailTenantId":101,"schema":"core"}
COMPASS_RETAIL_RUN_READONLY_DSN=postgresql://<a_east login>:<private password>@<retail host>:<port>/<published database>
```

`tenantId` 和 `workspaceId` 来自课堂 Web 范围，`role` 填发布私有记录中的实际 `a_east` 角色名。Compose 内的 DSN 使用容器可达的主机和端口，同一编排内通常是 `postgres:5432`。其他目标通过 `COMPASS_RETAIL_ALLOWED_ENDPOINTS` 按主机与端口放行。

配置 `COMPASS_MODEL_ENCRYPTION_KEY` 后执行 `pnpm db:migrate -F agent` 与 `pnpm db:seed -F agent`。Compose 部署的初始化服务执行这两步。seed 将默认连接登记到 Agent 库，保留已有配置。两项均空时跳过导入，只填一项会提示初始化错误。Agent 从数据库读取每个工作区的连接；升级完成后可在「数据与语义 → 数据连接」编辑并测试。

启动前用发布侧活动清单核对 release、内容摘要、数据库和 `a_east` 账号。以只读 DSN 连接后至少确认：

```sql
SELECT session_user, current_user, current_setting('transaction_read_only');
SELECT row_security_active('core.channel_product_daily'::regclass);
```

预期登录角色和当前角色均精确等于课堂绑定中的发布角色（对应 `a_east` profile），事务只读为 `on`，事实表 RLS 为 `true`。

## 正常演示

从仓库根启动基础链路：

```bash
pnpm dev -F web -F agent
```

登录 Web，进入绑定的工作区分析页。使用教学账号时，本课示例绑定「远山零售 / 经营总部」。输入框默认选择 Auto，也可手动选择支持 Tools 的主模型。发送以下问题。

> 比较 2025-11-17 至 11-23 和 11-24 至 11-30 两周，哪个渠道的实付金额变化最大？变化主要来自哪些商品？

正常路径应满足：

1. 页面先得到 Run ID，再展示模型实际发起的查询和分析过程。
2. 模型自行发现表和字段，按问题选择渠道及商品汇总；允许不同的查询序列。
3. 页面收到完整成功终态后自动折叠分析过程一次，用户可重新展开。
4. 回答按真实查询结果核对金额、差额与缺口；产物按本次工具结果发布。
5. 完整成功的问题和 finalAnswer 进入下一次追问历史，内部观察和文件字节留在执行侧。

该发布包的课堂对照值为：POS 基准期 1,410,889 分，比较期 543,596 分，变化 −867,293 分；SPU3、SPU1、SPU2 的变化分别为 −511,199、−259,344、−96,750 分。验收必须用当前活动清单对应的真实发布库重新查询，不能只比较文档数字。

## 对照 SQL

下面的查询使用与运行时相同的“每个事实粒度取最新 report_version”口径。务必以 `a_east` 只读身份执行。

```sql
WITH latest AS (
    SELECT DISTINCT ON (
        d.tenant_id, d.report_date, d.channel_id, d.sku_id, d.region_id
    )
        d.tenant_id,
        d.report_date,
        d.channel_id,
        d.sku_id,
        d.region_id,
        d.paid_amount_cent
    FROM core.channel_product_daily AS d
    WHERE d.tenant_id = 101
      AND d.report_date >= DATE '2025-11-17'
      AND d.report_date < DATE '2025-12-01'
    ORDER BY
        d.tenant_id,
        d.report_date,
        d.channel_id,
        d.sku_id,
        d.region_id,
        d.report_version DESC
)
SELECT
    ch.channel_code,
    COALESCE(SUM(d.paid_amount_cent) FILTER (
        WHERE d.report_date >= DATE '2025-11-17'
          AND d.report_date < DATE '2025-11-24'
    ), 0) AS paid_cent,
    COALESCE(SUM(d.paid_amount_cent) FILTER (
        WHERE d.report_date >= DATE '2025-11-24'
          AND d.report_date < DATE '2025-12-01'
    ), 0) AS compare_paid_cent,
    COALESCE(SUM(d.paid_amount_cent) FILTER (
        WHERE d.report_date >= DATE '2025-11-24'
          AND d.report_date < DATE '2025-12-01'
    ), 0) - COALESCE(SUM(d.paid_amount_cent) FILTER (
        WHERE d.report_date >= DATE '2025-11-17'
          AND d.report_date < DATE '2025-11-24'
    ), 0) AS delta_cent
FROM latest AS d
JOIN core.channel AS ch USING (tenant_id, channel_id)
GROUP BY ch.channel_code
ORDER BY ABS(
    COALESCE(SUM(d.paid_amount_cent) FILTER (
        WHERE d.report_date >= DATE '2025-11-24'
          AND d.report_date < DATE '2025-12-01'
    ), 0) - COALESCE(SUM(d.paid_amount_cent) FILTER (
        WHERE d.report_date >= DATE '2025-11-17'
          AND d.report_date < DATE '2025-11-24'
    ), 0)
) DESC, ch.channel_code COLLATE "C";
```

商品对照使用相同 `latest` CTE，增加 `channel_code = 'POS'` 过滤，并通过同租户 `core.sku → core.spu` 聚合。旧固定 SQL 参考位于 `examples/showcases/retail/reference/tools/business/retail_sales/sql.py`。当前产品通过 database_query 自主生成并执行查询，口径由数据字典与表、字段注释提供。

## 停止与故障演示

- 模型生成期间点击“停止任务”：页面先显示“正在终止”，原流确认模型退出及容量归还后显示“已终止”。后续模型、工具和重试计数不得增加。
- Retail 查询期间点击停止：用独立观察账号在 `pg_stat_activity` 确认该查询退出；Run 只在事务回滚、游标和连接清理确认后显示已终止。停止 Run 不发布未完成产物。
- 连续点击停止：清理只执行一次，重复请求复用相同结果。
- 查询失败：工具卡保留原 ToolCall ID 并显示安全错误，模型可以说明资料不足或继续；连接错误文本不进入页面。
- 断开原进度连接：BFF 中止上游，Agent 沿相同停止路径清理。页面保留已收到的安全工具过程，但不提交回答、历史或文件。
- 清理超时：终态为 `cleanup_unconfirmed`，故障专属连接不再复用。随后发起一次正常任务，确认新资源仍可用。

## 验证入口

真实 Retail 专项需要显式提供发布侧 `a_east` 只读凭据和同库管理凭据；两个 URL 必须指向同一个 `compass_retail_test_*` 专用库，且使用不同角色：

```dotenv
AGENT_RETAIL_TEST_DATABASE_URL=postgresql://<published ..._a_east login>:<password>@<host>:<port>/compass_retail_test_<id>
AGENT_RETAIL_TEST_ADMIN_URL=postgresql://<management login>:<password>@<host>:<port>/compass_retail_test_<id>
```

Agent 的完整 `test:integration` 还沿用既有 `AGENT_TEST_*` 与模型加密密钥配置；Retail 专项可单独执行 `cd apps/agent && uv run --locked pytest tests/test_agent_run_integration.py`。缺少显式凭据、库名不符合专用前缀、发布版本或摘要不匹配时都会明确失败，不会跳过或改用本机默认库。

```bash
pnpm contracts
pnpm contracts:check
pnpm check
pnpm build
pnpm test:integration -F agent
pnpm test:e2e -F web
pnpm smoke
```

单元与受控链路覆盖协议、竞态和清理分支；上述 Retail 专项覆盖专用发布库的固定查询、数据库端取消、清理和后续查询。真实模型配置、浏览器交互及课堂活动库仍需按 [Feature 989 AC](../mai-specs/989-lesson-15-agent-execution-kernel/spec/PRD.md#9-验收标准)逐项验收。
