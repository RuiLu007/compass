# 临时 Showcase

课程案例默认提供普通问题、样本和验收点，由产品 Agent 使用通用数据库查询和代码执行能力完成。这里仅保存演示工具、Skill 或编排机制本身所需的独立材料。

每个案例按需创建 `README.md`、`prompts.md`、`tools/<tool_name>/`、`skills/<skill_name>/`、`runner.py`、`tests/`。README 必须记录教学目标、负责人、依赖、临时扩展的必要性及退出条件。临时工具使用标准 ToolSpec / Registry / Dispatcher，由独立入口显式注册。

产品 Runtime、Harness、请求 Schema、默认工具清单和组合根不导入本目录。此目录不进入 Agent wheel 或生产镜像。普通 Chat 不携带案例执行标记。

演示结束或被通用能力覆盖时删除；需要进入产品时按正常 Feature 评审通用接口及跨场景行为。目录与注册规则由 `apps/agent/tests/test_showcase_isolation.py` 和构建产物检查验证。

[第 23 课跨源经营诊断](lesson-23-agentic-search/README.md)提供冻结数据、工作区资料、正式口径发布与 Chat 业务验收入口。
