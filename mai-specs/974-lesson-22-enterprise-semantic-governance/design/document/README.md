# 重建 Compass Retail 语义层阅读版

[阅读稿](retail-assets.md) 组织整体架构、流程、ER、工具契约、六层详设和文件树。[版本配置](retail-assets.json) 指向[默认资产设计](../RETAIL_SEMANTIC_ASSETS.md)中的安装与核对 SQL。[生成脚本](render-retail.mjs) 从[种子包](../../../../apps/web/db/main/seeds/retail-v1/)读取实体、字段、关联与术语，使用项目内的 HTML 渲染器嵌入图形和检索数据，生成[单文件 HTML](../RETAIL_SEMANTIC_ASSETS.html)。

从 Compass 根目录执行以下命令。首次配置渲染环境时按[生成工具说明](../../../../.agents/skills/my-design-to-html/references/generation.md)安装固定依赖与 Chromium。

```bash
node mai-specs/974-lesson-22-enterprise-semantic-governance/design/document/render-retail.mjs
node .agents/skills/my-design-to-html/scripts/check-html.mjs \
  mai-specs/974-lesson-22-enterprise-semantic-governance/design/RETAIL_SEMANTIC_ASSETS.html \
  --report-dir work/974-semantic-assets-reading
```
