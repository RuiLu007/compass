# 第 23 课 · 华东跑鞋投放复议

课程维护人负责此案例。学员通过 Compass 普通 Chat 提问，模型根据先前观察选择指标、明细、制度、历史待办和外部资料。讲师使用本目录装配经营时点、授权受众及独立评分。完整问题、业务数据分发校验和、七份原件和讲师 SQL 见[本课需求](../../../mai-specs/973-lesson-23-agentic-search/spec/PRD.md)。

课堂需要可复算的历史时点、只读数据库与可单独撤销的资料。固定日期、演练身份和价格变体由本目录管理。运行时使用产品自带的工具和 Agentic Search Skill。课程结束时可停止专用数据库并关闭工作区的来源绑定。生产配置与教学配置独立管理。

## 准备独立部署

需要 PostgreSQL 17 的 `initdb`、`pg_ctl`、`psql`，仓库锁定的 Node、pnpm、Python 环境，本地可用的 Redis，及支持工具调用的真实模型。课程 SQL 文件须已按 `data/retail/README.md` 安装。本目录的命令在 Compass 根目录运行。用 `pnpm install --frozen-lockfile`、`pnpm setup` 准备依赖。

1. 为教学 Web 和 Agent 各准备独立、已迁移的 `compass_web_test_*` 与 `compass_agent_test_*` 数据库。使用本地测试部署的应用和迁移账户配置 `WEB_DATABASE_URL`、`WEB_TEST_MIGRATION_DATABASE_URL`、`DATABASE_URL`、`AGENT_MIGRATION_DATABASE_URL`。设置专用服务令牌、状态加密密钥和 Redis 密码，配置 Web 与 Agent 内部 HTTP 地址。教学身份密码通过 `WEB_FIXTURE_PASSWORD` 与 `WEB_TEST_PASSWORD` 提供。Web 及其配置运行于本机回环地址。初始化步骤见 [Web 教学环境](../../../apps/web/README.md)和 [Agent 部署](../../../apps/agent/README.md)。
2. 依次执行 `pnpm db:seed -F web -- --fixture teaching` 和 `pnpm db:seed -F web -- --fixture retail-semantics`，为工作区提供六个合成账户、零售语义资产和审批基础数据。使用有权的账户在产品中配置可用模型，核对工具能力，给分析员授予模型调用权。将实际 Provider ID 设为 `COMPASS_SEARCH_MODEL`。
3. Web 配置 `COMPASS_DOCUMENTS_ROOT`、`COMPASS_DOCUMENTS_PIN_DIR` 为两个独立的服务端私有目录。Agent 配置 `COMPASS_SEARCH_WORKSPACES` 和 `COMPASS_SEMANTIC_WORKSPACES`，包含教学工作区 `99500000-0000-4000-8000-000000000020`。启用 Context Runtime、PromptOps 和受控自主执行，为同一 Provider 配置可用步数、token、费用与总时长。将 `127.0.0.1:55473` 加入 `COMPASS_RETAIL_ALLOWED_ENDPOINTS`。用 `WEB_PUBLIC_ORIGIN` 指向此教学 Web。启动 Web/Agent，检查 Agent 的 `/readyz`。
4. 在权限隔离的终端按下列顺序装配。仅 `freeze.py` 新建数据库集群，输出目录须为新路径。随后 `configure.py` 为销售部门分析员及报价受众组配置授权，将受限账号绑定到教学工作区，发布资料。最后 `publish_metrics.py` 走试算、两名独立评审和发布流程。创建后的连接口令只保存在权限为 0600 的文件中。

已有教学库升级时，先在载入该库 Agent 环境的终端运行 `.venv/bin/python -m compass_agent.promptops.sync` 预览提示词更新，再加 `--apply --backup-dir work/lesson-23-runtime/prompt-backup` 发布并保存旧版本。新会话会固定当时发布的 PromptOps 清单。

```bash
case_dir=examples/showcases/lesson-23-agentic-search
class_data=work/lesson-23-runtime/frozen
.venv/bin/python "$case_dir/freeze.py" "$class_data" --port 55473
.venv/bin/python "$case_dir/configure.py" "$class_data" "$COMPASS_DOCUMENTS_ROOT"
.venv/bin/python "$case_dir/publish_metrics.py" --frozen "$class_data"
.venv/bin/python "$case_dir/verify_dataset.py" "$class_data"
.venv/bin/python "$case_dir/documents_acceptance.py" --web "$WEB_PUBLIC_ORIGIN" \
  --runtime-connection "$class_data/connection.json"
```

