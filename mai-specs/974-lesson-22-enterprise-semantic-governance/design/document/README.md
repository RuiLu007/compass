# 第 22 课 HTML 阅读版

## 企业经营语义层技术方案

[技术方案 HTML](../TECH_DESIGN.html) 聚焦 Agent 的语义应用，按总体架构、上下文工程、工具体系、自然语言维护、场景提示词和代码改动组织。[架构 SVG](agent-semantic-architecture.svg)由[技术方案 Markdown](../TECH_DESIGN.md)与[阅读稿](guide.md)共用，标明受理时固定清单、识别后默认语义装配、模型自主工具循环，以及前端确认与恢复。口径选择时序继续取自主方案正文。[生成脚本](render-design.mjs)按[版本配置](edition.json)读取架构图和封面，从注册目录核对工具集合，从 [PromptOps 策略](../../../../apps/agent/src/compass_agent/prompts/shared/policies.json)和[工具定义](../../../../apps/agent/src/compass_agent/prompts/tools/)摘录模型收到的仓库默认原文。更新图表或 Prompt 后重新生成。

模型输入一节的[交互阅读组件](context-reading.mjs)按 Runtime 注册的场景路径嵌入四类任务、两个周报变体及取数规则的完整 Prompt。红色粗体只标记源文中的约束，复制按钮保留原始文本。消息栈示例从[零售定义发布器](../../../../apps/web/scripts/retail-semantic-release.mjs)读取教学指标定义，从教学基包核对检索别名。

从 Compass 根目录执行以下命令。脚本使用项目的 Python 环境、Prettier 以及[离线 HTML 渲染器](../../../../.agents/skills/my-design-to-html/references/generation.md)。

```bash
node mai-specs/974-lesson-22-enterprise-semantic-governance/design/document/render-design.mjs
node .agents/skills/my-design-to-html/scripts/check-html.mjs \
  mai-specs/974-lesson-22-enterprise-semantic-governance/design/TECH_DESIGN.html \
  --report-dir work/lesson22-design-review/standalone-qa
```

官网根据[课程资料索引](../../../../apps/home/src/lib/lesson-resources.json)分发本页，阅读入口为 `/resources?lesson=22&doc=technical-design`。

## Compass Retail 语义资产详解

[阅读稿](retail-assets.md) 以建模、持久化和 Agent 应用为主线，展示三类资产结构、完整表集与初始化规模，再解释版本发布、执行绑定、语义解析和结果沉淀机制。专题通过[资产 ER 图](asset-model.mmd)展开持久化，专题数据库章节先列 Web 16 张和 Agent 1 张语义相关表，再逐组解释内容、权限、发布和查询记录。表名与关系按当前迁移及数据库元数据核对，已有组织与任务表单独说明。[版本配置](retail-assets.json) 指向[默认资产设计](../RETAIL_SEMANTIC_ASSETS.md)中的安装与核对 SQL。[生成脚本](render-retail.mjs) 调用[v2／v3 发布器](../../../../apps/web/scripts/retail-semantic-release.mjs)读取完整字段、关联、术语释义与指标公式，并应用 v3 销售额释义，结合[v1 基包](../../../../apps/web/db/main/seeds/retail-v1/)的别名、指向和候选绑定，使用项目内的 HTML 渲染器嵌入图形和检索数据，生成[单文件 HTML](../RETAIL_SEMANTIC_ASSETS.html)。

从 Compass 根目录执行以下命令。首次配置渲染环境时按[生成工具说明](../../../../.agents/skills/my-design-to-html/references/generation.md)安装固定依赖与 Chromium。

```bash
node mai-specs/974-lesson-22-enterprise-semantic-governance/design/document/render-retail.mjs
node .agents/skills/my-design-to-html/scripts/check-html.mjs \
  mai-specs/974-lesson-22-enterprise-semantic-governance/design/RETAIL_SEMANTIC_ASSETS.html \
  --report-dir work/974-semantic-assets-reading
```

校验两页后，运行 `node apps/home/scripts/lesson-resources.mjs --lesson 22` 同步 Home 的第 22 课资源。两页在独立文件和课程阅读器内均可互相跳转。
