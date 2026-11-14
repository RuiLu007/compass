"""Optional, server-configured checks for candidate results."""

from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from compass_agent.schemas import RunCompleted, RunWaiting, TrustedContext


@dataclass(frozen=True, slots=True)
class VerificationContext:
    run_id: UUID
    caller: TrustedContext
    target: str | None = None


class VerificationRejected(Exception):
    """A configured check rejected the candidate."""


type VerificationHook[T] = Callable[[T, VerificationContext], Awaitable[None]]
type RunVerifier = VerificationHook[RunCompleted | RunWaiting]


async def verify_result[T](
    candidate: T,
    context: VerificationContext,
    hooks: Sequence[VerificationHook[T]],
) -> None:
    """Run read-only checks in order. Each hook sees an isolated snapshot."""
    for hook in hooks:
        try:
            await hook(deepcopy(candidate), deepcopy(context))
        except VerificationRejected:
            raise
        except Exception as error:
            raise VerificationRejected() from error
