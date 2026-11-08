# 第 12 课精简技术方案阅读版

本目录使用本地安装的 `my-design-to-html` Skill 生成独立的[精简版 HTML](../../TECH_DESIGN.compact.html)。原 `TECH_DESIGN.html` 和 `document/edition.mjs` 保留。

内容依据当前[技术方案](../../TECH_DESIGN.md)及[代码设计索引](../../CODE_DESIGN.md)，聚焦两级分类、字典特征、选模顺序、统一响应与关键代码。生产类型和算法以索引链接的应用源码为维护入口；本目录的短摘录用于集中阅读，更新时重新核对对应符号。

| 文件 / 内容                 | 职责与来源                                                                                                                                                 |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `guide.md`                  | 目标量化表、关系与流程图、接口、文件树和短代码摘录                                                                                                         |
| 类型摘录                    | [schemas.py](../../../../../apps/agent/src/compass_agent/schemas.py)：`Intent`、`RoutingFeatures`                                                          |
| 分类摘录                    | [intent.py](../../../../../apps/agent/src/compass_agent/routing/intent.py)：`from_jev`                                                                     |
| 字典摘录                    | [dictionary.py](../../../../../apps/agent/src/compass_agent/routing/dictionary.py)：`validate_intent`、`features_for`                                      |
| 选模摘录                    | [service.py](../../../../../apps/agent/src/compass_agent/routing/service.py)：`RoutingService.run` 中从 `selection` 到 `reason` 的分支，统一移除方法体缩进 |
| 配置摘录                    | [config.py](../../../../../apps/agent/src/compass_agent/routing/config.py)：`RoutingPolicy`                                                                |
| `edition.json` / `hero.svg` | 生成配置与首屏四阶段总览                                                                                                                                   |

在 Compass 根目录执行：

```bash
node .agents/skills/my-design-to-html/scripts/render.mjs mai-specs/992-lesson-12-multi-model-routing/design/document/review/edition.json
pnpm exec prettier --write --ignore-path /dev/null mai-specs/992-lesson-12-multi-model-routing/design/TECH_DESIGN.compact.html
node .agents/skills/my-design-to-html/scripts/check-html.mjs mai-specs/992-lesson-12-multi-model-routing/design/TECH_DESIGN.compact.html --report-dir work/design-to-html/compact-11-12/12
```

构建依赖与通用检查由 Skill 维护。截图与临时结果保存在 `work/`；最终 HTML 内嵌图表、样式与交互，可以单文件离线阅读。
