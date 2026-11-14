# 零售教学参考

- 负责人：andy。
- 教学目标：解释固定参数 SQL、Skill 脚本和业务编排的开发方式。
- 依赖：Python 3.13；脚本示例只使用标准库。
- 必要性：用于阅读与独立演示开发过程；普通渠道分析和周报使用产品数据库工具与 Python。
- 退出条件：课程示例替换或不再讲解固定业务编排时删除；正式业务工具另行提交通用化设计和验收。

`reference/` 为退出生产的源码与旧测试参考，引用的是退出时的接口，保留用于阅读。产品和自动测试不加载它。`skills/weekly-retail-review/` 为独立计算脚本示例。

独立脚本示例：从仓库根运行 `python examples/showcases/retail/runner.py --check` 检查目录；通过 `--baseline` 和 `--current` 传入包含 start、end、covered、rows 的 JSON 对象执行周汇总。日期由输入决定。输出是脚本计算结果，和产品 Agent 的回答独立。
