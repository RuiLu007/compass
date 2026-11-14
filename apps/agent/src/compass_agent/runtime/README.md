# Agent Runtime

拥有 Session / Run 的运行生命周期和应用装配，所有执行路径复用 Harness、工具 Dispatcher 和持久执行边界。RunService 默认使用空工具集，产品入口显式提供 compass_definition()。

| 模块                                          | 职责                                            |
| --------------------------------------------- | ----------------------------------------------- |
| `service.py`                                  | 路由、Agent 定义、通用工具的应用级装配入口      |
| `context.py`、`task_state.py`                 | 已启用能力的上下文和当前 Run 状态               |
| `turn.py`                                     | 将模型正文、独立产物和工具派发接到 Harness 循环 |
| `tool_bindings.py`                            | 当前 Run 工具注册、策略、调用结果与事件衔接     |
| `control.py`                                  | 所有权、取消、期限与终态仲裁                    |
| `durable_service.py`、`journal.py`            | 持久推进、执行记录、Checkpoint 与恢复           |
| `store.py`、`entities.py`、`private_state.py` | 数据记录、短事务与私有状态封装                  |
| `run_scheduler.py`                            | 队列、租户配额、领取与执行租约                  |
| `commands.py`、`hitl.py`                      | 幂等控制、人工决定与确认后的执行                |
| `execution_trace.py`、`public_projection.py`  | 执行树和公开消息投影                            |
| `redis_stream.py`、`message_reader.py`        | 持久事件发布、回放和快照读取                    |
| `resources.py`                                | 产物与工作流保存邀请                            |

职责按变化原因归属：通用循环和提示词进入 Harness，工具行为进入 tools，静态工具统一由 [工具清单](../tools/catalog.py) 按能力上下文装配，授权数据源由 data_connections 提供绑定，脚本及个人工作流资产进入 workflows，意图和模型选择进入 routing。Runtime 不导入案例实现，工具经 RegisteredTool 注入；工具实现仅使用契约和预算策略，避免反向引用 Runtime 或模型循环实现。

外部 API 与已保存数据继续使用现有 analysis 命名。固定课堂日期和问题已移到测试输入；生产执行从本次请求取得参数。`llm/runtime.py` 负责单次供应商调用，和本目录的多步运行生命周期分工明确。

公开过程在模型返回完整调用后直接创建带具体名称的工具节点；模型生成工具参数期间的 `compass.tool.preparation` 只在内部使用，不投递到前端。持久过程通过独立任务以 50ms 间隔检查待发布事件，执行边界继续主动刷新；所有权、恢复和过期维护保持 500ms 周期。SSE 空闲读取间隔为 50ms，读取上限 16 条只限制单次响应大小，不等待凑满；空闲心跳为 5 秒。已确认写入和投影游标校验继续生效。

旧请求兼容集中在 history.py。已受理请求的回执查询不会创建或调度 Run。发布前使用 apps/agent/scripts/check_runtime_upgrade.py 核对受影响的活动 Run，完成或明确取消后切换。

`workflow_tools.py` 绑定试跑、复用及子任务入口。`subagent.py` 监督独立 Agent 循环进程，模型与数据库操作由宿主代理。父 Run 持有身份、目录及预算，子任务保存为同一 Run 的 `subagent` Step，模型和工具调用归于该节点。

子任务工具回执保存送给模型的原始 JSON 文本，恢复时使用同一文本计算请求指纹。工作流脚本在派发子任务前被 runner 拒绝时，保存失败回执，恢复后直接返回相同的错误码。
