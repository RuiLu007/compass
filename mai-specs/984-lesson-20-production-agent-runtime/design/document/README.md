# 第 20 课首屏封面

`cover.png` 是已确认配色的课程封面，配置保存在 `edition.json`。图片在 HTML 中内嵌，离线阅读无需相邻资源。桌面显示完整封面，窄屏同时保留原文字概述。

正常重建 `edition.json` 时自动嵌入封面。仅更新封面、保留正文时，在项目根执行：

```bash
node .agents/skills/my-design-to-html/scripts/cover.mjs mai-specs/984-lesson-20-production-agent-runtime/design/document/edition.json
```
