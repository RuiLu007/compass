# 生成配置与命令

## 编辑源

通常在技术方案同目录的 `document/` 中保存 `guide.md` 与 `edition.json`。配置内所有文件路径相对 `edition.json` 解析，复制到独立项目后保持相对关系即可。

```json
{
  "title": "报表导出技术方案",
  "subtitle": "分批读取，按字段契约生成文件。",
  "brand": "Compass",
  "label": "人工阅读版 · 设计示意",
  "topics": ["批量读取", "导出契约", "结果文件"],
  "source": "../TECH_DESIGN.md",
  "guide": "guide.md",
  "output": "../TECH_DESIGN.html",
  "code": {
    "export": {
      "file": "../CODE_DESIGN.md",
      "contains": "async def export_report",
      "language": "python"
    }
  }
}
```

| 配置                                   | 约定                                                                                                    |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `title`、`source`、`guide`、`output`   | 必填；`source` 是有效技术方案，输出须为 `.html` 且不得覆盖输入文件                                      |
| `subtitle`、`brand`、`label`、`topics` | 可选；短标题、项目名、版本性质、少量主题词                                                              |
| `hero`                                 | 可选，相对路径的本地 SVG；构建时内嵌，须有有效 `viewBox`                                                |
| `metrics`                              | 可选数组，每项 `{ "value": "500", "label": "行 / 批", "detail": "单次数据库读取" }`；只填写源方案已有值 |
| `code`                                 | 可选映射；`file` 指向代码附录 Markdown，`contains` 必须唯一命中一个代码块，可按 `language` 缩小范围     |

在 guide 中以独立一行 `{{code:export}}` 插入对应完整代码块。更新原附录后重建；选择器无匹配或多匹配时构建报错，先核对源变化。

普通代码使用 fenced block，可在语言后附标签，如 `python exporter.py`、`text 变更文件`。图表使用 `mermaid 图表标题`；自绘 SVG 使用 `diagram-svg 图表标题`，内容为完整 SVG。图表省略标签时采用最近的章节标题。

Markdown 原始 HTML 不执行，图形统一经过图表入口。SVG 支持本地路径、文字、形状、marker 和内部引用；外部资源、脚本与事件处理器会被拒绝。阅读文稿不放依赖旁边文件的图片或链接；源文档链接留在维护 README。

## 安装与生成

下列 `<Skill目录>` 使用实际安装目录。Compass 中为 `.agents/skill/my-design-to-html`，发现入口 `.agents/skills/my-design-to-html` 指向同一目录。

```bash
npm ci --prefix <Skill目录> --ignore-scripts --no-audit --no-fund
npm exec --prefix <Skill目录> -- playwright install chromium
node <Skill目录>/scripts/render.mjs path/to/document/edition.json
```

依赖与浏览器已存在时直接生成。Skill 的固定依赖是 Markdown-it、Mermaid、highlight.js 与 Playwright；只在构建和检查时使用，最终 HTML 不加载这些库。无需启动项目应用、数据库或外部服务。

可用 `--output` 把试生成结果放入临时目录：

```bash
node <Skill目录>/scripts/render.mjs path/to/edition.json --output work/design-preview/review.html
node <Skill目录>/scripts/check-html.mjs work/design-preview/review.html --report-dir work/design-preview/qa
```

检查脚本会复制 HTML 到独立目录、禁用浏览器网络、检查页面交互并截图。检查得到的通过结果仅覆盖文档行为；内容正确性按源方案逐项核对。失败时修复编辑源或模板并重新生成。

## 维护

页面外壳在 `assets/page.html`，配色布局在 `assets/reading.css`，交互在 `assets/interactions.js`。模板和渲染器随 Skill 版本维护，派生 HTML 不手工修补。新增生成目录时，在最近 README 记录源路径与实际重建命令。

检查 Skill：

```bash
npm test --prefix <Skill目录>
```

测试临时输出位于系统临时目录，不写入教材或 Feature。要检查具体方案，请使用上面的 `--report-dir` 保存本次截图。
