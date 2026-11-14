# 982: 数据连接管理与默认 Retail 接入

| 项         | 值                                                                         |
| ---------- | -------------------------------------------------------------------------- |
| Phase      | done                                                                       |
| Profile    | standard                                                                   |
| 优先级     | P1                                                                         |
| 负责人     | andy                                                                       |
| 目标日期   | 待填                                                                       |
| 涉及子系统 | apps/web（数据连接管理与 BFF）、apps/agent（连接配置持久化与 Retail 查询） |

## 一句话描述

在管理端创建和维护多条 PostgreSQL/MySQL 数据连接，并默认接入本地 Retail PostgreSQL 实例。

## 背景

管理端已有「数据连接」入口。现有 Retail 分析链路通过部署环境配置连接和工作区绑定，且部分来源参数固定在代码中。管理员需要在页面维护连接，本地环境初始化后可使用默认 Retail 连接执行分析。

## 关键决策

连接配置由 Agent 应用库管理，沿用工作区权限和 Retail 只读身份。有效需求与验收以 PRD 为准。

## 上下文索引

- [素材索引](context/_INDEX.md)
- [PRD](spec/PRD.md)
- [技术方案](design/TECH_DESIGN.md)
- [技术方案审阅版](design/TECH_DESIGN.html)
- [方案评审](review/design_review.md)
- [业务测试计划与执行结果](test/TEST_PLAN.md)
- [实施与验证记录](IMPLEMENTATION.md)