`verify_dataset.py` 通过独立讲师连接运行金标准 SQL，再以分析员和冻结库受限身份查询五项已发布绑定、支付及净额按自然月的三行结果、无样本、覆盖缺口和延迟状态。还会核对地区、渠道、地点、行为事件、库存单据、库存流水的正式值域。三个类型字段的枚举均来自受限连接的列注释，并经各自的正式实体定义登记中英文含义。`documents_acceptance.py` 在同一部署内建立独立测试主体并检查 12 个资料探针、历史版本、撤权与恢复。需要停止冻结库时运行 `pg_ctl -D "$class_data/pgdata" stop`。下次启动同一数据可使用 `pg_ctl -D "$class_data/pgdata" -l "$class_data/postgres.log" -o '-h 127.0.0.1 -k /tmp -p 55473' start`。

## 普通 Chat 端到端

```bash
out=work/lesson-23-runtime/results
.venv/bin/python "$case_dir/evaluate.py" --case simple --output "$out"
.venv/bin/python "$case_dir/evaluate.py" --group candidate --count 6 --parallel 2 --output "$out"
.venv/bin/python "$case_dir/evaluate.py" --case finance --output "$out"
.venv/bin/python "$case_dir/evaluate.py" --case store --output "$out"
.venv/bin/python "$case_dir/evaluate.py" --case inventory-gap --output "$out"
```

使用 `--resume 已受理的记录.json` 重新采集同一 Run。使用 `compare.py 结果目录` 按问法、组别和提示词摘要汇总实际派发、token、已归属费用及耗时。使用 `--start-index` 补跑特定问法，编号与 PRD 的三问循环对应。每次请求新建 Session；并发提交共享已登录的安全会话。每个文件记录 Run、问题、快照、提示词版本、受保护引用、模型、工具及可计量用量。讲师需逐项核对因果、资格、签批和行动时间。`needs_instructor_review` 表示自动检查已通过，完整验收以逐项复核为准。

讲师可以在同一教学 Agent 数据库上用 `evaluate.py --recount 原始记录.json --output 校正目录` 离线核算费用。汇总读取每次真实模型调用的计量回执，包含路由分类器，按模型分别列出已知 token、实际费用、用量未明的次数及对应预留。旧记录须经核算后再参与费用汇总。原始回答与验收判定保存在各自的记录中。

对照组使用拥有相同数据版本、应用版本、模型、PromptOps 修订和预算的独立教学部署。在同一部署内依次运行以下组，每组都传 `--documents "$COMPASS_DOCUMENTS_ROOT" --output "$out"`。执行中的组保持当前访问政策稳定。

| 组名                                                                                             | 核对内容                                                                       |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| `baseline`、`sql`、`semantic`                                                                    | 全源关闭 Skill、仅明细、仅语义。与候选同样执行三种问法各两次                   |
| `without-semantic`、`without-detail`、`without-knowledge`、`without-history`、`without-external` | 对应能力缺口、剩余事实和采购状态；当前权限与固定清单逐项生效                   |
| `price`                                                                                          | 08:45 报价函替换为新版本，对照采购决定与保留余额                               |
| `rebate-expired --case store`                                                                    | 到期的杭州附批与当前申报条件                                                   |
| `budget`                                                                                         | 两次实际工具派发上限和未完成条件                                               |
| `unavailable`                                                                                    | Run 成功固定后，在专用目录内临时移走内容包，终态后恢复，检查服务故障与部分交付 |

`materials.py` 可单独发布新的资料版本或权限指针。参数 `--withdraw E-CAL-2025` 单独撤掉公开日历，`--withdraw E-1009` 单独撤掉商业报价。系统按新 Run 固定内容摘要，每次使用时复查当前权限。结束组间比较后执行 `.venv/bin/python "$case_dir/materials.py" "$COMPASS_DOCUMENTS_ROOT"` 恢复默认全源、当前报价和 Skill。讲师的运行记录、应用连接和模型凭据均保存在 `work/` 的私有环境中。

## 讲师核对采购建议

载入独立教学部署的环境，使用默认完整报价、五类来源和已发布的业务字段字典。在专供该回放使用的 Agent 库执行下列命令。讲师驱动以分析员身份提交一个新 Run，从实际采购收货和手册确定观察窗，查询会话和指标，生成采购意见与行动安排，再按当前权限回读每个角标。结果文件保存 Run ID、正文和回读数量。普通 Chat 的模型独立完成率由 `evaluate.py` 另行统计。

```sh
.venv/bin/python "$case_dir/purchase_acceptance.py" \
  --result work/lesson-23-runtime/instructor/purchase.json
```

在命令中加入 `--full-case`，可在同一个新 Run 中核对完整的 G1–G7，包括七至九月现金流、两个五日窗的业务记录、库存与预算行动。结果文件的 `anchorsByKey` 列出各项判断在产品报告里的角标和引用 ID。
