# Agent 工具

正式静态工具统一登记在 [catalog.py](catalog.py)，每个工具目录导出 `SPEC: ToolSpec`。定义、参数、执行适配和专属校验集中在同一个目录。

## 工具声明与 PromptOps

后台在 `prompts/tools/<tool_name>.json` 展示完整工具声明，包括函数名称、描述、参数 Schema 和调用示例。参数名称、类型、必填项、约束及执行绑定以注册代码为权威来源；随包导入从 `ToolSpec.definition()` 生成参数结构。在线允许编辑工具描述、参数说明及符合 Schema 的示例，保存、导入和发布都会在服务端核对代码契约。模型使用发布版本中的描述与示例，授权、审批和实际执行继续使用同一注册对象。

新增工具使用 `prompt_assets.tool_text(name)` 读取描述，参数说明可用 `tool_text(name, parameter)`。参数结构在定义代码中维护，执行 `python -m compass_agent.promptops.sync --write-sources` 可重新导出源码 JSON 并预览数据库差异；从根目录执行时按资产升级说明注入根 `.env`。模型可见的描述与示例合计最多 2000 字符，示例还需满足 Runtime 的 JSON 大小、深度与格式约束。

场景说明集中到 `prompts/support/routing/scenes.json`，阶段、技能、公共规则和前端示例按用途组成 JSON 资产。旧版本清单保留；旧文件名通过显式成员定位读取所属资产，不重新生成旧版本。数据同步入口与验收见 [资产粒度修订](../../../../../mai-specs/975-lesson-21-context-runtime-promptops/design/ASSET_MODEL_UPDATE.md)。

| 位置                                       | 职责                                                 |
| ------------------------------------------ | ---------------------------------------------------- |
| `builtin/<tool_name>/`                     | 通用工具，含查询、计划、Python、工作流与子 Agent     |
| `business/<tool_name>/`                    | 按工作区启用的正式业务工具，包含语义发现、查询与治理 |
| `catalog.py`、`catalog_contracts.py`       | 静态清单、能力组、可用条件与注册契约                 |
| `registry.py`、`dispatcher.py`、`batch.py` | 每轮冻结清单、统一执行与批次调度                     |
| `mcp/`、`sandbox/`、`artifacts.py`         | 用户远端工具、隔离执行后端与产物管理                 |

目录名与模型可见工具名完全一致。新增正式工具需导出 SPEC 并加入 catalog.TOOLS，预检、模型声明与实际注册共用条件；禁止在 Runtime 散落追加工具分支。按 Run 提供的授权绑定通过 tools_provider 注入 RegisteredTool，声明与执行使用同一对象。共享连接仓储位于 [data_connections](../data_connections/)，工具调用它提供的通用绑定。

## 经营语义工具

启用 Registry 的工作区按使用者权限装配 `search_semantics`、`get_metric_definition`、`resolve_semantics` 和 `query_metric`。查询使用 Run 固定的发布版本和私有参数引用。模型可按问题先查获准的明细。用户已指明指标或算法时直接使用；口径有歧义时可选择候选，或选择“都不合适”填写本次算法，继续原轮分析。缺少发布绑定只影响对应指标的正式查询。工具把可见定义、缺少的参数和失败原因返回模型，明细查询和交付各自校验权限。

语义检索的下一页游标绑定原 Run、发布清单和搜索条件。下钻使用已解析的子分组与父级筛选，从整体下钻可显式指定 `parent_group="total"`。对比使用同一指标、单位、分组、筛选及数据可见截止。对照期按用户要求显式传入。解析结果提供前一等长窗口供参考。

`discover_semantic_candidates`、`review_semantic_change` 和 `submit_semantic_change` 使用同一 Web 授权边界。主动建议使用 `trigger=suggest` 提交本轮查询回执、业务名称、口径、公式、单位、业务域和复用理由。模型先比较相关目录定义，服务按当前权限复核等价定义，每轮最多留存三个建议。用户勾选后才写入个人草稿。明确请求保存上一轮计算时使用 `requested`。修改已有指标时，向 `discover_semantic_candidates` 提供 `revise_metric` 和要修改的 `fields`。维护者在原会话核对逐字段差异，确认后写入同一资产的新草稿版本。预览和保存均复核维护权限及版本摘要。

