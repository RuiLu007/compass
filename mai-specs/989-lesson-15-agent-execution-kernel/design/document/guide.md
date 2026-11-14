## 1. 整体执行链路

一次 Chat 提交建立一个 Run，也就是从开始执行到产生终态的一次任务。Agent 调用模型，执行只读查询并回填结果，再通过 SSE（服务端发送事件）交付回答。用户可以在模型调用或查询期间停止任务。

[查看本课文件变更清单](#file-changes)

{{code:architecture}}

| 层 | 负责什么 |
| --- | --- |
| Chat | 发送问题，显示分析过程和最终回答。每个 Run 在 Chat 中显示为一轮对话（Turn）。 |
| Web BFF（服务于前端的 Web 后端） | 检查登录、工作区、模型权限和请求来源，把可信身份传给 Agent，并将进度转换为浏览器使用的 SSE 事件。 |
| Agent Run API | 分配 Run ID，受理停止，以 SSE 编码进度和唯一终态。 |
| Agent Runtime | 推进 ReAct 循环（调用模型 → 执行工具 → 回填结果 → 继续调用或结束），提交唯一终态。 |
| LLM Runtime（模型调用层） | 跟选定的模型供应商通信，管理重试、取消和调用容量。 |

本课讲解创建请求内的执行生命周期、一次只读查询、停止和流式交付。完整工具体系、工作流、计划执行与持久会话恢复留到后续课程。

## 2. Agent Runtime 类型设计

### Agent Runtime 总体 UML

**黄色是本课实现的模块和类型。** **浅绿色是沿用的类型。** 图中按 Run 生命周期、通用 ReAct 循环、模型调用、进度与 SSE 交付组织。`AgentExecutor.execute_tools()` 通过 `prepare`、`accept` 和 `dispatch` 三个回调接入任务。点「放大图表 → 原始尺寸」可查看类名、字段和依赖关系。

{{code:runtime-uml}}

### 从 Run 入口到通用执行循环

Compass 自研 Agent Runtime，在 Python 异步循环里直接控制系统提示词、工具结果如何写回模型上下文，以及 Run 何时结束。模型供应商协议、重试和取消复用模型调用层。

{{code:components}}

`RunService` 选模型并组装任务。`RuntimeTurn` 提供三个回调。`prepare` 组装模型输入，`accept` 审核响应并决定能否直接回答，`dispatch` 将工具调用交给 `HarnessTools`。`AgentExecutor` 推进循环，每次在工具结果处理完成后关闭 Step。

### 一次 Run 的数据关系

{{code:state}}

| 类型 | 边界 |
| --- | --- |
| RunControl | 管理 Run 的执行期限、当前在途调用、停止原因和唯一终态。 |
| AgentState | 保存模型消息、步骤与续接令牌。 |
| Step | 一次逻辑模型调用及其触发的零个或多个工具调用。底层重试属于该次逻辑调用。 |
| ToolCall / ToolResult | 观察按原调用 ID 回填。成功、空结果和可处理错误都能进入上下文。 |
| TaskState | 继承 `AgentState`，按 ID 保存当前 Run 的数据库结果。模型只在本 Run 内引用 `resultId`。 |

{{code:loop}}

## 3. 最小工具 · 只读数据库查询

{{code:database}}

| 动作 | 如何接入 |
| --- | --- |
| 绑定 | Agent 根据已授权的租户和工作区选用受限 Retail 连接。模型只提供工具操作及查询参数。 |
| 探索 | `list_tables` 列出可读业务表，`describe_table` 查询可读字段、类型和可空性。 |
| 执行 | `query` 验证单条只读 SELECT。Retail 只读事务校验身份、引用表权限和行级权限。输出最多 50 行、4 KiB。 |
| 回填 | 用原 `toolCallId` 向模型回填结果或安全错误。模型观察含 `resultId`，浏览器工具预览省略它。 |
| 提交 | 模型调用 `submit_database_result({resultId})`，Agent 从保存的结果生成 Markdown 表格。这个通用查询示例的文件状态为 `none`。 |

{{code:dispatch}}

## 4. 停止与资源清理

Chat 发独立的 `POST /api/web/v1/analysis/runs/{runId}/cancel`，收到停止回执后继续消费创建 Run 时打开的响应流。

{{code:cancel}}

每次启动模型调用或工具批次都做第一次检查。批次内每次工具调用由 `CallControl` 约束自己的期限。第二次检查只在整个 Run 要结束时执行一次。

| 检查点 | 行为 |
| --- | --- |
| 派发前 | 先查 Run 的停止和期限，再登记活动阶段。停止受理后不再派发。 |
| 停止受理 | 独立 POST 返回 `accepted` 或 `already_terminal`。原 SSE 负责交付最终状态。 |
| 在途资源 | 模型复用 LLM Runtime 的流关闭和容量释放；SQL 发出有界取消，等原命令退出，再关闭游标、回滚和关闭连接。 |
| 提交前 | 同步复核后只提交一次。清理未确认优先返回 `cleanup_unconfirmed`。清理已确认时，按首个原因返回超时 `failed/run_timeout` 或停止 `canceled`。没有停止原因时提交候选结果。 |

{{code:commit}}

## 5. Agent → BFF → Chat 流式协议

时序图和完整响应来自同一次受控 Run。Agent、BFF 和浏览器解码均走实际代码，模型输出和查询结果使用固定测试数据。EOF（流结束）表示 HTTP 响应完整关闭。

{{code:sequence}}

{{code:wire}}

| 下游包 | 如何读 |
| --- | --- |
| `seq=1` | 独立的 `run.started`，取得 Run ID。 |
| `seq=2` | 路由、首个 Step、分析节点、工具参数准备和工具开始。 |
| `seq=3` | 查询结果、首 Step 完成、下一 Step 和结果发布。 |
| `seq=4` | 独立的 `run.finished`，终态带 Markdown。响应关闭后 Chat 才提交回答。 |

分析节点通过 `compass.execution.node@1` 报送模型阶段状态。有限 v1 链路的公开节点会剥离 `detail`，包括 Thinking 与模型旁白正文。最终正文在 `run.finished.finalAnswer`。

| 规则 | 约定 |
| --- | --- |
| 包信封 | `protocol`、`version`、`requestId`、`runId`、`seq` 和 `event`。每帧一行 `data: <JSON>` 加空行。 |
| 批次 | 进度按数组位置处理，最多 16 项、32 KiB、24 ms。BFF 按下游包从 1 连续重编。 |
| 扩展 | 按 `name@version` 识别。未知可选扩展可跳过，已注册扩展经验证后转发。 |
| 提交 | BFF 验证到上游 EOF 才转发终态。Chat 验证到下游 EOF 才渲染回答、折叠过程并提交历史。 |

## 6. 文件变更清单

按当前目录定位第 15 课的实现。`[新增]` 是本课引入的能力文件，`[迁移]` 是重构后的落点，`[调整]` 是本课接入涉及的现有文件，`[生成]` 是契约脚本产物。共用文件的行注描述本课涉及的职责。

### Agent 执行内核

{{code:files-runtime}}

### 模型路由与调用接缝

{{code:files-model}}

### 只读查询与单次工具控制

{{code:files-query}}

### Web BFF 与 Chat

{{code:files-web}}

### 接口契约与生成入口

{{code:files-contracts}}

### 测试与课堂复现

{{code:files-tests}}
