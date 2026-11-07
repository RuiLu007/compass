# 技术设计静态阅读版

本目录负责将 [V2 技术方案](../TECH_DESIGN.v2.md)与[最新实施记录](../../IMPLEMENTATION.md)生成 [HTML 阅读版](../TECH_DESIGN.v2.html)。Markdown 与应用源码继续作为设计、进度及类型的维护入口；HTML 是派生产物，修改内容后重新生成。

[V3 学员阅读版](../TECH_DESIGN.v3.html)独立组织产品需求、技术目标和完整设计，可作为单个 HTML 文件离线分发。它包含分层架构 SVG、字段附中文说明的完整 ER、UML、时序与类型，正文保留到 §2.4 完整类型与核心代码设计；需求依据、评审索引、风险评估、兼容性与部署、实施拆分与验证方法、历史迁移版本号及仓库文档链接均不进入 V3。V2 Markdown 与 HTML 保留。

[精简版技术方案](../TECH_DESIGN.compact.html)使用项目 `my-design-to-html` Skill 独立生成，保留目标量化表、核心图表、短代码与变更文件树。阅读编辑源及重建命令见 [review 说明](review/README.md)；V2、V3 及原有生成入口保留。

## 文件职责

| 文件                                      | 用途                                                                                   |
| ----------------------------------------- | -------------------------------------------------------------------------------------- |
| `render.mjs`                              | 解析完整 Markdown、Mermaid 预渲染、代码高亮、类型索引和离线 HTML 装配                  |
| `page.html`                               | 页面外壳、总览 SVG、阅读导航和对话框模板                                               |
| `styles.css`                              | Compass 文档样式、响应式和打印布局                                                     |
| `interactions.js`                         | 目录定位、检索、代码复制/展开、图表缩放与 SVG 导出                                     |
| `package.json` / `package-lock.json`      | 独立文档构建依赖，固定版本；不加入产品应用 workspace                                   |
| `learner-intro.md`                        | V3 的需求简述、O/KR 目标、原则与架构说明                                               |
| `learner-delivery.md`                     | 保留的部署与验证文稿，不参与 V3 生成                                                   |
| `learner-data.md`                         | V3 完整 ER 的 45 个字段中文说明、关系约束及设计理由；字段与 Agent ORM 对齐             |
| `layered-architecture.svg`                | 展示层、Web/BFF、Agent/Memory/Worker、数据与模型服务的分层协同图；区分已接入与规划关系 |
| `learner.mjs`                             | 从维护中的设计选取完整详细章节，调整学员版叙述与页面外壳；源章节变化时校验替换位置     |
| `learner.css` / `learner-interactions.js` | V3 实际界面截图布局与放大交互                                                          |

## 生成

从 Compass 根目录执行，首次安装后可直接运行第二条命令：

```bash
npm ci --prefix mai-specs/993-lesson-11-llm-runtime/design/document --ignore-scripts --no-audit --no-fund
node mai-specs/993-lesson-11-llm-runtime/design/document/render.mjs
```

生成 V3 时使用独立参数，仅写入 `TECH_DESIGN.v3.html`：

```bash
node mai-specs/993-lesson-11-llm-runtime/design/document/render.mjs --v3
```

其他课次可通过 `--edition <module>` 复用本目录的渲染器、页面外壳、样式与交互。edition 模块声明设计源、输出路径、学员内容与模板适配函数、图表标题与说明；图表数量按声明校验。第 12 课入口见 [Feature 992 构建说明](../../../992-lesson-12-multi-model-routing/design/document/README.md)。原 V2 / V3 命令保持原行为，edition 只写其声明的输出文件。

edition 可选 `overviewHeading` 指定首页概览跳转的标题前缀，默认 `需求简述`；纯设计阅读版可指定已有设计章节，如第 13 课的 `1.1`。`directoryHeading` 指定变更文件导航的标题前缀，默认 `2.3.7`；第 13 课使用 `2.3.4`。

V3 的 UML、时序、类型和模块设计由 V2 Markdown 派生；分层 SVG 与中文 ER 的维护入口见上表，学员版新增叙述维护在 `learner-*.md`，其余适配集中在 `learner.mjs`，不另存完整设计源副本。`spec/visuals/implemented-model-*.png` 为实际页面截图，构建时转为内嵌图片。最终 HTML 不需要这些源文件或任何服务。

目标表中的 50 并发、成功率及延迟数值属于待压测目标；更新为实测结论前须执行对应负载验证。页面将当前实现约束与待确认容量分开标明。

生成使用项目现有 `apps/web` 的 Playwright、Chromium 与根 Prettier；缺少浏览器时，从 `apps/web` 运行 `pnpm exec playwright install chromium`。仅构建时需要这些依赖，生成的 HTML 内嵌 SVG、样式、检索数据和交互脚本，没有 CDN、字体或外部网络依赖，可直接双击打开。

运行工具锁定 Mermaid 11.12.0（MIT）、markdown-it 14.1.0（MIT）与 highlight.js 11.11.1（BSD-3-Clause）；许可证随安装包保留。HTML 只携带渲染后的图形、标记及本项目脚本，不复制第三方运行库。

## 阅读与验证

- 六张 Mermaid 图、完整类型、代码块与设计表格从 Markdown 提取；V3 另内嵌一张分层 SVG 总览，共七张设计图。总览可放大和导出，窄屏支持在图表容器内横向滚动。
- UML、ER 与时序图可全屏查看，支持按钮缩放、原始尺寸、滚动浏览和 SVG 下载；Mermaid 源码保留可复制。
- 代码高亮在构建期完成；完整类型定义支持类型跳转、展开和复制。检索覆盖正文与类型，用 `⌘/Ctrl + K` 打开。
- 移动端使用折叠目录；表格与代码在各自容器内滚动，打印时展开代码。图表 SVG 可单独导出以保留大图细节。
- V3 正文、表格和代码使用 14px；辅助说明与次级导航使用 13px，标题按 30/24/19px 分级；移动端保持正文尺寸并调整布局。
- 页面头部的约束与验收摘要来自本次已确认结果；后续验收更新时，同步模板中的摘要。详细结果始终从实施记录读取。

浏览器截图和临时验收脚本保存在忽略目录 `work/993/design-html/`。提交文档时不包含 `node_modules/` 或临时测试输出。

2026-11-04 已完成离线 Chromium 验证：六张图表与十三个代码块均完整保留，内部锚点有效且无重复 ID；检索、复制、展开、图表缩放/导出、Esc 关闭和打印样式通过。375、768、1440 宽度及 812×375 横屏无页面横向溢出，减少动态效果偏好可用；无页面脚本错误和外部网络请求。结果见本机 `work/993/design-html/verification.json`。

V3 另在空目录中的独立 HTML 副本完成离线验证：两张内嵌截图及放大交互、全部目录锚点、完整类型与图表、搜索、复制、打印和上述屏幕尺寸通过；页面不含外部文件链接或网络请求。V2 Markdown/HTML 的 SHA-256 与改造前一致。本机验证脚本与截图位于 `work/993/design-html-v3/`。
