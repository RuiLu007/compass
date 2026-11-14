# 979: Agent 工具目录与统一注册

| 项         | 值     |
| ---------- | ------ |
| Phase      | done   |
| Profile    | simple |
| 优先级     | P1     |
| 负责人     | andy   |
| 目标日期   | 待填   |
| 涉及子系统 | agent  |

## 一句话描述

按工具名独立目录，区分通用和业务工具，集中注册并固化规范

## 背景

工具定义和执行散落，Todo 与 Retail 耦合，Python 启用依赖 Retail

## 关键决策

通用与业务工具分层，每个工具独立目录；使用集中静态清单，复用现有 Registry、Dispatcher 和动态工具机制。

## 上下文索引

- [素材索引](context/_INDEX.md)

## 本次实施

需求与验收真源：[技术方案](design/TECH_DESIGN.md)。采用 simple，复用当前会话需求，按技术设计、实施和验收连续推进；不另建 PRD。

## 验收结果

375 项 Agent 测试、lint、typecheck、build 与契约检查通过。规则已写入 AGENTS 正文，详见 [实施与验证记录](IMPLEMENTATION.md)。外部环境集成测试未纳入本次目录重构验收。
