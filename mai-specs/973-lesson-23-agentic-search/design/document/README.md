# 第 23 课技术方案阅读版

2026-11-22 版描述已接入的默认通用 Loop、工具层权限、通用 Verify 和普通／循证回答交付。主方案、回答契约、代码设计维护新协议；知识库与课堂专题保留既有能力和历史验收，并标明需要重新验证的部分。Chat 交互原型保留为旧版参考。

[主方案](../TECH_DESIGN.md)、[回答契约](../EVIDENCE.md)、[代码设计](../CODE_DESIGN.md)、[知识库专题](../VECTOR_SEARCH.md)、[课堂专题](../RETAIL_CASE.md)是设计源，同目录同名 HTML 为派生阅读版。[guide.md](guide.md)组织主方案八章，图与代码通过 [edition.json](edition.json)引用唯一源块。

本版主方案直接使用[课程共用阅读器](../../../../scripts/course-design/reader.mjs)。原 [build.mjs](build.mjs)依赖旧章节和自定义占位符，保留为旧版生成记录，本版不运行它。四份专题继续使用已有 [supplements.mjs](supplements.mjs)，其通用正文转换读取最新 Markdown。主方案第七章展示前端推理协议、完整交付帧和引用用例，第八章按 App 用深色代码块展示文件改动树。知识库专题采用目标、架构、ER、流程、API、维护与代码改动的顺序。附录生成器支持传入专题名，仅刷新本次修改的专题。

从 Compass 根目录运行：

```sh
node scripts/course-design/reader.mjs mai-specs/973-lesson-23-agentic-search/design/document/edition.json
node --input-type=module -e 'import { buildSupplements } from "./mai-specs/973-lesson-23-agentic-search/design/document/supplements.mjs"; await buildSupplements(process.cwd() + "/mai-specs/973-lesson-23-agentic-search/design");'
pnpm exec prettier --ignore-path /dev/null --write mai-specs/973-lesson-23-agentic-search/design/{TECH_DESIGN,EVIDENCE,CODE_DESIGN,VECTOR_SEARCH,RETAIL_CASE}.html
```

离线校验使用 `.agents/skills/my-design-to-html/scripts/check-html.mjs` 的临时副本。当前课程外壳有两个检索按钮，原检查器的 `[data-search]` 严格定位会冲突；本轮只在 work 副本中改为 `[data-search]:visible` 的首个匹配，并增加 390 px 与键盘目录／搜索检查。原检查器代码保持原状，复核时沿同样方式适配定位。方案阶段的五页报告保存在 `work/lesson23-design-revision/`；实现后主方案和知识库的复验保存在 `work/lesson23-runtime/html-main-final/` 与 `html-knowledge-final/`。检查离线资源、锚点、代码复制／展开、图表缩放／导出、搜索和打印，并补充桌面、390 px、键盘目录与搜索检查。视觉基准和分发约定见[技术方案 HTML 阅读规范](../../../../docs/ui-style-guide.md#技术方案-html-阅读版)。检查材料写入 work，不作为新运行协议的验收结果。

Home 第 23 课仅分发主方案和知识库方案，均提供 HTML 与 Markdown；其余专题保留在 Feature。知识库专题单独更新时运行：

```sh
node --input-type=module -e 'import { buildSupplements } from "./mai-specs/973-lesson-23-agentic-search/design/document/supplements.mjs"; await buildSupplements(process.cwd() + "/mai-specs/973-lesson-23-agentic-search/design", ["VECTOR_SEARCH"]);'
pnpm exec prettier --ignore-path /dev/null --write mai-specs/973-lesson-23-agentic-search/design/VECTOR_SEARCH.html
node apps/home/scripts/lesson-resources.mjs --lesson 23
```

本轮实现与验证记录分别在 dev/IMPLEMENTATION.md 和 test/ACCEPTANCE.md。Home 继续只保留主方案和知识库方案。
