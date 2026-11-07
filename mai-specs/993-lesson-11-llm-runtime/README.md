# 第 11 课 LLM Runtime

《企业级Agent工程架构实战营》｜Feature 993

## 学员资料包

[下载第 11 课学习资料](lesson-11-learning-materials.zip)。解压后包含：

- `PRD.md`：整合当前有效接入范围、功能、交互、权限与验收标准。
- `TECH_DESIGN.md`：与学员 HTML 阅读版对应的完整 Markdown 技术方案。
- `TECH_DESIGN.html`：可直接双击打开的独立离线技术方案。
- `visuals/`：Markdown 引用的图片与架构图。
- `README.md`：阅读说明。

压缩包仅收录本次分发文档及阅读所需图片。HTML 内嵌所有资源，可独立分享。

## 维护入口

需求继续维护在 [PRD](spec/PRD.md)，完整设计维护在 [V2 技术方案](design/TECH_DESIGN.v2.md)。学员版内容组织与 HTML 生成见[阅读版构建说明](design/document/README.md)，Feature 范围和实施状态见 [BRIEF](BRIEF.md)。

学员 Markdown 由现有 `createLearnerDocument` 组合生成，图片路径改为包内路径，分层架构占位符替换为 SVG 引用；HTML 使用现有 V3 学员阅读版。分发 PRD 保留需求与验收编号，整合有效范围，省去内部来源索引、待办与变更历史。资料包为派生分发文件，更新内容时修改上述维护入口后重新生成。
