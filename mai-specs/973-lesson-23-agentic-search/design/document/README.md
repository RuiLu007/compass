# 第 23 课技术方案阅读版

[离线 HTML](../TECH_DESIGN.html) 由[技术方案 Markdown](../TECH_DESIGN.md)生成。七章沿用原文题目和顺序。工具、正式指标、阶段、停止条件与工程交付清单均从本章表格抽取。两段 Prompt 和反馈 JSON 直接取原文代码块。报价图读第六章两版报价，按余额、比例和整档约束计算，并与原文决策核对。每章的“对照原文”可在离线文件内打开完整章节。

[阅读布局](guide.md)只维护讲解顺序、图注与 Verify 时序图。[生成入口](build.mjs)复用项目的离线渲染器，装配本课交互组件。[配置](edition.json)指定原文和代码块，样式与交互分别在 [reader.css](reader.css) 和 [reader.js](reader.js)。经营数值、工具接口或决策有变化时先更新主方案；构建会检查章节、相关表格和案例计算是否仍匹配。

从 Compass 根目录生成并检查阅读版。

```sh
node mai-specs/973-lesson-23-agentic-search/design/document/build.mjs
node .agents/skills/my-design-to-html/scripts/check-html.mjs mai-specs/973-lesson-23-agentic-search/design/TECH_DESIGN.html --report-dir work/lesson-23-reading-qa
```

课程原件的词法召回实验仍可单独运行。

```sh
python3 mai-specs/973-lesson-23-agentic-search/design/lexical_probe.py
```
