# 990: 第 14 课：模型调用可观测与运营治理

| 项         | 值                                                                                                                                                |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase      | done                                                                                                                                              |
| Profile    | standard                                                                                                                                          |
| 优先级     | P1                                                                                                                                                |
| 负责人     | andy                                                                                                                                              |
| 目标日期   | 待填                                                                                                                                              |
| 涉及子系统 | apps/agent（Runtime观测、LiteLLM回调、异步有界导出）、apps/web（Chat关联与按需观测展示）、contracts（观测关联字段）、deploy（观测配置与后端接入） |

## 一句话描述

为 Compass 建立轻量模型调用观测能力，关联 Logical Call / Attempt，展示调用链、性能、故障与模型用量，并保证敏感内容保护和观测故障隔离。

## 背景

承接第11课统一模型接入、第12课意图与模型路由和第13课可靠执行，依据本次第14课PPT选型，以B类生产运营型为基线，采用轻量OpenTelemetry埋点、LiteLLM与本地 Docker Langfuse，支撑五个核心Showcase；按实际业务建设，避免过早平台化。

## 关键决策

- 2026-11-05：用户指定从四张第 14 课 PPT 抽取的[开发默认上下文](context/lesson-14-development-context.md)作为本课后续需求、设计、评审与实现前的默认读取材料。采用其中第 4 页的 B 类生产运营型选型基线；具体规格和验收在后续阶段明确。

- 2026-11-06：用户授权连续完成 PRD、技术设计、方案评审与学员 HTML。后端确定为本地 Docker Langfuse；[PRD](spec/PRD.md)为需求与 AC 真源，状态仍为 draft。

## 本次设计交付

2026-11-06：完成 PRD、技术方案与代码设计，方案评审五项发现修订后通过；按用户要求由 subAgent 使用 my-design-to-html 生成学员单文件阅读版。后续授权实施的当前结果如下。

## 实施与验收完成

2026-11-06：用户要求补齐分布式观测并完整实现验收。已交付 OTel / LiteLLM 接入、Logical Call / Attempt、可信 W3C 传播与实例隔离、安全导出、平台对比与调用编号 UI、本地 Docker Langfuse 六服务。`pnpm check` / `pnpm build`、真实 PostgreSQL / Langfuse / 浏览器 / 生产镜像 / 默认端口 smoke 通过；18 项 AC 完成，代码自检问题已修复。当前主应用 3000 与 Langfuse 3002 可访问，既有业务记录保留，前置数据库版本已对齐 0004。

学员 HTML 同步分布式与实施收敛，离线和窄屏 QA 通过。完整范围、实际数字、私有配置位置和保留边界见[实施记录](IMPLEMENTATION.md)及[代码自检](review/code_review.md)。工程实现随后纳入 Compass 独立仓库的附注标签 `compass/L14-complete`。

## 学员交付

[第 14 课学习资料包](lesson-14-learning-materials.zip)收录 PRD、Markdown 技术方案与离线 HTML。包内按当前工程版本说明端口和正文采集策略。

## 上下文索引

- [开发默认上下文：四页 PPT 内容抽取](context/lesson-14-development-context.md)
- [素材索引](context/_INDEX.md)
- [产品需求文档](spec/PRD.md)
- [技术方案](design/TECH_DESIGN.md)
- [供评审的代码设计](design/CODE_DESIGN.md)
- [方案评审与修订复核](review/design_review.md)
- [实施与验收](IMPLEMENTATION.md)
- [代码自检](review/code_review.md)
- [学员 HTML 技术方案](design/TECH_DESIGN.html)
- [阅读版编辑源与生成入口](design/README.md)
