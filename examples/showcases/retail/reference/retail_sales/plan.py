"""Todo definitions and progress owned by one analysis Run."""

from typing import Annotated

from pydantic import Field, TypeAdapter

from compass_agent.schemas import RunPeriod, RunTodoId, RunTodoItem, RunUUID, ToolDefinition
from compass_agent.tools.builtin.todo_write.plan import (
    CompleteTodo as GenericCompleteTodo,
)
from compass_agent.tools.builtin.todo_write.plan import (
    ExecutionPlan as GenericExecutionPlan,
)
from compass_agent.tools.builtin.todo_write.plan import (
    ReplaceTodo as GenericReplaceTodo,
)
from compass_agent.tools.builtin.todo_write.plan import (
    SelectTodo,
    SkipTodo,
)
from compass_agent.tools.builtin.todo_write.plan import (
    TodoDefinition as GenericTodoDefinition,
)


class TodoDefinition(GenericTodoDefinition):
    # Preserve structured scope fields in plans already recorded by the Retail adapter.
    period: RunPeriod | None = None
    compare_to: RunPeriod | None = Field(default=None, alias="compareTo")
    channel_code: str | None = Field(
        default=None, alias="channelCode", pattern=r"^[A-Z0-9_]{1,24}$"
    )
    channel_from: RunTodoId | None = Field(default=None, alias="channelFrom")


class ReplaceTodo(GenericReplaceTodo[TodoDefinition]):
    pass


class CompleteTodo(GenericCompleteTodo):
    query_id: RunUUID | None = Field(default=None, alias="queryId")


TODO_ACTION = TypeAdapter(
    Annotated[ReplaceTodo | SelectTodo | CompleteTodo | SkipTodo, Field(discriminator="op")]
)


def todo_tool() -> ToolDefinition:
    from compass_agent.tools.builtin.todo_write import definition

    return definition(TODO_ACTION, "登记、选择、完成或跳过经营分析事项，提交当前 baseVersion。")


class ExecutionPlan(GenericExecutionPlan[TodoDefinition]):
    def projection(self) -> list[RunTodoItem]:
        return [
            RunTodoItem(
                id=item.id,
                goal=item.goal,
                status=self.status[item.id],
                dependsOn=item.depends_on,
                optional=item.optional,
                period=item.period,
                compareTo=item.compare_to,
                channelCode=item.channel_code,
                reason=self.skips.get(item.id),
            )
            for item in self.items.values()
        ]