## 资料检索

启用检索的工作区通过 `search_documents` 检索制度、历史产物和外部材料，通过 `read_document` 打开本轮已取得的段落及同版本相邻段。启用向量的资料包用现有 OpenAI Provider 的 text-embedding-3-small 生成查询向量，再与词法排名融合。可用 COMPASS_EMBEDDING_PROVIDER_ID 指定连接。搜索结果中的 retrievalMode 表示实际用到的 hybrid 或 lexical。搜索摘要与原文均登记来源。Web 在候选排名、取原文、继续执行和历史读取时按当前成员、工作区和发布方政策重新授权。来源包、时点与连接配置见 [Web 资料来源](../../../../../apps/web/README.md#第-23-课资料与回答引用)。

资料包可声明自己的核验操作。通用检索支持当前来源的行列、引文、计算与比较。已启用的零售包另外提供观察窗口、采购档位及工作日历核验；输出 Schema 与回执校验共用本轮相同的包配置。

## 默认装配

Runtime 默认工具集为空。产品入口 compass_definition() 装配 todo、database、python、workflow 及经授权的定时任务工具：

- todo_write：模型登记和维护任务计划。
- database_query：列出授权 PostgreSQL 连接、发现获准 schema 的表和字段、执行受限只读 SQL。多连接使用 connectionId，表名须带 schema。工具读取表和字段注释；账号权限与 RLS 继续约束可见数据。当前 MySQL 仅提供连接配置与探测。
- python_analysis：沙箱可用时执行通用分析代码；输入必须是当前 owner、租户和 Run 授权的数据引用。当前产物通道保留 PNG。
- create_workflow：用本次参数试跑模型生成的编排，成功后提出保存邀请。脚本入口支持异步函数体及 `async function (args) { … }` 包装，编译失败返回 `invalid_workflow_script`。
- sub_agent：接收 prompt，启动独立的只读分析循环，只向父级返回最终结果。
- schedule_read 读取当前工作区内本人计划和触发历史。schedule_write 生成一次所有者确认，摘要包含计划变更和按计划时区显示的预计触发时间，分析内容保留 Markdown 换行。工具使用已批准的计划版本、Run 租约与持久 Action 同事务完成更新和 Checkpoint。定时 Run 只装配读取工具。
- 个人工作流：按用户及工作区装配启用的动态资产，执行时再次复验。模型工具清单保留通用和 MCP 工具，剩余槽位按创建时间装配资产。
- 用户配置的 MCP：每个 Run 重新发现，沿用统一授权、预算、审批、取消和恢复机制。

数据库注册由 data_connections.tools.provider() 按可信运行上下文加载连接，使用 database_query 自己的声明和工厂。查询结果在工具适配内登记 AuthorizedInput，供 Python 使用；Runtime 不解析业务字段。

## 教学扩展

临时工具和 Skill 放在 [examples/showcases](../../../../../examples/showcases/README.md)，由独立入口显式装配。案例优先提供普通问题、样本和检查点。产品 Runtime、Harness、公开 Run 请求和默认清单不加入课程 case ID、日期分支或固定调用序列。旧零售工具与固定周报实现已退出生产；既有工作流资产保留查看、改名、启停和导出。

## 验证

从仓库根运行 pnpm lint -F agent、pnpm typecheck -F agent、pnpm test -F agent 和 pnpm build -F agent。test_tool_catalog.py 检查目录和注册一致性；test_showcase_isolation.py 检查默认清单、请求边界和实际构建的 wheel；test_generic_database_integration.py 使用独立 PostgreSQL、真实 Sandbox 和 Redis 验证查询、授权输入及审批恢复。
