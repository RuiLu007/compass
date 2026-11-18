# 第 20 课技术方案阅读版

[离线 HTML](TECH_DESIGN.html) 展示 Agent 内 scheduler、Run 消费迁移与核心设计。决策以[技术方案](TECH_DESIGN.md)及[代码设计](CODE_DESIGN.md)为准。阅读版由 [guide.md](document/guide.md) 和 [edition.json](document/edition.json) 引用图和拟议代码。

从 Compass 根目录重建并检查。

```bash
node .agents/skills/my-design-to-html/scripts/render.mjs mai-specs/984-lesson-20-production-agent-runtime/design/document/edition.json
pnpm exec prettier --write --ignore-path /dev/null mai-specs/984-lesson-20-production-agent-runtime/design/TECH_DESIGN.html
node .agents/skills/my-design-to-html/scripts/check-html.mjs mai-specs/984-lesson-20-production-agent-runtime/design/TECH_DESIGN.html --report-dir work/lesson-20-design/html-qa
```
