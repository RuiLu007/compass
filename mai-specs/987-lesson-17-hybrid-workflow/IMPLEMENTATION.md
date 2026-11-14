# 第 17 课混合工作流实施记录

[有效 PRD](spec/PRD.md) · [技术方案](design/TECH_DESIGN.md) · [Agent 启用方式](../../apps/agent/README.md)

## 实现

- Dynamic Workflow 由主 Agent 查找、生成、执行及提交。新脚本通过校验后，Chat 展示临时保存邀请，用户确认才写入资产库。独立 Node 进程中的 QuickJS WASM 运行 JS，父进程按预算、截止时间和数据绑定派发最多三个临时 SubAgent。每个子任务使用独立模型消息和 Verifier，主 Agent 消费一个核验结果。
- `agent/main` 的 0007 迁移提供 `workflow_asset`，按作用域和源码摘要去重。Web 通过已授权 BFF 提供 Chat 保存确认、按新日期复用，以及「插件 → Workflow」的源码查看、改名和启停。Agent 运行角色只能更新管理列。
- 固定提案经服务端主意图匹配或模型工具进入同一程序流程，复用绑定、日期准备与证据核验，输出 `budget_decision` 等待合同。合成活动限定在显式配置的租户和工作区。
- 周报 Skill 打包 `SKILL.md` 和受信 Python 脚本，汇总当前 Run 的已核验证据并报告周期缺口。现有 Run SSE、普通 Chat 与 Todo 路径保持兼容。
- Agent 契约、Web 类型、0007 空库 SQL 和各版本增量已生成。Docker 构建定义携带 Node 和锁定的 JS 依赖。

## 验证结果

| 范围             | 本次执行结果                                                                                                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 单测与静态检查   | `pnpm check` 全部 19 个任务通过。Agent 273 个、Web 102 个单测通过，契约漂移、格式、lint 和类型检查通过。                                                                              |
| Agent PostgreSQL | 迁移、模型和混合工作流共 31 项通过。覆盖原生与增量的 0006 → 0007 升级、运行角色权限、隔离 Retail 查询、跨用户拒绝、并发保存去重、换时段复用、待审双入口及周报缺数。                   |
| Web PostgreSQL   | `pnpm test:integration -F web` 共 22 项通过。                                                                                                                                         |
| 浏览器           | `hybrid-workflow.spec.ts` 通过，覆盖生成 JS、查看邀请且确认前零写入、插件页可见、改名、启停、换日期复用、固定待审和脚本周报。`analysis-composer.spec.ts` 的 12 项回归分两组全部通过。 |
| 重启复用         | 保存后重启本机 Agent，同一个资产 ID、版本和源码以新日期执行，事件显示复用，结果来自本次查询。                                                                                         |
| 构建与运行       | `pnpm build`、`pnpm db:sql:check -F agent`、连续两次 seed、`pnpm smoke --apps web,agent` 和 Compose 配置校验通过。                                                                    |

隔离回归测试使用 PostgreSQL 17、受限 Retail 登录与受控模型响应。固定端口预览另用 DeepSeek 和课程发布的 Retail 数据完成浏览器实跑。渠道诊断实际生成 JS、查询实付和商品贡献并独立复核，固定提案查询后进入待审，复盘 Skill 查询两个单期后生成周报。浏览器验证了查看 JS 和参数前后资产数不变，确认后插件页可见该资产。

## 2026-11-11 · 工作流资产自动注册

- 补齐 `runtime/workflow_catalog.py`，将当前用户及工作区的已启用资产转换为稳定的具名工具，首次模型推理前注入业务目录和参数 schema。旧资产无需重新保存，定义版本和源码保持原样。
- 按单次 16 个工具的既有预算直接装配 3 个资产工具，目录每页 20 条。其余资产支持按目录 ID 执行、按名称或游标继续发现，查询结果可进入下一步直接工具槽位。
- 首页渠道诊断示例使用普通业务问题，已有能力匹配时由模型选择复用。Chat 展示实际调用的资产名称与版本，保存卡文案说明后续自动调用。
- 涉及商品贡献和独立复核的诊断在执行工具集合中保留能完整交付该目标的路径，覆盖省略“并行”的问法。
- 本轮未增加迁移。调用时仍重新校验当前主体、启用状态、版本和源码摘要。新 Run 重建目录，改名保持工具身份，停用阻止新调用。

