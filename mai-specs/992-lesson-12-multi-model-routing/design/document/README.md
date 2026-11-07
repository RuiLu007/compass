# 第 12 课技术方案学员阅读版

本目录将维护中的[技术方案](../TECH_DESIGN.md)和[代码设计](../CODE_DESIGN.md)组织为[独立离线 HTML](../TECH_DESIGN.html)。结构沿用第 11 课 V3：需求简述 → 方案概述（目标、原则、整体架构）→ 详细设计（数据、UML、时序、配置、接口、目录、类型与核心代码）。

文档为设计待实现状态。采用当前维护方案末尾的范围补充：本期验证流程、契约和模型接入；JEV 使用代码默认接受门槛；SC-39 优先使用 Flash 与独立提示词。学员版将补充规则合入正文和算法，不包含内部评审索引、变更过程、实施任务或失效评测前置条件。类型和代码保留设计骨架属性，辅助函数及完整框架装配仍待实现。

新增[精简版技术方案](../TECH_DESIGN.compact.html)，按当前技术方案与代码索引重新组织为目标量化表、核心图表、短代码与变更文件树。它使用项目 `my-design-to-html` Skill 和独立的 [review 编辑源](review/README.md)，原学员版 HTML 及其配置保留。

## 维护入口

| 文件          | 职责                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------ |
| `edition.mjs` | 从现有技术方案与代码设计提取章节、合并有效范围并适配第 11 课页面外壳；源章节缺失时停止生成 |
| `intro.md`    | 学员需求简述、技术目标、原则与架构引导                                                     |
| `diagrams.md` | 六张 Mermaid 图的编辑源：总体架构、数据与记录、组件 UML、类型关系、状态和时序              |

渲染、页面模板、样式和交互复用[第 11 课 document](../../../993-lesson-11-llm-runtime/design/document/README.md)的唯一维护源；本目录通过 edition 参数装配，无新增构建依赖。HTML 是派生产物，修改上述源文件后重新生成。

## 生成

在 Compass 根目录运行：

```bash
npm ci --prefix mai-specs/993-lesson-11-llm-runtime/design/document --ignore-scripts --no-audit --no-fund
node mai-specs/993-lesson-11-llm-runtime/design/document/render.mjs --edition mai-specs/992-lesson-12-multi-model-routing/design/document/edition.mjs
```

依赖沿用第 11 课锁文件中的 Mermaid、markdown-it 和 highlight.js，以及工程已有的 Playwright、Chromium 与 Prettier。浏览器缺失时从 `apps/web` 执行 `pnpm exec playwright install chromium`。本课的字典导出脚本和 routing 源码路径均为拟实施设计，文档构建无需它们存在。

生成的 `TECH_DESIGN.html` 内嵌图表 SVG、样式、代码高亮、搜索索引和交互脚本。直接双击可读；包含目录、搜索、类型跳转、代码展开/复制、图表缩放和 SVG 下载，适配窄屏与打印。

## 阅读检查

将 HTML 单独复制到空目录，在浏览器离线模式下检查六张图、完整代码、内部锚点与无外部请求；检查搜索、复制、展开、图表缩放和下载，以及 375 / 768 / 1440 宽度和横屏布局。临时检查脚本与截图放在忽略目录 `work/992/design-html/`。原技术方案、代码设计及第 11 课已生成文档保留原样。

2026-11-05 已完成独立 HTML 副本的离线 Chromium 检查：六张 SVG、十六个完整代码块（含图表源码）、内部锚点、唯一元素 ID、搜索与类型跳转、代码复制/展开、图表缩放/导出、Esc 关闭、上述屏幕尺寸、移动目录、减少动态效果和打印样式均通过；无页面脚本错误与外部网络请求。格式、受影响 README 链接、`git diff --check` 和 57 项 `pnpm test:tooling` 检查通过。本次仅验证文档及构建工具，模型接入与产品代码尚待实现。
