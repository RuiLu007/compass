# 意图分类体系阅读版

本目录维护第 12 课独立的[意图分类体系定义 HTML](../../INTENT_TAXONOMY.html)，已通过用户审阅。内容覆盖一级分类、40 个场景的输入与产出、字典特征、分类 Prompt、选模契约与素材收敛关系。

- [guide.md](guide.md)：阅读编辑源；维护场景定义、复杂度说明及 JEV / Flash 分类输入模板。
- [edition.json](edition.json)：生成配置。
- [场景工作簿](../../../spec/task-scenarios.xlsx)：场景、问题与标注素材真源。
- [PRD](../../../spec/PRD.md)：当前分类契约和范围真源。
- [运行时字典](../../../../../apps/agent/src/compass_agent/routing/scenes.json)：用于核对现有归属、四项特征和响应方式。

工作簿保留旧宽分类、拆分和动态降级字段；阅读版按当前 PRD 整理，不修改工作簿、运行时配置或既有技术方案。已登记 home 第 12 课，与技术方案并列；分发时统一隐藏顶部栏。

从 Compass 根目录生成和检查：

```bash
node mai-specs/992-lesson-12-multi-model-routing/design/document/intent/render.mjs
node .agents/skills/my-design-to-html/scripts/check-html.mjs mai-specs/992-lesson-12-multi-model-routing/design/INTENT_TAXONOMY.html --report-dir work/992-intent/qa
```

HTML 为派生产物；修改 guide 后重新生成。检查截图与临时数据放在 `work/992-intent/`。

分类目录统一为一张 40 行场景表，支持业务域、复杂度与关键词组合筛选、计数、空态和重置。`render.mjs` 复用通用阅读版渲染器并内嵌 `filters.js` / `filters.css`，保留离线交互；打印呈现完整场景表。修改内容或交互后使用本目录渲染入口重建。

场景总表使用五列：一级分类、二级场景、任务类型、复杂度、任务特征描述。特征描述以一两句话解释级别依据及 AI 执行难点，替代原岗位、输入与响应目标三列；场景 ID 和默认级别保持原值。

第四部分展示 `apps/agent/src/compass_agent/routing/intent.py` 中的 JEV 两题指令与 Flash System 指令，动态候选目录和会话消息以占位符呈现。分类器指令变更时同步模板并重建；3.3、3.4 两节已移除。