本次验证：Agent 284 项、Web 102 项单测通过，工作流 PostgreSQL 集成 7 项通过。端到端用例覆盖首次生成、确认保存、插件管理、全新对话普通提问自动调用及原有固定流程和周报。`pnpm check`、全 workspace 构建和契约漂移检查通过，13 项本地格式 Hook 测试按既有环境条件跳过。HTML 阅读版离线交互及四档宽度检查通过。

`8100` 使用真实模型和 Retail 连接完成两种业务表述及两组日期的自动复用，浏览器检查了无历史、无资产引用、无 showcase 的请求，结果显示既有 v1 资产。桌面、390 px 窄屏和键盘展开通过。临时运行结果及截图留在 `work/lesson-17-try/`，验收未改写用户已有资产。

## 复跑

以专用 `compass_agent_test_*` 和 `compass_web_test_*` 数据库配置 `AGENT_TEST_*`、`WEB_TEST_*`，供应并迁移 Agent 后运行：

```bash
uv run --locked pytest apps/agent/tests/test_database_tools_integration.py \
  apps/agent/tests/test_models_integration.py \
  apps/agent/tests/test_hybrid_workflow_integration.py
pnpm test:integration -F web
```

浏览器测试额外设置 `WEB_TEST_WORKFLOW=true`、`COMPASS_WORKFLOW_E2E_RETAIL_DSN`、`COMPASS_HYBRID_WORKFLOW_ENABLED=true` 和 `COMPASS_CONTROLLED_AUTONOMY_ENABLED=true`。用 `uv run --locked python -m apps.agent.tests.workflow_e2e_app` 创建独立 Retail 测试库，启动 `apps.agent.tests.workflow_e2e_app:app`，然后运行 `pnpm test:setup -F web -- --fixture teaching` 和 `pnpm test:e2e -F web -- hybrid-workflow.spec.ts`。首次生成场景使用空的工作流资产库。

## 2026-11-11 · 统一执行过程协议与 UI

- 新增 `RunExecutionNode`、`ExecutionTrace` 和 `compass.execution.node@1`，将现有模型轮次、参数准备、普通工具、Workflow、Skill 与临时 SubAgent 投影为统一节点。保留原执行与文件来源校验、保存邀请和最终回答合同。
- 节点类型与来源、执行环境独立建模，提供父子身份、revision、依赖、串并行分组、阶段元数据、耗时及安全摘要。MCP 和通用 Sandbox 仅完成合同和组件示例，完整执行在第 18 课接入。
- 现有 QuickJS 任务函数派发携带并行组，固定流程按实际并行准备分组；SubAgent 的公开模型轮次和 Retail 工具挂在自身节点下。已启动的 Promise 与异步延迟派发暂保持 Workflow 下的节点，完整异步上下文传播由第 18 课完成。
- Chat 使用统一执行树；全局 UI 规范、体验指南和课程 17～20 的产品 / 技术方案及 HTML 同步。桌面并行布局、390 px 窄屏、键盘操作、动态图标、状态切换、输入输出换行与复制均经过浏览器验证。
- 本次生产数据库无迁移。隔离端到端使用专用测试库和真实 Retail PostgreSQL 查询，模型响应使用受控夹具；已有用户的工作流资产保持原样。

本轮验证：Agent 287 项、Web 105 项单测通过，工作流 PostgreSQL 集成 7 项通过，执行过程组件与完整混合工作流端到端 2 项通过。`pnpm check` 全部 19 项任务、全 workspace 构建、契约生成一致性通过。HTML 17～20 的离线交互、搜索、复制、图表缩放与导出、打印及四档宽度检查均通过。临时日志、测试环境和截图保存在 `work/execution-nodes-*`。原有格式 Hook 中按环境跳过的测试仍按跳过记录。
