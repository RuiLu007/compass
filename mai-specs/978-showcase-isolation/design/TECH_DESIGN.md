# Showcase 隔离与 Agent 通用能力收敛

> Feature：978 | Profile：simple | 状态：已实施，通过本次验收
> 需求来源：2026-11-14 当前会话，用户要求演示能力隔离、减少案例定制、让模型通过查数与代码执行自主完成任务。
> 模板来源：`.agents/skills/my-create-design/assets/templates/TECH_DESIGN.simple.tmpl.md`
> 代码基线：当前工作区；存在其他未提交的 Runtime、契约和 Web 调整，实施时重新核对。

[返回 BRIEF](../BRIEF.md)

## 需求依据与验收

默认产品 Agent 使用授权的数据查询、Todo 和代码执行能力解决问题。课程案例保存问题、样本和验收预期；临时工具或 Skill 通过独立演示装配运行。核心代码保持对具体课程、渠道、周报和商品核验的独立性。

用户已于当前会话授权按本方案实施。实现与本次验证记录维护在 [IMPLEMENTATION.md](../IMPLEMENTATION.md)。本次保留数据库结构与用户数据。

| AC   | 完成条件                                                                                                                                                                                           |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AC-1 | 默认工具收敛为 `todo_write`、`database_query`、沙箱可用时的 `python_analysis`，继续按用户配置加载 MCP；三个案例工具与零售专用 Workflow 退出默认清单。                                              |
| AC-2 | Runtime / Harness / 通用 Catalog 的可执行代码不依赖 Retail / Weekly / Showcase 模块，不识别课程名称、固定业务子任务、问题关键词或案例 ID；通用状态、预算、鉴权、审批、持久化、取消和恢复继续工作。 |
| AC-3 | Chat 案例发送普通问题和通用运行约束；更换日期、渠道、提问措辞后使用相同执行入口，不注入预定调用序列、业务答案或专属工具名单。                                                                      |
| AC-4 | 数据库工具从当前授权连接发现表和字段，执行受限只读 SQL；连接与输入授权保留 owner、租户和 Run 边界。配置中没有零售演示连接时，普通数据库查询和 Python 执行仍可使用。                                |
| AC-5 | 临时演示能力位于生产包外，由独立入口显式装配；生产默认目录、公开请求 DTO 和 wheel 不包含其专属能力。目录、导入、默认清单及打包边界有自动测试。                                                     |
| AC-6 | 既有数据连接、历史会话、产物与工作流资产保留；旧输入的兼容策略、活动 Run 的切换门槛及用户工作流不可执行时的行为明确。                                                                              |
| AC-7 | 普通汇总、缺数据、SQL 失败、Python 失败、越权拒绝、超时取消和恢复均有行为验证；至少用一组非课程固定日期与另一组合法渠道验证通路。业务结论按输入数据核对，模型可说明缺口。                          |

## 当前问题与删减清单

实施前静态清单有 9 个工具。下列清单为已批准的删除、停用和通用化范围。

| 对象                                                         | 建议                           | 范围                                                                                                                                             |
| ------------------------------------------------------------ | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `retail_sales`                                               | 删除生产工具及其固定业务执行链 | 查渠道、商品由 `database_query` 完成；连接和鉴权实现先抽离，数据表与样本保留。确有教学价值的固定 SQL 可作为演示参考材料。                        |
| `load_weekly_review`                                         | 删除                           | 目前只读取一份固定 Skill 文本；普通周报无需专属加载工具。                                                                                        |
| `build_weekly_review` 与 `weekly-retail-review`              | 删除生产实现                   | 模型按任务使用 SQL 汇总，复杂计算和绘图通过 Python；固定汇总脚本若用于课程讲解，仅保留在演示目录。                                               |
| `find_workflows`、`run_dynamic_workflow`、`reuse_workflow`   | 退出产品默认装配               | 当前输入绑定 baseline/current、两种渠道分析契约和固定 SubAgent profile；先把此实现隔离为教学示例。通用工作流回归产品前需独立消除这些约束。       |
| `todo_write`                                                 | 保留通用版本                   | 删除 Catalog 中 Retail 适配选择及 Runtime 的业务分支；计划用自然语言目标，完成按通用任务状态处理。                                               |
| `database_query`                                             | 保留并解耦连接                 | 当前依赖 RetailBinding、read_retail、固定 core/ops 和默认 core；改为授权连接及其 schema 范围，完成通用化后移入 `tools/builtin/database_query/`。 |
| `python_analysis`                                            | 保留                           | 保持沙箱、输入授权及预算；查询结果通过通用 AuthorizedInput 接入。                                                                                |
| `showcase` 请求字段和 `configure_workflow_route`             | 新请求移除                     | Web/BFF/Agent 同步去掉案例选择、日期正则触发、工具白名单与案例提示词。                                                                           |
| `require_product_evidence`、`constraints.verification`       | 新请求移除                     | Agent 已标 deprecated，公开 Schema 和 BFF 仍接受；移除该业务字段，保留通用结果核验扩展点。                                                       |
| `WORKFLOW_ENTRY_INSTRUCTIONS`、`PROFILES`、Retail 专用提示词 | 从生产核心移除                 | 数据口径随数据源说明提供；临时流程说明放演示自己的目录。                                                                                         |
| `execution_trace.py` 等旧工具标签和分支                      | 跟随依赖清理                   | 新工具使用通用元数据；历史记录优先展示已存标题，旧记录必要的显示映射放只读兼容投影。                                                             |

