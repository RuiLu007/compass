# 通用 Agent Runtime 与过期案例清理

> Feature：980 | Profile：simple | 状态：已实现并验证
> 需求来源：2026-11-14 当前会话；模板：my-create-design/assets/templates/TECH_DESIGN.simple.tmpl.md

[返回需求](../BRIEF.md)

## 需求依据与验收

用户要求统一意图/模型与确定性 Workflow 分流，所有任务进入通用 Agent Runtime。内置工具归 tools/builtin，用户工具复用 MCP，Skill 复用现有资产。系统提示词由 Harness 管理。清理 analysis 和固定课堂预设，保留第十五至二十课的实际执行能力。

- AC1：生产源码移除 analysis 包及导入；Harness 循环和上下文不依赖 Retail 实现。
- AC2：Retail 查询、计划约束、核验与 CSV 作为内置能力保留；预算、停止和模型执行保持通用。
- AC3：移除固定问题、日期和路由特判；确定性 Workflow 与显式案例入口兼容并通过统一运行服务执行。
- AC4：动态 Workflow、子任务、Skill、MCP、Sandbox、批次约束、审批、恢复、Worker 调度保持现有协议。
- AC5：Agent lint、类型、单元测试通过；执行可用的 PostgreSQL 集成检查，明确报告环境缺口。

## 最小实现方案

- harness/context.py 定义 AgentDefinition 与有限上下文构造，harness/prompts.py 管理系统提示词。
- harness/executor.py 统一多步模型调用、工具回填和停止，业务完成处理通过注入函数提供。
- runtime/service.py 为应用装配入口；runtime/turn.py、runtime/tool_bindings.py 连接模型循环、工具和运行状态。
- tools/builtin/retail/{plan,verifier,session,actions}.py 管理真实零售能力；工具实现不导入 Runtime 或 Workflow 实现。
- routing/workflows.py 管理确定性 Workflow 选择与显式运行入口；生产代码不保留固定课堂问题/日期。
- RuntimeStore、ExecutionJournal、RunScheduler、HTTP 路径、DTO 和数据库格式保持兼容，无数据库迁移。
- 既有测试宿主中的受控模型与故障模拟继续用于回归测试；清理范围为生产代码中的过期演示特判。

## 供评审的代码设计

以下为设计示意，执行顺序和恢复边界沿用现有实现。

```python
@dataclass(frozen=True)
class AgentDefinition:
    system_prompt: str
    tools: tuple[RegisteredTool, ...] = ()

# Harness 接收工具目录和完成处理，不识别渠道或商品。
async def handle(step, result, request):
    if not result.tool_calls:
        return complete(result.text)
    step.tool_calls = tuple(result.tool_calls)
    state.messages.append(assistant_message(result))
    state.continuation_token = result.continuation_token
    return await dispatch(step, request.tools)
```

工具注册、参数校验、派发及结果绑定继续共用现有 Dispatcher。计划/核验状态留在内置能力中，Checkpoint 内容字段沿用当前格式。取消与资源清理异常继续向 RunControl 传播；工作流结果须通过现有核验后发布。

## 实施任务与验证

| 任务               | 范围                      | 关联 AC  | 完成条件                         |
| ------------------ | ------------------------- | -------- | -------------------------------- |
| 拆分职责与迁移导入 | Harness、Runtime、Retail  | AC1、AC2 | 无旧包依赖，通用上下文可独立使用 |
| 清理预设并统一路由 | Routing、提示词、案例入口 | AC3      | 无固定问题/日期路由分支          |
| 回归运行与工具     | Agent 测试                | AC4、AC5 | 实际记录检查结果与未验项         |

## 兼容与边界

保留外部 analysis 命名的 API 与 DTO，避免联动破坏 Web 和已保存 Run。业务工具保留现有数据核验与权限规则；原有未提交改动在此基础上增量维护。内部模块迁移同步测试导入与 README，不改其他 Feature 文件。

实际变更、检查结果和升级边界见 [实施记录](../IMPLEMENTATION.md)。
