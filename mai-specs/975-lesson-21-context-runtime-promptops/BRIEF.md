# 975: 第 21 课｜系统提示词与 Context Runtime

| 项         | 值                                       |
| ---------- | ---------------------------------------- |
| Phase      | draft                                    |
| Profile    | standard                                 |
| 优先级     | P1                                       |
| 负责人     | andy                                     |
| 目标日期   | 待填                                     |
| 涉及子系统 | agent runtime, promptops, web management |

## 一句话描述

覆盖六类经营任务的提示词分层、PromptOps 管理、运行时装配、热更新及回归。

## 背景

Compass 的提示词与指令分布在路由、Agent 执行和工作流中。需要统一场景、阶段与租户的组装规则，并让发布版本在跨步骤和恢复时保持一致。

## 关键决策

本课使用 standard Profile，按 PRD → 技术设计整理。PRD 是本课功能与验收编号的维护入口。工程设计基线为 `compass/L20-complete`。

## 上下文索引

- [素材索引](context/_INDEX.md)
- [PRD 与验收标准](spec/PRD.md)
- [技术方案](design/TECH_DESIGN.md) · [HTML 审阅版](design/TECH_DESIGN.html)
- [代码设计](design/CODE_DESIGN.md)
