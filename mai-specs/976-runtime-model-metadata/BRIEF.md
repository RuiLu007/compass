# 976: Runtime 模型元数据与默认生成参数

| 项         | 值                   |
| ---------- | -------------------- |
| Phase      | dev                  |
| Profile    | standard             |
| 优先级     | P1                   |
| 负责人     | andy                 |
| 目标日期   | 待填                 |
| 涉及子系统 | apps/agent, apps/web |

## 一句话描述

管理各模型的上下文窗口、输入和输出 token 上限及默认生成参数，供 Runtime 解析和上下文工程读取。

## 背景

模型目录已接入 LiteLLM 能力探测，Runtime 仍使用固定输出 token 默认值和通用上下文限制。不同模型及部署实例需要按实际能力计算请求预算。

## 关键决策

[技术方案](design/TECH_DESIGN.md)记录有效需求、验收条件和实施顺序。本期交付 LM Runtime 配置管理、默认参数解析与上游错误回传。Agent Runtime 上下文压缩和 Web 输入提示留作后续接入。

[实施记录](IMPLEMENTATION.md)记录实现范围、数据库版本和本次验证结果。

## 上下文索引

- [素材索引](context/_INDEX.md)
