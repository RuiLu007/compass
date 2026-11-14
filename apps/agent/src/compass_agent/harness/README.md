# Agent Harness

通用模型与工具循环。依赖模型调用服务、工具契约和注入端口，保持独立于 Retail、Workflow 实现和持久化存储。

| 模块              | 职责                                             |
| ----------------- | ------------------------------------------------ |
| `context.py`      | AgentDefinition、输入上下文与体积限制            |
| `prompts.py`      | 系统提示词目录；按注册能力追加约束               |
| `executor.py`     | Step、模型生成、工具派发衔接、消息回填与有界重试 |
| `contracts.py`    | 推理状态、Step、观察及注入端口                   |
| `autonomy.py`     | 步数、Token、成本、工具预算与停止策略            |
| `verification.py` | 任务结果的可选核验钩子                           |

`AgentExecutor.execute_tools()` 接收请求准备、结果校验与工具派发函数。它保存模型返回的工具调用顺序和 continuation；工具观察由派发端口按相同顺序回填。任务专用的查询、计划、核验留在相应内置工具包。

默认 AgentDefinition 和 RunService 使用通用系统提示词和空工具集。应用可以注入 RegisteredTool 与 System Prompt；个人 MCP 复用现有装配与工具执行链路；教学 Skill 在独立 Showcase 入口中使用。
