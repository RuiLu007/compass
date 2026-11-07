# 第 11 课精简技术方案阅读版

本目录使用项目 [my-design-to-html Skill](../../../../../.agents/skills/my-design-to-html/SKILL.md)生成独立的[精简版 HTML](../../TECH_DESIGN.compact.html)。原有 V2、V3 HTML 及其生成配置保留。

内容依据 [V2 技术方案](../../TECH_DESIGN.v2.md)及其当前有效的 LiteLLM、System One、诊断响应增量，聚焦职责、数据、调用流程、输入输出和核心代码。源方案继续维护技术决策；本目录负责阅读组织。

| 文件           | 职责                                                              |
| -------------- | ----------------------------------------------------------------- |
| `guide.md`     | 目标量化表、精简图表、文件变更与执行伪代码                        |
| `edition.json` | 生成配置；PreparedCall、Runtime 签名及 SDK 示例从源方案代码块提取 |
| `hero.svg`     | 首屏统一入口与 Adapter 关系图                                     |

在 Compass 根目录执行：

```bash
node .agents/skills/my-design-to-html/scripts/render.mjs mai-specs/993-lesson-11-llm-runtime/design/document/review/edition.json
pnpm exec prettier --write --ignore-path /dev/null mai-specs/993-lesson-11-llm-runtime/design/TECH_DESIGN.compact.html
node .agents/skills/my-design-to-html/scripts/check-html.mjs mai-specs/993-lesson-11-llm-runtime/design/TECH_DESIGN.compact.html --report-dir work/design-to-html/compact-11-12/11
```

构建依赖与通用检查由 Skill 维护。截图与临时结果保存在 `work/`；最终 HTML 内嵌图表、样式与交互，可以单文件离线阅读。
