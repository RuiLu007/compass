# 980: 通用 Agent Runtime 与过期案例清理

| 项         | 值     |
| ---------- | ------ |
| Phase      | done   |
| Profile    | simple |
| 优先级     | P1     |
| 负责人     | andy   |
| 目标日期   | 待填   |
| 涉及子系统 | agent  |

## 一句话描述

移除 analysis 主链路，统一任务定义、提示词、工具与运行编排，保留第十五至二十课实际能力

## 背景

通用 Agent 执行与固定零售课堂案例耦合，需清理固定预设与空目录并保持真实运行功能

## 关键决策

本次会话为有效需求来源，验收条件维护于 [技术方案](design/TECH_DESIGN.md)。选择直接设计与实施，保留外部协议和数据格式，清理内部模块及过期预设。

- [技术方案阅读版](design/TECH_DESIGN.html)
- [实施与验证](IMPLEMENTATION.md)：357 项 Agent 单元测试、27 项 Worker 测试、89 项集成测试通过；2 项服务重启测试未启用。

## 上下文索引

- [素材索引](context/_INDEX.md)
