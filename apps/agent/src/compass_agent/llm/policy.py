"""Fixed single-attempt policy; durations use the event loop monotonic clock."""

import math
from dataclasses import dataclass, field, fields
from typing import Literal

from compass_agent.schemas import RUN_CONTEXT_MESSAGES


@dataclass(frozen=True, slots=True)
class OutputLimits:
    visible_input_bytes: int = 192 * 1024
    schema_bytes: int = 16_384
    visible_output_bytes: int = 1_048_576
    private_reasoning_bytes: int = 1_048_576
    continuation_bytes: int = 1_048_576
    wire_response_bytes: int = 2_097_152
    messages: int = RUN_CONTEXT_MESSAGES
    tools: int = 16

    def __post_init__(self):
        defaults = {f.name: f.default for f in fields(self)}
        if any(not 0 < getattr(self, name) <= maximum for name, maximum in defaults.items()):
            raise ValueError("output limits must stay within application limits")


@dataclass(frozen=True, slots=True)
class CallPolicy:
    started_monotonic: float
    deadline_monotonic: float
    max_attempts: Literal[1] = 1
    allow_fallback: Literal[False] = False
    connect_timeout_s: float = 10.0
    cleanup_timeout_s: float = 2.0
    limits: OutputLimits = field(default_factory=OutputLimits)

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(v)
            for v in (
                self.started_monotonic,
                self.deadline_monotonic,
                self.connect_timeout_s,
                self.cleanup_timeout_s,
            )
        ):
            raise ValueError("policy times must be finite")
        if not 0 < self.connect_timeout_s <= 10 or not 0 < self.cleanup_timeout_s <= 2:
            raise ValueError("timeouts exceed application limits")
        if self.max_attempts != 1 or self.allow_fallback is not False:
            raise ValueError("current runtime accepts one attempt without fallback")
        if not 0 < self.deadline_monotonic - self.started_monotonic <= 180:
            raise ValueError("call duration must be within 180 seconds")


@dataclass(frozen=True, slots=True)
class LogicalPolicy:
    max_attempts: Literal[1, 2] = 1
    attempt_timeout_s: float = 180.0

    def __post_init__(self):
        if (
            self.max_attempts not in {1, 2}
            or not math.isfinite(self.attempt_timeout_s)
            or not 0 < self.attempt_timeout_s <= 180
        ):
            raise ValueError("invalid logical policy")
