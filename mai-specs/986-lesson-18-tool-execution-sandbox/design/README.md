# 第 18 课技术方案阅读版

[TECH_DESIGN.html](TECH_DESIGN.html) 是单文件离线阅读版，沿用 [第 14 课的阅读结构和模板](../../990-lesson-14-model-observability/design/README.md)。阅读内容包含 api/runtime/harness/workflows/tools 模块边界与迁移目录、Run 专属 MCP 装载、单工具与批次策略，以及 OpenSandbox + NFS-Ganesha 的部署、分配和存储生命周期。模块迁移和运行链已实现，启动方式见[工具执行指南](../../../docs/lesson-18-tool-runtime.md)，当前验证结果见[实施记录](../IMPLEMENTATION.md)。工具类型 UML 沿用[第 16 课的颜色标记](../../988-lesson-16-controlled-autonomy/design/TECH_DESIGN.md#agent-runtime-类型设计)。技术决策以[技术方案](TECH_DESIGN.md)为准。阅读版用 [guide.md](document/guide.md) 组织讲解，通过 [edition.json](document/edition.json) 引用原方案的图和拟议代码。

从 Compass 根目录重建并检查：

```bash
node .agents/skills/my-design-to-html/scripts/render.mjs mai-specs/986-lesson-18-tool-execution-sandbox/design/document/edition.json
pnpm exec prettier --write --ignore-path /dev/null mai-specs/986-lesson-18-tool-execution-sandbox/design/TECH_DESIGN.html
node .agents/skills/my-design-to-html/scripts/check-html.mjs mai-specs/986-lesson-18-tool-execution-sandbox/design/TECH_DESIGN.html --report-dir work/lesson-18-design/html-qa
```
