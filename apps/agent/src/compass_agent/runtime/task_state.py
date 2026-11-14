"""Run-local orchestration state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from compass_agent.harness.contracts import AgentState

if TYPE_CHECKING:
    from compass_agent.runtime.tool_bindings import RunToolBindings


@dataclass(slots=True)
class TaskState(AgentState):
    tools: RunToolBindings | None = field(default=None, init=False, repr=False)
