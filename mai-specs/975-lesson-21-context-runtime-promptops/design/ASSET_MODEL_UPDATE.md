# PromptOps 资产粒度修订

日期：2026-11-20。有效需求来源为本次用户确认的方案：场景目录聚合为 JSON、工具展示完整定义、参数 Schema 由代码维护并在后台只读、描述与示例支持在线编辑发布、源码与数据库同步。此修订覆盖原管理方案中逐条 Markdown 资产的相关约定。

## 需求与验收

| 编号  | 验收条件                                                                                     |
| ----- | -------------------------------------------------------------------------------------------- |
| AC-A1 | 40 个场景在一个 JSON 中维护；场景描述和示例参与实际分类请求；ID 与路由属性保持代码契约。     |
| AC-A2 | 一个工具对应一份完整 JSON 声明，展示名称、描述、完整参数 Schema 和调用示例。                 |
| AC-A3 | 描述、参数说明与示例可保存草稿、发布、回看和恢复；新任务使用新版本，已有任务保留绑定。       |
| AC-A4 | 服务端拒绝工具名、参数名、类型、必填项、约束等结构修改；示例 arguments 符合代码 Schema。     |
| AC-A5 | 分类说明、阶段与技能短指令、公共规则、工具补充说明和前端示例按职责归组；管理树显示当前资产。 |
| AC-A6 | 显式同步入口保留在线修改与未发布草稿，创建新的不可变发布清单；重复执行不产生多余版本。       |
| AC-A7 | 历史版本可读；旧格式快照恢复时将历史文本转换到当前资产结构，保留原快照与现存草稿。           |

## 资产结构与权威来源

本次将 129 个随包文件收敛为 39 个资产：14 个完整工具声明、一个场景目录、六个前端示例集合、四个职责指令集合及 14 份独立任务或辅助提示词。

```text
prompts/
├── tools/<tool_name>.json              # 完整工具声明；参数结构源于 ToolSpec
├── shared/
│   ├── execution.json                 # 阶段目标与可复用技能指令
│   ├── policies.json                  # 通用约束与输出协议
│   ├── tool-guidance.json             # 工具使用补充说明
│   └── fragments/base.md              # 主体职责
├── support/
│   ├── routing/
│   │   ├── scenes.json                # 40 个场景、描述与识别示例
│   │   └── instructions.json          # 分类、分流与交付类型识别指令
│   ├── starters/
│   │   ├── analysis.json              # 分析入口问题
│   │   └── models/*.json              # 按完整模型测试用途归组
│   ├── delivery/repair.md
│   ├── diagnostics/reliability-request.md
│   ├── session/title.md
│   └── subagent/analysis.md
└── tasks/                             # 四类任务及确有差异的场景交付规则
```

工具的参数结构以各目录的 `ToolSpec` / `ToolDefinition` 为权威来源。`catalog.bundled()` 生成当前代码对应的完整 JSON，导入时更新声明；参数描述作为注释保留。`structured.validate_tool()` 在保存、导入与发布时核对代码契约。比较时仅忽略 JSON Schema 的 description 注释，名为 description 的真实业务参数仍受结构校验。

场景的固定属性与可编辑文字在同一份随包 JSON 中维护。分类器读取本次绑定版本的描述和示例；路由特征从安装包读取并进入部署策略摘要。工具通过 `tool_text()` 读取完整 JSON 中的描述与参数说明。修改代码中的参数后，可在上述同步命令增加 `--write-sources`，将当前注册代码的 Schema 重新导出到源码 JSON；未加 `--apply` 时数据库保持预览模式。

Web 的工具编辑器只开放描述、参数说明和示例；完整 JSON 通过共用代码组件只读展示。示例使用 description 与 arguments，arguments 按可信代码 Schema 校验。发布的描述和示例进入模型实际收到的工具声明。工具注册的授权、审批、执行后端与资源限制继续由代码负责。

描述与示例合计遵守现有 `ToolFunction` 的 2000 字符限制。参数说明遵守 Runtime 的 Schema 大小和嵌套限制；示例同时检查 Schema 格式约束、JSON 大小与深度。在发布前完成这些检查。运行回执保留实际资产路径、完整资产摘要与成员选择；追踪日志按实际 JSON 资产去重，历史投影则记录原文件来源。

`legacy_layout.json` 只用于明确的成员定位和历史读兼容。运行时按职责取结构化资产的成员，校验其所属不可变版本的摘要。旧文件名退出随包资源与管理树，已绑定旧清单的 Run 仍可读取旧版本。当前选择策略摘要覆盖聚合布局与校验实现。

## 持久化、升级与恢复

Owner 为 Agent / main，沿用现有 `prompt_file`、`prompt_version`、`prompt_generation`、`prompt_head`、`prompt_command`。**本次不增加表或列，不增加 Alembic 版本或 SQL 交付。** JSON 正文仍保存于现有 Text 字段。本次数据整理由显式命令执行，API 导入和应用启动均不隐式升级资产。

从 Compass 根目录预览：

```sh
uv run --env-file .env --locked --package compass-agent python -m compass_agent.promptops.sync
```

停止 Agent 与 Scheduler 后提交，并允许当前部署重新注册策略：

```sh
uv run --env-file .env --locked --package compass-agent python -m compass_agent.promptops.sync --apply --reset-runtime-policy
```

提交前将文件、版本、代次及原 Head 信息保存到 `work/promptops-assets-upgrade/`，文件权限为 0600。事务锁定 Head，保留旧版本与旧清单，将当前正文聚合到新路径。已发布的在线修改覆盖对应源码成员；未修改的成员采用当前源码；未发布草稿迁移为新资产草稿，保留独立发布步骤。遇到源码与线上同时修改同一现有资产时拒绝覆盖，要求先合并。有效执行租约存在时拒绝重置部署策略。

旧格式快照仍可按原路径与版本回看。恢复旧格式快照时，新建一份当前结构的清单，按成员复制历史正文，并补齐当前代码需要的新资产；原快照保持不变。草稿继续保留，受影响文件按现有 CAS 规则重新核对基线。当前格式的快照沿用原来的精确版本恢复行为。

## 验证入口

- Agent：现有应用 lint、typecheck、test；新增 `test_prompt_assets.py` 覆盖真实声明装配、结构篡改、聚合发布、历史恢复与幂等升级。
- PostgreSQL：显式设置 `COMPASS_PROMPTOPS_POSTGRES_TEST=true` 与 `AGENT_TEST_ADMIN_URL`，执行 `tests/test_prompt_assets_postgres.py`；仅创建独立 `compass_agent_test_promptops_*` 数据库并在结束后清理。
- Web：现有应用检查，更新 PromptOps E2E 以使用聚合示例；实际检查桌面、390px 窄屏、只读 Schema、无效示例、键盘与 JSON 预览。
- 契约与分发：重新生成并检查契约，验证 wheel 包含 JSON 与兼容布局，不包含已移除的碎片文件。
