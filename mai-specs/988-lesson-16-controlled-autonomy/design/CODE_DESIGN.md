# 第 16 课 · 核心代码路径

[返回技术方案](TECH_DESIGN.md)

## 生产入口与工具装配

调用摘记按当前源码整理。产品入口提供通用能力，工作区授权决定数据库绑定，Run 目录在第一次构造工具声明时冻结。

```text Runtime 调用入口
get_agent_executor() → RunService(definition=compass_definition(), tools_provider=provider(connections))
└── RunService.run(command, context, control, emit, verifiers=())
    ├── tool_loader.discover(...) → 本轮 MCP 注册项
    ├── tools_provider(...) → 授权工作区的数据库注册项
    ├── RuntimeTurn.execute_turn(..., publish_text, max_steps)
    │   └── AgentExecutor.execute_tools(..., prepare, accept, dispatch)
    │       ├── prepare() → RunToolBindings.definitions() → 冻结的 ToolRegistry
    │       ├── ModelService.run(...) → 模型文本或工具调用
    │       ├── RunToolBindings.dispatch() → ToolResult 按调用 ID 回填
    │       └── 无工具调用 → RunCompleted(finalAnswer=模型正文)
    ├── 追加工具产物，执行已配置的 verify_result(...) 回调
    └── RunControl.commit(candidate) → RunResult
```

工具和模型的真实调用均通过同一 Run 裁决边界。

## 正文完成分支

摘自 `runtime/turn.py` 的 `RuntimeTurn.execute_turn()` 内部回调，省略执行轨迹记录。

```python RuntimeTurn.handle 分支
if not calls:
    control.require_running()
    candidate = RunCompleted(
        status="completed",
        finalAnswer=self._final_answer(result.text),
        file=AnalysisFileNone(status="none"),
    )
    return candidate
if len(calls) > RUN_STREAM_LIMITS["toolsPerStep"]:
    raise RunFailure("model_incomplete")
if not controlled and max_steps is not None and step.index >= max_steps:
    raise RunFailure("step_limit")
return None
```

| 入口 | 当前关键行为 |
| --- | --- |
| `RunService.run()` | 根据模型能力选择可用目录；全局、场景、调用级回调按序合并。若安装回调，先核验候选结果再发布。 |
| `RunToolBindings.freeze()` | 组合静态能力、应用提供的注册项和发现的 MCP 注册项。Checkpoint 记录本轮目录标识。 |
| `RunToolBindings.dispatch()` | 控制派发边界；存在声明为按序执行的调用时整批串行。将结构化结果按调用 ID 追加到模型历史，并独立保存产物引用。 |
| `database_query.handler.registration()` | 检查数据源仍获授权，在同一 Run 登记 `AuthorizedInput`，供后续 Python 工具引用。 |
| `AutonomyBudget.reserve()/settle()` | Attempt 派发前预留，按已知用量或保守上界结算。底层重试消耗同一 Run 预算。 |
| `history.decode_command()`、`DurableRuntime` | 处理历史请求，沿用保存的期限和活跃时长。旧执行模式已退出，恢复继续校验冻结工具目录。 |

## Todo 动作

| 动作 | 主要字段 |
| --- | --- |
| `replace` | `baseVersion`、`items`、可选 `selectTodoId`。事项包含 `id`、`goal`、`dependsOn` 和 `optional`。 |
| `select` | `baseVersion`、`todoId`。 |
| `complete` | `baseVersion`、`todoId`、可选 `selectTodoId`。 |
| `skip` | `baseVersion`、`todoId`、`reason`。适用于符合条件的可选事项。 |
