# 第 23 课技术方案阅读版

主方案、向量检索、代码实现、引用校验、零售数据五份 Markdown 维护设计正文，同目录同名 HTML 为离线阅读版。Chat 交互原型单独用于产品交互说明。

[生成入口](build.mjs)从 [主方案](../TECH_DESIGN.md)提取表格、Prompt、反馈字段和两组经营案例。[阅读布局](guide.md)组织主方案的七章；原文对照、报价切换与案例表保留在生成结果。[附录生成器](supplements.mjs)直接读取其余四份 Markdown。五页都使用[课程共用阅读器](../../../../scripts/course-design/reader.mjs)。视觉样板、浏览器检查与发布步骤见[技术方案 HTML 阅读规范](../../../../docs/ui-style-guide.md#技术方案-html-阅读版)。

从 Compass 根目录构建五份离线 HTML，检查桌面、390 px、目录、搜索、图表和报价切换。确认后执行第二条命令发布该课。

```sh
node mai-specs/973-lesson-23-agentic-search/design/document/build.mjs
node apps/home/scripts/lesson-resources.mjs --lesson 23
```

Home 的课程资源索引同时登记 HTML 和 Markdown。讲师回放、业务评分与普通 Chat 实跑详情见[本课验收记录](../../test/ACCEPTANCE.md)。
