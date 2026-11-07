"""Application-owned configuration snapshots; imports have no runtime side effects."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from compass_agent.llm.contracts import InvocationContext, ModelSpec
from compass_agent.llm.policy import CallPolicy


@dataclass(frozen=True, slots=True)
class PreparedCall:
    spec: ModelSpec
    context: InvocationContext
    policy: CallPolicy
    started_at: datetime
    deadline_at: datetime
    ciphertext: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RoutingGrant:
    """In-process classifier permission; never accepted from HTTP input."""

    actor_user_id: UUID
    scope: object
    call_id: UUID
    provider_id: UUID
    kind: Literal["generate", "decision"]
