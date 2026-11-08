# 第 14 课技术方案

- [TECH_DESIGN.md](TECH_DESIGN.md)：技术设计真源。
- [CODE_DESIGN.md](CODE_DESIGN.md)：职责拆分与关键计算设计。响应头示例由阅读配置引用。
- [TECH_DESIGN.html](TECH_DESIGN.html)：面向学员的单文件离线阅读版，包含架构图和关键源码摘录。
- [document/guide.md](document/guide.md)：阅读版结构、术语图、埋点地图、代码摘录与文件变化清单。
- [document/edition.json](document/edition.json)：标题、设计指标及代码唯一块引用配置。

阅读版采用 `my-design-to-html` 的浅底绿色模板。第 2 章的埋点地图按当前 Agent 的 Span 名称、实际父子关系与导出方式编排。代码块标题标明源模块和对应职责，模块片段保留原分支并去除外层缩进。更新设计或实现后，按代码块标题同步阅读源再重建 HTML。主要入口见 [Observer](../../../apps/agent/src/compass_agent/observability/observer.py)、[导出 Pipeline](../../../apps/agent/src/compass_agent/observability/export.py)、[Agent HTTP](../../../apps/agent/src/compass_agent/model_api.py)、[执行器](../../../apps/agent/src/compass_agent/llm/executor.py)和 [Langfuse Compose](../../../deploy/compose/observability.yaml)。

术语图和数据样例按 OTel 官方概念及 OTLP 结构组织，Langfuse 图按本项目固定版本的部署配置绘制。[OTel Trace](https://opentelemetry.io/docs/concepts/signals/traces/) · [OTLP JSON 编码](https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding) · [Span 协议字段](https://github.com/open-telemetry/opentelemetry-proto/blob/main/opentelemetry/proto/trace/v1/trace.proto) · [OTel Metrics API](https://opentelemetry.io/docs/specs/otel/metrics/api/) · [Langfuse 应用容器](https://langfuse.com/self-hosting/deployment/infrastructure/containers)

从 Compass 根目录执行：

```bash
node .agents/skill/my-design-to-html/scripts/render.mjs mai-specs/990-lesson-14-model-observability/design/document/edition.json
node .agents/skill/my-design-to-html/scripts/check-html.mjs mai-specs/990-lesson-14-model-observability/design/TECH_DESIGN.html --report-dir work/lesson-14-design/refresh/html-qa
```

检查报告与桌面 / 窄屏截图位于 `work/lesson-14-design/refresh/html-qa/`。阅读行为检查覆盖离线打开、搜索、代码复制与展开、图表缩放与 SVG 导出、锚点、打印和不同宽度布局。应用接入、Docker 与供应商验证由 Feature 的实施材料维护。
