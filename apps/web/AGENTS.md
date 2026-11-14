<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Web UI 约束

- 改动页面、组件、代码展示或交互状态前，必须阅读并遵循 [Compass UI 样式规范](../../docs/ui-style-guide.md)。
- 复用 [设计变量](src/app/ui-system.css)和 [共用组件](src/components/ui)。新增通用变体时同步更新规范和产品内的「体验指南」。
- 在实际页面检查桌面、窄屏和键盘操作。对有请求的组件检查进行中、成功和失败，对代码展示检查长行、换行和复制。
