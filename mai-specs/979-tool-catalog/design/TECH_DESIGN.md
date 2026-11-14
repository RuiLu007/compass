# Agent 工具目录与统一注册

> Feature：979 | Profile：simple | 状态：已实施
> 需求来源：2026-11-14 当前会话，用户批准工具目录与注册改造及验收后补充 AGENTS 约束。
> 模板来源：`.agents/skills/my-create-design/assets/templates/TECH_DESIGN.simple.tmpl.md`

[返回 BRIEF](../BRIEF.md)

## 需求依据与验收

- AC-1：通用工具位于 `tools/builtin/<tool_name>/`，业务工具位于 `tools/business/<tool_name>/`；静态工具名与目录名相同，定义、参数及执行适配集中到各自目录。
- AC-2：`tools/catalog.py` 是静态工具清单唯一入口，预检与实际注册复用相同声明和启用条件；保留 MCP 与用户工作流资产的动态注册。
- AC-3：Todo 状态管理独立于 Retail；经营分析继续校验日期、依赖和查询结果，保留现有参数、完成规则和事件协议。
- AC-4：Python 工具由 Agent 配置与沙箱决定是否启用，授权输入仍绑定当前 owner 和 Run，保留输入限制和产物约定。
- AC-5：现有工具执行、工作流、取消与恢复相关测试通过；增加目录注册一致性、通用 Todo、Python 无 Retail 绑定的验收测试。
- AC-6：验收通过后在 `docs/agent-rules.md` 增加后续工具创建规范，保留根 AGENTS.md 软链接；更新相关源码路径说明。

本次不改变数据库结构、HTTP DTO、工具名称、审批语义和业务工作流算法。当前工作区已有 Runtime 重构，保留已有修改。共享工作流引擎、连接仓储和沙箱后端保持独立职责。

## 最小实现方案

每个工具导出 `SPEC: ToolSpec`。声明包含名称、类别、能力组、定义、启用条件、并行规则与注册工厂。`catalog.definitions()` 与 `catalog.registrations()` 共用 `selected()`。Runtime 仅提供本轮能力上下文和执行适配，不新增静态工具注册分支。

通用 Todo 提供任务状态与依赖转换；Retail 使用子类约束业务目标、时段和查询匹配，通过现有 RetailSession 接入结果校验。无 Retail 的 Agent 可以启用 `todo`。Python 使用独立 `python` 能力组，保持 Dispatcher 与 SandboxBinding。

Workflow 的工具定义和输入解析迁入各工具目录；共享执行、资产持久化、收据核验继续由 workflows 引擎承担。数据库查询目前使用零售受限账号，归入业务层。

## 供评审的代码设计

拟议调用关系，验收见实施记录：

```python
context = CatalogContext(enabled=definition.builtin_tools, sandbox=sandbox_available)
model_tools = catalog.definitions(context)
registered_tools = catalog.registrations(context, handler)
```

两条入口必须使用一致上下文。隐藏的 `apply_budget` 保留审批策略，模型清单不暴露；具名工作流继续按资产版本动态注册。重复工具名由 Registry 拒绝。

## 实施任务与验证

| 任务                     | 模块                        | 关联 AC    | 验证                                   |
| ------------------------ | --------------------------- | ---------- | -------------------------------------- |
| 工具独立目录与依赖迁移   | tools、调用方、测试导入     | AC-1       | 导入检查、目录约束测试                 |
| 集中声明与运行时装配     | catalog、runtime、workflows | AC-2       | 预检/注册一致性、既有工具测试          |
| 通用 Todo 与 Python 去耦 | builtin、Retail 适配        | AC-3、AC-4 | 正反例、无 Retail 执行                 |
| 集成验收与规则           | agent、文档                 | AC-5、AC-6 | lint、typecheck、test、build、文档检查 |

## 关键决定

按当前调用契约保留工作流日期参数；本次统一组织与注册，工作流引擎继续支持现有经营分析入口。新增静态工具需维护一份 SPEC 和一个清单项，目录与注册一致性由自动测试检查。