### 隐含依赖

- `runtime/service.py` 直接创建 RetailSession、RetailActions、HybridWorkflows，按问题解析诊断日期并限制工作流入口。
- `tools/catalog.py` 当前会按 Retail 状态替换 Todo 定义；`CatalogContext` 含 retail/database_only 等业务装配字段。
- `runtime/tool_bindings.py` 直接提取零售结果、转成 Python 输入。抽离时同步统一输入引用，不能只删除注册项。
- 数据连接的持久实体和管理 API 位于 `tools/business/retail_sales/`。这些是现有用户配置，先搬到独立连接模块，再删除查询案例代码。

## 最小实现方案

### 产品执行路径

```text
普通用户问题 / 案例问题
  → 同一 Agent 循环
  → 自主选择：发现表与字段 → 只读查询 → 按需 Python 处理
  → 依据实际工具结果回答，必要时说明缺口
```

复用 AgentDefinition、ToolSpec、ToolRegistry、Dispatcher、ToolLoader、AuthorizedInput 和沙箱。保留现有预算、取消、审批、审计及持久恢复能力；工具返回的业务数据由模型解释。无需为本次引入插件市场、动态 Python 模块扫描或新的工作流平台。

### 连接与查询

1. 将连接实体、仓储、凭据与探测从 retail_sales 抽到 `data_connections/`；保留已有表名、记录、账号和配置 API。现有 PostgreSQL 执行实现不顺带扩展 MySQL 驱动。
2. 查询工具拥有自己的 `AuthorizedDatabaseBinding`，只暴露当前允许的连接和 schema，内部使用可信凭据及只读账号。Retail 仍可作为一条数据源配置，账号本身的租户/区域限制继续生效。
3. 表发现、字段描述和受限 SELECT 为同一工具的操作。schema 范围来自受信配置或现有运行账号权限，在工具内执行校验，移除硬编码 core/ops 分支；禁止访问配置之外的 schema。
4. 单位、指标含义和业务字段说明通过表/列注释或数据源说明提供。查询口径属于可解释的数据上下文，案例问题不携带固定 SQL 和答案。
5. 查询结果提供通用结果引用，并在执行适配中登记 AuthorizedInput；Python 使用它处理数据。两段数据可由一次 SQL 合并查询，现有单输入引用仍可完成比较，本次不强制新增多输入 API。

### 代码执行与文件能力边界

当前 Python 执行已存在；产物通道目前固定 chart.png，数据库产物类型约束只允许 image/png 与 text/csv。

本次先保留已有结果与 PNG 通道。生成 HTML 源码可作为回答内容，HTML 文件下载、隔离预览和通用文件产物另列后续切片，不宣称已交付。该切片涉及 Agent 主库产物类型约束、Schema、Sandbox、下载接口及 Web 呈现，届时须单独登记迁移 owner、起止版本与发布恢复顺序。HTML 不直接在产品同源执行。

### 现有工作流资产

保留资产记录和查看/导出入口。当前零售专用脚本不再自动注册进普通 Chat；用户主动运行旧资产时返回明确的版本/能力不可用反馈。保留数据与现有迁移，禁止自动重写用户脚本或把旧脚本包装成通用能力。

通用 Workflow 的发现、执行、保存和复用具备长期价值，但其输入、工具和子任务必须来自显式声明，不能写死业务 profile。本次通过独立教学装配保留原演示；完整通用化另行设计，不为三个工具立即造一套新框架。

## 临时 Showcase 管理规范（拟写入 AGENTS）

### 目录与装配

```text
examples/showcases/<case_id>/
  README.md           # 目标、负责人、依赖、临时能力及退出条件
  prompts.md          # 普通案例问题、数据范围、预期检查点
  tools/<tool_name>/  # 确需演示工具开发时使用，目录名与工具名一致
  skills/<skill_name>/
  runner.py           # 显式注册演示自己的工具，复用通用 Agent 入口
  tests/
```

上述为拟新增目录与文件，只在存在实际内容时创建。产品代码位于 apps/agent/src；examples 不进入生产 wheel。普通业务问答案例只需 prompts 与测试，runner 和工具目录均按需建立。

