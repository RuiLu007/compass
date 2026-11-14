"""Generic task planning tool; domain adapters can supply a stricter action schema."""

from typing import Any

from pydantic import TypeAdapter

from compass_agent.schemas import ToolDefinition, ToolFunction
from compass_agent.tools.builtin.todo_write.plan import (
    TODO_ACTION,
    ExecutionPlan,
    TodoDefinition,
    TodoRejected,
)
from compass_agent.tools.catalog_contracts import ToolSpec
from compass_agent.tools.contracts import ExecutionReceipt


def definition(
    adapter: TypeAdapter[Any] = TODO_ACTION,
    description="登记、选择、完成或跳过任务事项，提交当前 baseVersion。",
):
    return ToolDefinition(
        function=ToolFunction(
            name="todo_write",
            description=description,
            parameters={"type": "object", **adapter.json_schema(by_alias=True)},
        )
    )


async def execute(plan: ExecutionPlan[TodoDefinition], args, context, index: int):
    action = TODO_ACTION.validate_python(args)
    try:
        plan.apply(action, index)
    except TodoRejected as error:
        return ExecutionReceipt(
            context.call_id, "error", "confirmed", {"status": "error", "code": error.code}
        )
    return ExecutionReceipt(context.call_id, "ok", "confirmed", {"status": "ok", **plan.snapshot()})


SPEC = ToolSpec("todo_write", "builtin", "todo", lambda context: definition(), lambda context: True)
