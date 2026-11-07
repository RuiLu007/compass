# 第 14 课技术方案

- [TECH_DESIGN.md](TECH_DESIGN.md)：技术设计真源。
- [CODE_DESIGN.md](CODE_DESIGN.md)：职责拆分与关键计算设计；头校验和统计计算继续由阅读配置引用。
- [TECH_DESIGN.html](TECH_DESIGN.html)：面向学员的单文件离线阅读版，内容均为设计示意。
- [document/guide.md](document/guide.md)：阅读版结构、表格、图表与文件变化清单。
- [document/edition.json](document/edition.json)：标题、设计指标及代码唯一块引用配置。

阅读版采用 `my-design-to-html` 的浅底绿色模板。技术决定在 TECH_DESIGN.md 维护；分布式关联与单一 Observation 按第 7 节的收敛设计呈现。阅读源中的简洁代码取自 [Observer](../../../apps/agent/src/compass_agent/observability/observer.py)、[安全字段](../../../apps/agent/src/compass_agent/observability/privacy.py)、[回调](../../../apps/agent/src/compass_agent/observability/litellm_receipt.py)与 [ModelService](../../../apps/agent/src/compass_agent/models/service.py)，用于展示关键分支。相关设计或方法变化时，先同步阅读源再重建 HTML；保留当前 Feature 阶段。

从 Compass 根目录执行：

```bash
node .agents/skill/my-design-to-html/scripts/render.mjs mai-specs/990-lesson-14-model-observability/design/document/edition.json
node .agents/skill/my-design-to-html/scripts/check-html.mjs mai-specs/990-lesson-14-model-observability/design/TECH_DESIGN.html --report-dir work/lesson-14-design/html-qa
```

检查报告与桌面 / 窄屏截图位于 `work/lesson-14-design/html-qa/`，属于临时检查产物。阅读行为检查覆盖离线打开、搜索、代码复制与展开、图表缩放与 SVG 导出、锚点、打印和不同宽度布局。应用接入、Docker 与供应商验证由 Feature 的实施材料维护。
