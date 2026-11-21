"""Private, versioned data passed between collection, compilation and the execution journal."""

from dataclasses import dataclass, field
from typing import Literal

from compass_agent.context_runtime.profiles import Phase, Task
from compass_agent.schemas import ChatMessage, GenerateInput


@dataclass(frozen=True, slots=True)
class SourceRef:
    kind: str
    key: str
    revision: str


@dataclass(frozen=True, slots=True)
class AccessRef:
    tenant_id: str
    workspace_id: str
    binding: str
    revision: str


@dataclass(frozen=True, slots=True)
class ContextRequest:
    run_id: str
    step_key: str
    tenant_id: str
    workspace_id: str
    scope_profile: str
    task: Task
    scene: str
    phase: Phase
    generation: int
    binding_version: int


@dataclass(frozen=True, slots=True)
class ContextItem:
    item_id: str
    kind: str
    message: ChatMessage
    source: SourceRef
    group: str
    required: bool
    access: tuple[AccessRef, ...] = ()
    trust: Literal["managed", "actor", "observation", "dictionary"] = "actor"
    data_as_of: str | None = None
    byte_bound: int = 0


@dataclass(frozen=True, slots=True)
class Selection:
    item: str
    decision: Literal["selected", "omitted"]
    reason: str
    kind: str = "conversation"
    trust: str = "actor"
    source: SourceRef | None = None
    group: str = ""
    role: str = ""
    byte_bound: int = 0
    access: tuple[AccessRef, ...] = ()


@dataclass(frozen=True, slots=True)
class Compaction:
    group: str
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class PreparedContext:
    request: GenerateInput
    receipt: dict
    selections: tuple[Selection, ...] = field(default_factory=tuple)
    compactions: tuple[Compaction, ...] = field(default_factory=tuple)
