# Feature 992 技术设计

[精简版技术方案](TECH_DESIGN.compact.html)按当前方案与代码索引整理，使用第 13 课阅读风格，聚焦目标量化、分类与选模图表、响应契约、关键代码和变更文件。独立编辑与生成入口见 [review 说明](document/review/README.md)，原 HTML 保留。

[技术方案](TECH_DESIGN.md)维护两级意图识别、字典特征提取、契约选模和现有LLM Runtime一次推理。[代码设计](CODE_DESIGN.md)展开分类兜底、查表、选模与响应。前端统一复用现有models/calls，分类与选模在服务端内部完成；路由策略直接维护于代码配置，试算和实验使用本地脚本/测试函数。完整Harness留到后续章节，Runtime可靠性留到第14课。需求与验收统一维护于[PRD](../spec/PRD.md)，进度见[需求简报](../BRIEF.md)。

[学员版技术方案](TECH_DESIGN.html)沿用第 11 课 V3 的结构和离线阅读交互，整合当前范围补充，包含六张设计图及核心类型与算法。HTML 为派生产物，维护与生成见 [document 说明](document/README.md)。当前保持设计待实现状态。

## 意图分类体系补充

[意图分类体系定义](INTENT_TAXONOMY.html)为已审阅的独立阅读版，结合场景工作簿和当前 PRD 展开 40 个场景与特征设计。编辑、生成与范围差异说明见 [intent README](document/intent/README.md)。本稿已登记 home 第 12 课，Feature 实施阶段保持不变。
