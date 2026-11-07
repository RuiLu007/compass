# 第 13 课学员版技术方案

本目录将[技术方案](../TECH_DESIGN.md)及[代码设计](../CODE_DESIGN.md)组织为[单文件 HTML](../TECH_DESIGN.html)。页面沿用第 12 课的视觉样式、代码交互及图表放大；内容按 2026-11-05 的用户要求，仅用设计表格、Mermaid/SVG、代码和代码块文件清单展开。需求依据、验收、评审索引、部署、验证和兼容章节不进入学员版。

## 编辑入口

| 文件          | 职责                                                                                |
| ------------- | ----------------------------------------------------------------------------------- |
| `edition.mjs` | 引用维护方案，直接提取 6 个核心代码块，装配类型示意与页面；关键源结构变化时停止生成 |
| `guide.md`    | 学员版章节、表格、响应节选与文件变更说明；通过标记引用代码和图表                    |
| `diagrams.md` | 8 张 Mermaid 图的编辑源                                                             |
| `hero.svg`    | 首屏 Logical Call / Attempt 关系图                                                  |
| `reading.css` | 学员版单栏阅读布局，复用共享颜色、字体和交互样式                                    |

技术决策继续维护于上层 Markdown。这里的表格与类型是阅读适配；方案更新时同步对应适配内容并重新生成。核心执行算法直接抽取，避免维护第二份算法副本。

## 生成

复用[第 11 课 document](../../../993-lesson-11-llm-runtime/design/document/README.md)的渲染器、固定版本依赖、模板及交互；第 12 课也使用相同入口。本次给共享渲染器增加可选 `overviewHeading` 与 `directoryHeading`，用于纯设计版本的首页与变更文件跳转，旧版本默认行为保持。

从 Compass 根目录执行：

```bash
node mai-specs/993-lesson-11-llm-runtime/design/document/render.mjs --edition mai-specs/991-lesson-13-reliable-model-execution/design/document/edition.mjs
```

依赖缺失时，按共享 document 的锁文件安装；产物内嵌 SVG、样式、代码及交互，不加载 CDN 或外部字体。图表源可展开查看，SVG 可下载，代码可复制及展开；不需要运行 Compass 应用。

页面检查使用工程已有 Playwright，在 375、768、1440 宽度与横屏下检查离线渲染、图表、锚点、代码操作及打印。检查脚本、截图及临时独立副本保存在 `work/991/design-html/`，不提交到学员资料。
