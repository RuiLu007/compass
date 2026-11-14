# 第 15 课首屏封面

`cover.png` 是已确认配色的课程封面，配置保存在 `edition.json`。图片在 HTML 中内嵌，离线阅读无需相邻资源。桌面显示完整封面，窄屏同时保留原文字概述。

替换 `cover.png` 后，在 Compass 根目录重建 HTML。构建会嵌入封面并生成流协议对照区。

```bash
node mai-specs/989-lesson-15-agent-execution-kernel/design/document/build.mjs
```
