# 第 15 课 · Agent 执行内核代码设计

[返回技术方案](TECH_DESIGN.md)

以下摘录围绕本课的请求内 ReAct、只读查询、停止与 SSE。组件之间的结构和状态所有权见主方案。

## ReAct 核心循环

位于 Agent 的 `harness/executor.py`。下面是课堂切片，省略观测、可选预算和阶段进度的装配。`RuntimeTurn` 把 `prepare`、`accept` 和 `dispatch` 传给 `execute_tools()`，后者构造 `handle` 并交给底层 `execute()`。

```python ReAct 循环 · 课堂示意
for index in range(1, RUN_STREAM_LIMITS["steps"] + 1):
    control.require_running()
    step = state.start_step(index)
    emit_step_started(step)

    prepared = prepare()
    result = await control.perform(
        lambda: invoke_model(step, prepared),
    )
    require_complete(result)

    candidate = await handle(step, result, prepared)
    finish_step(step, "completed")
    if candidate is not None:
        control.require_running()
        return candidate

raise RunFailure("step_limit")
```

`handle` 先让 `accept` 检查模型响应。需要调用工具时，`execute_tools()` 保存 assistant 消息和续接令牌，再调用 `dispatch`。`HarnessTools` 按原调用 ID 写回工具观察。通用查数提交命中已保存的 `resultId` 时直接返回候选回答。

## 通用数据库工具

`runtime/tool_bindings.py` 的 `HarnessTools` 分发工具，`tools/builtin/retail/actions.py` 的 `RetailActions` 处理查询和提交。下面摘录 `tools/builtin/database.py` 的 `_query_database_live()` 的 `query` 分支，省略列表、字段查询及异常映射。服务端持有连接绑定，SQL 解析先于建立连接，只读事务内按实际身份核对引用表权限。

```python database_query · query 分支摘录
query, relations = parse_read_sql(action.sql)

async def read(cursor):
    await cursor.execute("SET LOCAL search_path = pg_catalog, core")
    if relations:
        await cursor.execute(_ALLOWED_RELATIONS, (relations, relations))
        allowed = {name for row in await cursor.fetchall() for name in row if name}
        if not set(relations) <= allowed:
            raise SqlRejected()
    control.require_running()
    # 此处源码再次检查阶段截止时间。
    await cursor.execute(
        "SELECT pg_catalog.left(pg_catalog.row_to_json(result)::text, "
        f"{MAX_RESULT_BYTES + 1}) "
        f"FROM ({query}) AS result LIMIT {MAX_ROWS + 1}",
        values,
    )
    return await cursor.fetchall()

return _bounded_result(await read_retail(binding, control, read), offset)
```

`read_retail()` 管独占连接、只读事务、身份校验和清理。查询返回的 `DatabaseResult` 按 ID 保存到 `TaskState.database_results`。`submit_database_result` 用该 ID 找到本 Run 的结果，由服务端渲染表格。无效或未知引用回填安全错误。

## 停止裁决

`runtime/control.py` 中的 `commit()` 在活动阶段收口后同步运行，确保只登记一个终态。`RunControl.perform()` 控制模型调用和工具批次，批次内每次调用由 `tools/dispatcher.py` 的 `CallControl` 约束期限。停止入口返回停止回执，原 SSE 负责交付最终状态。

```python RunControl.commit · 源码
def commit(self, candidate: RunResult) -> RunResult:
    if self.terminal is not None or self.phase is not None:
        raise RuntimeError("run terminal committed out of order")
    self._mark_deadline()
    if not self.cleanup_confirmed:
        reason = self.stop_reason or "execution_error"
        candidate = RunCleanupUnconfirmed(
            status="cleanup_unconfirmed",
            reason=reason,
            cleanup="unconfirmed",
        )
    elif self.stop_reason == "deadline":
        candidate = RunFailed(status="failed", errorCode="run_timeout")
    elif self.stop_reason is not None:
        candidate = RunCanceled(status="canceled", reason=self.stop_reason, cleanup="confirmed")
    self.terminal = candidate
    self.registry.finish(self, candidate.status)
    self.finished.set()
    return candidate
```

## SSE 接入

Agent 由 `api/runs.py` 和 `runtime/stream.py` 维护首帧、响应生命周期和编码。Web 的 `server/analysis-agent-client.ts` 调用 `projectRunBody()` 检查、投影并重编号。浏览器的 `client/analysis-run.ts` 使用同一契约解码，等完整 EOF 后提交终态。参见[协议与完整响应](STREAM_PROTOCOL.md)。
