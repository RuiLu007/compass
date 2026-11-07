# 两级意图分流与模型路由

统一 `POST /v1/models/calls` 内部执行 JEV → Flash 分类兜底、字典特征提取、契约选模和一次现有 Runtime 推理。意图对象只有 `category_id`、`scene_id`；`intent/features/routing/result/metrics` 分层返回，分类不产生 Skill、工具或多步骤计划。

`intent.py` 维护两题 JEV 与同结构 Flash 分类；`dictionary.py` 从只读 `scenes.json` 查特征；`config.py` 维护代码默认门槛/时限；`service.py` 完成模型选择与调用。`prompts.py` 的 `OFF_TOPIC_PROMPT` 专用于 SC-39 闲聊：固定调用 Flash 婉拒并引导业务数据任务，优先于用户业务主模型选择。

Provider 角色默认解析已启用的决策模型、`deepseek-flash` / `deepseek-v4-flash` 和 `deepseek-v4-pro`，多条时选最近更新项。可通过 `COMPASS_ROUTING_JEV_PROVIDER_ID`、`COMPASS_ROUTING_FLASH_PROVIDER_ID`、`COMPASS_ROUTING_PRO_PROVIDER_ID` 显式绑定；值为已有 Provider UUID，凭据仍在原存储。`COMPASS_ROUTING_MODE` 为 `jev_with_flash`（默认）或 `flash_only`。分类模型须启用，推理模型还须满足已有 Chat 准入和可见性。

本课不调优分类效果；默认概率0.5、间隔0.1用于演示流程。最多JEV一次、Flash分类一次、推理一次；180秒父预算下分类最多30秒（JEV8秒、Flash20秒）。失败无生成重试或容灾，Runtime可靠性留第14课。

场景JSON从课程需求工作簿的“场景目录”派生，不含题目金标。课程仓库内更新后运行：

```bash
uv run --locked python apps/agent/scripts/export_routing_dictionary.py
uv run --locked python apps/agent/scripts/export_routing_dictionary.py --check
```

脚本可用 `--source` 显式指定工作簿；学员分发包直接使用已交付JSON，无需安装表格处理库或访问需求目录。运行 `pnpm test -F agent` 验证流程；专用PostgreSQL与浏览器入口见应用README。
