# 两级意图分流与模型路由

`POST /v1/analysis/runs` 在 Agent Runtime 内调用路由服务，执行 JEV → Flash 分类兜底、字典特征提取和选模。内部意图对象包含 `category_id`、`scene_id`。`run.routed` 公开实际模型、场景和工具启用状态，后续模型根据上下文决定是否调用工具。

JEV 和 Flash 只选择 `scene_id`。场景清单统一维护在 `prompts/support/routing/scenes.json`，包含 ID、路由属性、描述和识别示例。`dictionary.py` 从安装包中的同一份 JSON 读取代码控制的 `category_id` 和路由特征，从本次任务固定的发布版本读取描述与示例；后台修改固定属性会被拒绝。`config.py` 维护代码默认门槛/时限；`service.py` 完成模型选择与调用。`prompts.py` 的 `OFF_TOPIC_PROMPT` 专用于 SC-39 闲聊：固定调用 Flash 婉拒并引导业务数据任务，优先于用户业务主模型选择。

Provider 角色默认解析已启用的决策模型、`deepseek-flash` / `deepseek-v4-flash` 和 `deepseek-v4-pro`，多条时选最近更新项。可通过 `COMPASS_ROUTING_JEV_PROVIDER_ID`、`COMPASS_ROUTING_FLASH_PROVIDER_ID`、`COMPASS_ROUTING_PRO_PROVIDER_ID` 显式绑定；值为已有 Provider UUID，凭据仍在原存储。`COMPASS_ROUTING_MODE` 为 `jev_with_flash`（默认）或 `flash_only`。分类模型须启用，推理模型还须满足已有 Chat 准入和可见性。

本课不调优分类效果；默认概率0.5、间隔0.1用于演示流程。最多JEV一次、Flash分类一次、推理一次；180秒父预算下分类最多30秒（JEV8秒、Flash20秒）。失败无生成重试或容灾，Runtime可靠性留第14课。

场景JSON从课程需求工作簿的“场景目录”派生，不含题目金标。课程仓库内更新后运行：

```bash
uv run --locked python apps/agent/scripts/export_routing_dictionary.py
uv run --locked python apps/agent/scripts/export_routing_dictionary.py --check
```

脚本可用 `--source` 显式指定工作簿；学员分发包直接使用已交付JSON，无需安装表格处理库或访问需求目录。运行 `pnpm test -F agent` 验证流程；专用PostgreSQL与浏览器入口见应用README。

## 运行边界

路由只负责分类和模型选择，后续由同一个通用 Agent 循环调用工具。课程名称、固定日期和业务问题关键词均不触发专属执行路径。旧 workflows.py 的零售分流已移出生产；历史工作流资产管理独立保留。
