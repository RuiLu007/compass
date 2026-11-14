"""Run-local task state, independent of data sources and business goals."""

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    RunContractModel,
    RunTodoId,
)

type SkipReason = Literal["out_of_scope", "duplicate_optional"]
type TodoStatus = Literal["pending", "failed", "verified", "skipped"]


class TodoDefinition[Goal: str = str](RunContractModel):
    id: RunTodoId
    goal: Goal = Field(min_length=1, max_length=200)
    depends_on: list[RunTodoId] = Field(default_factory=list, alias="dependsOn", max_length=5)
    optional: bool = False


class TodoAction(RunContractModel):
    base_version: int = Field(
        alias="baseVersion", ge=0, le=RUN_STREAM_LIMITS["steps"] * RUN_STREAM_LIMITS["toolsPerStep"]
    )


class ReplaceTodo[T: TodoDefinition[Any]](TodoAction):
    op: Literal["replace"]
    items: list[T] = Field(min_length=1, max_length=6)
    select_todo_id: RunTodoId | None = Field(default=None, alias="selectTodoId")


class SelectTodo(TodoAction):
    op: Literal["select"]
    todo_id: RunTodoId = Field(alias="todoId")


class CompleteTodo(TodoAction):
    op: Literal["complete"]
    todo_id: RunTodoId = Field(alias="todoId")
    select_todo_id: RunTodoId | None = Field(default=None, alias="selectTodoId")


class SkipTodo(TodoAction):
    op: Literal["skip"]
    todo_id: RunTodoId = Field(alias="todoId")
    reason: SkipReason


TODO_ACTION = TypeAdapter(
    Annotated[
        ReplaceTodo[TodoDefinition] | SelectTodo | CompleteTodo | SkipTodo,
        Field(discriminator="op"),
    ]
)


class TodoRejected(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ExecutionPlan[T: TodoDefinition[Any]]:
    def __init__(self) -> None:
        self.version = 0
        self.first_step: int | None = None
        self.items: dict[str, T] = {}
        self.status: dict[str, TodoStatus] = {}
        self.skips: dict[str, SkipReason] = {}
        self.verified: dict[str, str] = {}
        self.retired: set[str] = set()
        self.selected: str | None = None
        self.active: str | None = None

    def replace(self, action: ReplaceTodo[T], index: int) -> None:
        proposed = {item.id: item for item in action.items}
        if (
            len(proposed) != len(action.items)
            or self.retired.intersection(proposed)
            or all(item.optional for item in proposed.values())
        ):
            raise TodoRejected("invalid_plan")
        for key, status in self.status.items():
            if status in {"verified", "skipped"} and proposed.get(key) != self.items[key]:
                raise TodoRejected("invalid_transition")
        for item in proposed.values():
            dependencies = set(item.depends_on)
            if (
                len(dependencies) != len(item.depends_on)
                or not dependencies.issubset(proposed)
                or dependencies.intersection(self.skips)
            ):
                raise TodoRejected("invalid_dependency")
        self.validate_targets(proposed)
        remaining = set(proposed)
        while remaining:
            ready = {
                key for key in remaining if not remaining.intersection(proposed[key].depends_on)
            }
            if not ready:
                raise TodoRejected("invalid_dependency")
            remaining.difference_update(ready)
        next_id = action.select_todo_id
        if next_id is not None:
            item = proposed.get(next_id)
            if item is None or next_id in self.verified or next_id in self.skips:
                raise TodoRejected("invalid_transition")
            self._check_dependencies(item)
        if list(self.items.values()) == action.items:
            if next_id is not None:
                self.selected = self.active = next_id
            return
        self.retired.update(set(self.items) - set(proposed))
        self.status = {
            key: self.status[key] if self.items.get(key) == item else "pending"
            for key, item in proposed.items()
        }
        self.items = proposed
        self.selected = self.active = next_id
        self.version += 1
        if self.first_step is None:
            self.first_step = index

    def actionable(self, key: str) -> T:
        item = self.items.get(key)
        if item is None or self.status[key] not in {"pending", "failed"}:
            raise TodoRejected("invalid_transition")
        return item

    def _check_dependencies(self, item: T, *, after: str | None = None) -> None:
        if any(
            dependency != after and dependency not in self.verified
            for dependency in item.depends_on
        ):
            raise TodoRejected("dependency_unmet")

    def ready(self, key: str, *, after: str | None = None) -> T:
        if key == after:
            raise TodoRejected("invalid_transition")
        item = self.actionable(key)
        self._check_dependencies(item, after=after)
        return item

    def validate_targets(self, proposed: dict[str, T]) -> None:
        """Business adapters may enforce additional target relationships."""

    def complete(self, action: CompleteTodo) -> None:
        item = self.ready(action.todo_id)
        next_id = action.select_todo_id
        if next_id is not None:
            self.ready(next_id, after=item.id)
        self.status[item.id] = "verified"
        self.verified[item.id] = ""
        if next_id is not None or self.selected == item.id:
            self.selected = next_id
        if next_id is not None or self.active == item.id:
            self.active = next_id

    def apply(self, action, index: int) -> None:
        if action.base_version != self.version:
            raise TodoRejected("stale_plan_version")
        if isinstance(action, ReplaceTodo):
            self.replace(action, index)
        elif isinstance(action, SelectTodo):
            self.ready(action.todo_id)
            self.selected = self.active = action.todo_id
        elif isinstance(action, SkipTodo):
            self.skip(action)
        else:
            self.complete(action)

    def skip(self, action: SkipTodo) -> None:
        item = self.actionable(action.todo_id)
        if not item.optional or any(item.id in other.depends_on for other in self.items.values()):
            raise TodoRejected("invalid_transition")
        self.status[item.id] = "skipped"
        self.skips[item.id] = action.reason
        if self.selected == item.id:
            self.selected = None
        if self.active == item.id:
            self.active = None

    def snapshot(self) -> dict:
        return {
            "version": self.version,
            "selectedTodoId": self.selected,
            "items": [
                {**item.model_dump(mode="json", by_alias=True), "status": self.status[item.id]}
                for item in self.items.values()
            ],
        }
