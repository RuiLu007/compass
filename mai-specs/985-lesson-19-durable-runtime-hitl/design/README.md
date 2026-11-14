# 第 19 课技术方案审阅版

[TECH_DESIGN.html](TECH_DESIGN.html) 展示 Redis 短期流、SQL 完整投影与 Message / Run 双接口共享响应；[HITL_DESIGN.html](HITL_DESIGN.html) 展示人工决定增量。

维护源为 [TECH_DESIGN.md](TECH_DESIGN.md)、[DATA_DESIGN.md](DATA_DESIGN.md)、[CODE_DESIGN.md](CODE_DESIGN.md)、[REDIS_DESIGN.md](REDIS_DESIGN.md) 和 [HITL_DESIGN.md](HITL_DESIGN.md)。按项目 `my-render-design-html` 技能由这些源文件刷新同名 HTML；数据字段、响应类型和关键代码随源同步。

页面样式对齐[第 14 课审阅版](../../990-lesson-14-model-observability/design/TECH_DESIGN.html)：暖白底色、深绿代码面板、固定顶栏、文档检索、图表放大与代码复制/展开。后续重新生成时保留这套阅读样式。

生成阶段使用技能模板固定版本的 Mermaid 与 Highlight.js；交付 HTML 内嵌 SVG、代码高亮和阅读交互，可直接离线打开。检查桌面/窄屏、键盘、检索、代码操作、图表缩放及页内导航；系统切换深色时仍采用第 14 课的浅色阅读配色。

[document/guide.md](document/guide.md) 与 [document/edition.json](document/edition.json) 为早期排版资产，保留作历史资料。其旧读取协议与生成配置不作为当前合同或重建入口。