### 准入与退出

1. **默认使用通用能力**：案例优先由普通问题、数据样本、数据字典和结果检查组成。
2. **临时扩展写明教学目标**：仅在演示工具/Skill/审批/调度本身时增加专属能力。README 必须列出已有能力不足之处、负责人、依赖、预期删除或转正条件。
3. **独立启用**：只有演示入口显式注册临时工具；常规 Chat 和生产镜像默认不可见，不向公开 Run DTO 加 case 枚举。
4. **单向依赖**：Showcase 可以调用核心公开接口；Runtime、Harness、默认 Catalog、schemas.py 和生产组合根均不导入 Showcase。
5. **共用执行约束**：演示工具沿用 Registry/Dispatcher 的授权、预算、取消、审批及结果边界；演示配置不改变核心判断和正常工具权限。
6. **退出有记录**：课程演示结束或实现被通用能力覆盖时删除；需要转为产品能力时，按普通 Feature 审查、补齐通用参数与跨场景验收，再进入正式工具目录和清单。
7. **失效可检查**：案例依赖变更时运行它自己的测试；过期条件在评审时核对。本次不建设常驻扫描或自动发布服务。

### 自动门禁

扩展现有模块边界测试：生产源码无 examples/showcases 导入，默认清单只有允许的通用能力与显式业务配置，Runtime/Harness 不导入零售案例，构建 wheel 无演示包。临时工具在自身目录注册 SPEC，案例装配通过 AgentDefinition.tools 注入，不修改生产 Catalog。

## 界面与请求契约

- 第 15/16 课案例保留为普通提问示例；先列计划、最多轮次、超时继续作为通用运行约束。
- 周复盘按钮只填业务问题和日期，不再携带 weekly_review 标记或指定固定 Skill 调用链。
- 渠道诊断按钮可以描述“分析贡献、独立复核”的目标；普通 Chat 不再强制走渠道工作流。需要展示 Workflow 工程机制时使用隔离演示入口，并明确标注演示用途。
- 案例提问与手输同一问题通过同一请求结构、同一工具装配和同一模型循环。
- Python、MCP、并发、超时、恢复案例以能力行为为验收重点，不绑定固定业务工具。

页面实施遵循 apps/web 局部规则和 Compass UI 样式规范，案例发送与历史工作流查看、导出均需浏览器验证。

## 供评审的代码设计

以下是方案接口示意，实际实现入口见 tools/catalog.py、data_connections/tools.py 和各工具目录。省略既有 Dispatcher、MCP、预算和持久化算法，它们继续复用。

### 工具装配

```python
# tools/catalog.py：正式工具清单
TOOLS = (TODO_WRITE, DATABASE_QUERY, PYTHON_ANALYSIS)

# 组合根 / ToolLoader：按当前可信用户和 Run 装配，取得数据库授权绑定。
# 现有 AgentDefinition.tools 接受 RegisteredTool；随 Run 装配可增加一个 provider 回调。
async def load_product_tools(trusted, control):
    entries = [todo_registration()]
    binding = await connections.authorized_binding(trusted, control)
    if binding is not None:
        entries.append(database_registration(binding))
    if sandbox_available:
        entries.append(python_registration())
    return tuple(entries)
```

实现以 ToolSpec 和注册工厂为真源，definitions 与执行继续使用同一批 RegisteredTool；不能在预检和实际调用分别复制上述选择规则。示意中的工厂名称为拟议名称，现有对应点是 ToolSpec.register、ToolLoader.assemble 和 API 组合根。

### 通用结果输入

```python
# 数据库工具执行适配：只有成功的、当前 Run 已授权结果可成为代码执行输入。
receipt = await query_authorized_database(binding, args, context)
if receipt.status == "ok":
    sources[receipt.query_id] = AuthorizedInput(
        context.run_id, context.owner, receipt.full_data, binding.revalidate
    )
return receipt
```

Run 结束前后沿用现有结果大小与所有权约束；模型预览截断不能改变代码执行实际使用的数据，超过授权输入大小应明确返回限制。Runtime 仅传递通用引用，不理解渠道、商品或业务证据结构。

### 演示隔离

```python
# examples/showcases/<case>/runner.py：显式装配，产品组合根无此导入。
agent = AgentDefinition(
    system_prompt=demo_instructions,
    tools=tuple(spec.register(demo_context, demo_handlers) for spec in DEMO_TOOLS),
)
```

DEMO_TOOLS 由该案例的局部 catalog 维护，临时工具仍使用标准注册与执行机制。

## 迁移顺序与任务

