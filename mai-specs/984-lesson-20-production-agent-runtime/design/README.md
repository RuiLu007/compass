# 第 20 课技术方案阅读版

[TECH_DESIGN.html](TECH_DESIGN.html) 是单文件离线阅读版，沿用 [第 14 课的阅读结构和模板](../../990-lesson-14-model-observability/design/README.md)。技术决策以[技术方案](TECH_DESIGN.md)与[代码设计](CODE_DESIGN.md)为准。阅读版用 [guide.md](document/guide.md) 组织讲解，通过 [edition.json](document/edition.json) 引用原方案的图和拟议代码。

从 Compass 根目录重建并检查：

```bash
node .agents/skills/my-design-to-html/scripts/render.mjs mai-specs/984-lesson-20-production-agent-runtime/design/document/edition.json
pnpm exec prettier --write --ignore-path /dev/null mai-specs/984-lesson-20-production-agent-runtime/design/TECH_DESIGN.html
node .agents/skills/my-design-to-html/scripts/check-html.mjs mai-specs/984-lesson-20-production-agent-runtime/design/TECH_DESIGN.html --report-dir work/lesson-20-design/html-qa
```
