# Compass 第 17 课实施上下文｜M3

## 前置能力

第 16 课 M2 受控 Agent Executor，可在受约束的节点内执行 Plan → Execute → Verify。

## 本课要交付的运行能力

- 定义 Workflow、Node、Edge，提供 Sequence、Conditional Routing 与基础 Parallel/Join。节点声明输入输出，汇合按节点结果推进，异常沿明确边界传递。
- 封装 Agent Node。确定性阶段做任务分类和权限预检，开放分析交给 M2，分析结束回到 Workflow 做结果校验与路由。
- 只读报告分支可完成。行动建议分支到达 Human Decision Node 时生成可检查的等待结果，本期只提供挂起接口和等待语义。
- 课程比较 Loop、Subworkflow、Agent-in-Workflow 和 Workflow-as-Tool，实战采用 Workflow Shell + Autonomous Analysis Node。交付 M3、Workflow 控制流与混合架构 ADR。

## 课次边界

进程内轻量编排。Loop、Subworkflow、Workflow-as-Tool 作为教学中的控制结构和组合模式。持久等待、审批回调和恢复在 M5。

## Compass 接入约束

- 沿用 Agent 侧推理所有权与可信身份上下文。权限预检可以走项目已有入口或显式教学夹具，未获许可的输入不可触发 Agent Node。
- Workflow Node 复用 M2 执行器、工具契约和停止原因。人审等待返回后不得推进后继业务动作。

## 跨课契约

任务以 Run 进入，外部能力经受控接口调用，关键状态变化生成结构化事件。课堂复用同一个 Compass 经营分析案例，每课以可观察的正常和异常结果验收本期增量。