| 阶段              | 主要文件/职责                                                                                                   | 关联 AC          | 退出条件                                                           |
| ----------------- | --------------------------------------------------------------------------------------------------------------- | ---------------- | ------------------------------------------------------------------ |
| 1. 规则与基线     | 工具清单、模块边界测试、examples 规范、AGENTS                                                                   | AC-1、AC-5       | 默认能力与隔离边界可自动检查，保留当前工作区修改                   |
| 2. 查询通路去耦   | tools/business/database_query → tools/builtin/database_query、data_connections、api/runs、runtime/tool_bindings | AC-2、AC-4       | 无 RetailSession 即可发现表、查数、Python 分析，权限与只读规则通过 |
| 3. 移除案例路径   | Retail/Weekly 工具、提示词、routing/workflows、Runtime、课程按钮                                                | AC-1、AC-2、AC-3 | 原问题以普通 Agent 执行；需要保留的工程演示独立装配                |
| 4. 契约及存量处理 | schemas、BFF、生成契约、workflow 资产接口和历史投影                                                             | AC-6             | 新请求无案例字段，存量不被删除，旧请求/旧资产行为明确              |
| 5. 综合验收       | Agent/Web 测试、PostgreSQL/Sandbox、smoke                                                                       | AC-7             | 普通查询与计算、失败及恢复、构建和契约检查通过                     |

每阶段先完成替代通路，再移除依赖；不留下“目录删了、Runtime 分支还在”的结束状态。正式规则在实施验收后落入 AGENTS，验收后同步 docs/agent-rules.md，保留根 AGENTS.md 软链接。

### 请求与存量切换

新 Web/BFF/Agent 同批更新；新请求中的 showcase 和 constraints.verification 由输入校验明确拒绝，当前 UI 同步停止发送。旧幂等重试、已受理 Run 的请求快照与新请求入口分别处理；在接纳已知 requestId 时，先查现有记录并沿用已存快照，不能因新 DTO 移除字段而重新执行。

发布前核对活动/暂停/待确认 Run。受移除执行能力影响的存量 Run 需在旧版本完成，或通过现有控制接口明确取消后再切换；未满足时阻止移除相关执行器。该过程不删除历史快照、活动清单或用户资产。必要的旧字段读取放在明确的历史解码边界，不继续影响新请求调度。

### 数据库说明

本次阶段 1—5 目标为 Python 模块和请求契约调整，无预定 DDL、数据删除或迁移历史修改。连接表及工作流资产表保持不变；后续发现存量兼容确需 DDL 时先补本 Feature 的迁移登记。HTML 产物切片单独登记迁移，当前尚未设计起止版本和 SQL，不纳入本次实施承诺。

### 验收策略

- Agent lint/typecheck/test、Web lint/typecheck/test、受影响构建及 contracts/contracts:check。
- 真实 PostgreSQL 验证授权连接、schema 限制、只读拒绝、汇总与空数据；Sandbox 验证授权输入、执行结果和取消清理。
- UI 验证案例点击与同文手输请求等价，除通用运行约束外不携带课程执行标记；桌面、窄屏和键盘回归。
- 模型验收使用实际数据和当前模型，对比汇总金额、差额及缺口；不要求固定调用序列，不要求每次都用 Python，不把返回成功当作结论正确。
- 预算、超时、审批、持久恢复和 MCP 执行保持独立回归。

## 取舍与待评审点

删除案例专用工具可减少注册、提示词和状态耦合。自主 SQL 与计算的输出会随模型和问题变化，依靠数据口径、工具边界和结果验收控制质量；课程演示可稳定数据输入，模型路径保持自主选择。

实施重点已落实：旧工作流显示查看和导出说明，切换前核对非终态 Run，数据库 schema 范围来自运行账号权限。现有工作流数据保留，HTML 文件能力独立排期。实际实施和真实运行结果见 IMPLEMENTATION.md。

## 实施对齐

- 默认静态清单为三个 builtin；DataConnections 按 Run 提供授权绑定，数据库工具工厂同时提供声明和执行适配。MCP 继续动态注册。
- 旧 classroom budget effect 的写入、恢复分支及专用配置一并退出生产；表结构与历史公开投影保留。通用审批使用工具提案和执行回执。
- 兼容重试通过内部 sessions/retry 只读取得已受理回执，验证 owner、requestId 和历史规范化内容；该入口不创建或调度 Run。普通新请求继续按移除旧字段后的 DTO 校验。
- 旧 Workflow 资产保持查看、改名、启停与脚本导出。旧执行请求返回 workflow_unavailable。切换前的只读检查同时检查旧工具绑定、case 字段与旧预算审批 continuation。
- examples/showcases/retail/reference 保存历史代码供阅读；当前独立 runner 演示标准库 Skill 计算，历史 Workflow 代码不作为可运行交付。产品代码无依赖该目录的导入。
- 本次无 DDL 或数据迁移。原连接表、凭据、资产和会话保留；SQL 派生检查继续核对原结构。
