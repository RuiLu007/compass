# Feature 972 代码复评

复评日期：2026-11-23。结论：代码复评通过，本轮无新增 P0 / P1。完整真实模型端到端验收由主代理继续完成。

增量复评覆盖 `context_runtime/optimizer.py` 的 `plain_tool_prefix` 状态与冻结帧恢复，以及 `model_step.py`、`classifier.py` 在 accepted replay 时的 epoch 授权。沿调用路径核对 `llm/continuation.py`、`source_access.py` 和 Journal 的归属校验。仅修改本评审记录，保留其他工作区改动。

## 本轮结论

- 普通模式中存在工具消息时才设置 `plainToolPrefix`；后续请求同时维持 `thinking=False` 与 `reasoning_effort=None`。冻结帧包含该状态并参与摘要校验，恢复时重新装载，旧检查点缺省为 `False`。
- 未改写消息前缀时保留原生 continuation。已有原生 thinking 状态、且未设置普通模式标记的请求继续使用 thinking；普通模式产生的 continuation 在后续请求保持普通模式，避免重新启用 thinking 导致协议失败。
- 当前范围保持在单个 Run 的主执行路径。新 Run 创建独立优化器，子 Agent 和验证调用沿用现有入口。暂不需要增加供应商分支或阶段重置规则；当前最新工具批仍保留在前缀中，按 Run 保持模式符合设计取舍。
- accepted replay 与新调用统一向 `require_run_sources / require_search_sources` 传入 `journal.epoch`，授权语义正确。Journal 在读取已接受结果前仍校验当前执行归属；同 epoch 的 transient 来源可供本次执行继续使用，其他 epoch 以及缺少 epoch 的历史读取继续被拒绝。Search 重建缓存的签名包含 transient 的 epoch 范围，改变 epoch 会重新校验。`fresh` 仅保留观测用途，其他来源的权限与版本复核保持原逻辑。

## 已关闭发现

- P1：历史来源失效后，独立保留已接受的 root physical 执行状态，并移除原问题、参数和工具输出；来源失效不再遮蔽部分成功动作。
- P1：摘要回填按实际序列化帧容量与消息数复查，超限时逐个移除最早完整轮，支持零轮回填。
- 工具结果授权依赖复核通过：生产者的 `source_deps` 经持久化和重放保留；数据库提供显式 witness，通用 MCP 文件继续采用 transient 限制。
- 同 epoch 内已接受模型步骤的重放拒绝问题已关闭：本 Run 追加 transient 来源后，恢复旧步骤继续使用当前有效 epoch，不再因 `fresh=False` 丢失执行期授权。

## 本次验证

- 独立执行 `uv run --package compass-agent pytest apps/agent/tests/test_context_optimization.py -q`：15 passed，包含普通工具前缀的 continuation 与恢复回归。
- 使用实际 `llm.continuation.seal / restore` 和内存 codec 运行两个独立探针：原生 thinking continuation 保持推理状态；已锁定的普通模式 continuation 保持普通模式。两项均通过。
- 前轮 PostgreSQL 集成结果由主代理回传：3 passed，覆盖 transient 来源和部分动作状态；本轮未重复执行数据库测试。
- 独立执行 transient 来源归属测试与 Search 重建授权测试：2 passed。额外调用实际 `require_search_sources` 验证缓存路径：同 epoch 连续读取成功，切换到其他 epoch 或缺少 epoch 均返回 `source_access_denied`。
- 主代理回传新增 `test_context_runtime_integration.py` 的 `transient_replay` PostgreSQL 场景：1 passed。已核对测试经过实际 Runtime、工具来源登记、结果交付及同 epoch 重放，并断言重放结果一致且模型调用数不增加。

剩余验收项为主代理已安排的真实 DeepSeek 连续工具调用及完整选择、压缩流程，完成后更新实施验收记录。
