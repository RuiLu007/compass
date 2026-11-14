# Agent 工具

正式静态工具统一登记在 [catalog.py](catalog.py)，每个工具目录导出 `SPEC: ToolSpec`。定义、参数、执行适配和专属校验集中在同一个目录。

| 位置                                       | 职责                                                |
| ------------------------------------------ | --------------------------------------------------- |
| `builtin/<tool_name>/`                     | 通用工具，含查询、计划、Python、工作流与子 Agent    |
| `business/<tool_name>/`                    | 按 Agent 配置启用的正式业务工具，当前无内置业务工具 |
| `catalog.py`、`catalog_contracts.py`       | 静态清单、能力组、可用条件与注册契约                |
| `registry.py`、`dispatcher.py`、`batch.py` | 每轮冻结清单、统一执行与批次调度                    |
| `mcp/`、`sandbox/`、`artifacts.py`         | 用户远端工具、隔离执行后端与产物管理                |

目录名与模型可见工具名完全一致。新增正式工具需导出 SPEC 并加入 catalog.TOOLS，预检、模型声明与实际注册共用条件；禁止在 Runtime 散落追加工具分支。按 Run 提供的授权绑定通过 tools_provider 注入 RegisteredTool，声明与执行使用同一对象。共享连接仓储位于 [data_connections](../data_connections/)，工具调用它提供的通用绑定。

## 默认装配

Runtime 默认工具集为空。产品入口 compass_definition() 选择 todo、database、python、workflow 四组，按实际授权与后端条件启用：

- todo_write：模型登记和维护任务计划。
- database_query：列出授权 PostgreSQL 连接、发现获准 schema 的表和字段、执行受限只读 SQL。多连接使用 connectionId，表名须带 schema。工具读取表和字段注释；账号权限与 RLS 继续约束可见数据。当前 MySQL 仅提供连接配置与探测。
- python_analysis：沙箱可用时执行通用分析代码；输入必须是当前 owner、租户和 Run 授权的数据引用。当前产物通道保留 PNG。
- create_workflow：用本次参数试跑模型生成的编排，成功后提出保存邀请。脚本入口支持异步函数体及 `async function (args) { … }` 包装，编译失败返回 `invalid_workflow_script`。
- sub_agent：接收 prompt，启动独立的只读分析循环，只向父级返回最终结果。
- 个人工作流：按用户及工作区装配启用的动态资产，执行时再次复验。模型工具清单保留通用和 MCP 工具，剩余槽位按创建时间装配资产。
- 用户配置的 MCP：每个 Run 重新发现，沿用统一授权、预算、审批、取消和恢复机制。

数据库注册由 data_connections.tools.provider() 按可信运行上下文加载连接，使用 database_query 自己的声明和工厂。查询结果在工具适配内登记 AuthorizedInput，供 Python 使用；Runtime 不解析业务字段。

## 教学扩展

临时工具和 Skill 放在 [examples/showcases](../../../../../examples/showcases/README.md)，由独立入口显式装配。案例优先提供普通问题、样本和检查点。产品 Runtime、Harness、公开 Run 请求和默认清单不加入课程 case ID、日期分支或固定调用序列。旧零售工具与固定周报实现已退出生产；既有工作流资产保留查看、改名、启停和导出。

## 验证

从仓库根运行 pnpm lint -F agent、pnpm typecheck -F agent、pnpm test -F agent 和 pnpm build -F agent。test_tool_catalog.py 检查目录和注册一致性；test_showcase_isolation.py 检查默认清单、请求边界和实际构建的 wheel；test_generic_database_integration.py 使用独立 PostgreSQL、真实 Sandbox 和 Redis 验证查询、授权输入及审批恢复。
