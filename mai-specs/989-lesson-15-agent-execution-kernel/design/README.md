# 第 15 课技术方案

- [技术方案](TECH_DESIGN.md)与[代码附录](CODE_DESIGN.md)。
- [流式协议](STREAM_PROTOCOL.md)与[浏览器收到的完整 SSE](document/run-example.sse)。SSE 来自受控模型与查询结果，经过实际 Agent 编码、BFF 投影和浏览器解码。原 `stream-reference/` 保留早期协议的教学实现。
- [HTML 阅读版](TECH_DESIGN.html)沿用浅底绿色模板和本课封面。[阅读文稿](document/guide.md)和[生成配置](document/edition.json)引用主文的图和附录的代码。[构建脚本](document/build.mjs)将时序图和抓包放在并排的面板中。

在 Compass 根目录执行以下命令重建和检查阅读版。

```bash
node mai-specs/989-lesson-15-agent-execution-kernel/design/document/build.mjs
pnpm exec prettier --write --ignore-path /dev/null mai-specs/989-lesson-15-agent-execution-kernel/design/TECH_DESIGN.html
node .agents/skills/my-design-to-html/scripts/check-html.mjs mai-specs/989-lesson-15-agent-execution-kernel/design/TECH_DESIGN.html --report-dir work/lesson-15-design/html-qa
```
