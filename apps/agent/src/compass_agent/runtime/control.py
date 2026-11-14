"""Event-loop-owned Run lifecycle, ownership, cancellation and terminal arbitration."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeVar
from uuid import UUID, uuid4

from compass_agent.schemas import (
    DEFAULT_RUN_TIMEOUT_SECONDS,
    AnalysisCancelAccepted,
    AnalysisCancelAlreadyTerminal,
    RunCanceled,
    RunCleanupUnconfirmed,
    RunFailed,
    RunResult,
    RunTerminalStatus,
    TrustedContext,
)
from compass_agent.tools.contracts import PhaseCanceled, StopRequested

T = TypeVar("T")
StopReason = Literal["user", "disconnected", "shutdown", "deadline"]


class RunCapacityExceeded(Exception):
    pass


class RunRegistryClosed(Exception):
    pass


@dataclass(frozen=True, slots=True)
class OwnerKey:
    actor_user_id: UUID
    tenant_id: UUID
    workspace_id: UUID

    @classmethod
    def from_context(cls, context: TrustedContext) -> OwnerKey:
        if context.purpose != "chat" or context.scope.kind != "tenant":
            raise ValueError("analysis runs require tenant chat context")
        return cls(
            actor_user_id=context.actor_user_id,
            tenant_id=context.scope.tenant_id,
            workspace_id=context.scope.workspace_id,
        )


@dataclass(frozen=True, slots=True)
class TerminalRunSummary:
    run_id: UUID
    owner: OwnerKey
    status: RunTerminalStatus
    expires_at: float


class RunRegistry:
    def __init__(
        self,
        *,
        max_active: int = 8,
        terminal_limit: int = 128,
        terminal_ttl_s: int = 300,
    ):
        self.max_active = max_active
        self.terminal_limit = terminal_limit
        self.terminal_ttl_s = terminal_ttl_s
        self.active: dict[UUID, RunControl] = {}
        self.terminal: OrderedDict[UUID, TerminalRunSummary] = OrderedDict()
        self.closing = False

    def register(
        self,
        owner: OwnerKey,
        *,
        lifetime_s: float = DEFAULT_RUN_TIMEOUT_SECONDS,
        run_id: UUID | None = None,
    ) -> RunControl:
        if self.closing:
            raise RunRegistryClosed()
        self._purge()
        if len(self.active) >= self.max_active:
            raise RunCapacityExceeded()
        loop = asyncio.get_running_loop()
        run_id = run_id or uuid4()
        if run_id in self.active:
            raise RunCapacityExceeded()
        control = RunControl(
            run_id=run_id,
            owner=owner,
            registered_at=loop.time(),
            deadline_at=loop.time() + lifetime_s,
            registry=self,
        )
        self.active[run_id] = control
        return control

    def lookup(self, run_id: UUID) -> RunControl | TerminalRunSummary | None:
        self._purge()
        return self.active.get(run_id) or self.terminal.get(run_id)

    def finish(self, control: RunControl, status: RunTerminalStatus) -> None:
        if self.active.get(control.id) is not control:
            raise RuntimeError("run registry ownership changed")
        del self.active[control.id]
        summary = TerminalRunSummary(
            run_id=control.id,
            owner=control.owner,
            status=status,
            expires_at=asyncio.get_running_loop().time() + self.terminal_ttl_s,
        )
        self.terminal[control.id] = summary
        self.terminal.move_to_end(control.id)
        while len(self.terminal) > self.terminal_limit:
            self.terminal.popitem(last=False)

    async def close(self, *, timeout_s: float = 8) -> None:
        self.closing = True
        controls = tuple(self.active.values())
        for control in controls:
            control.request_stop("shutdown")
        if controls:
            try:
                async with asyncio.timeout(timeout_s):
                    await asyncio.gather(*(control.finished.wait() for control in controls))
            except TimeoutError:
                pass

    def _purge(self) -> None:
        now = asyncio.get_running_loop().time()
        expired = [run_id for run_id, item in self.terminal.items() if item.expires_at <= now]
        for run_id in expired:
            self.terminal.pop(run_id, None)


class RunControl:
    def __init__(
        self,
        *,
        run_id: UUID,
        owner: OwnerKey,
        registered_at: float,
        deadline_at: float,
        registry: RunRegistry,
    ):
        self.id = run_id
        self.owner = owner
        self.registered_at = registered_at
        self.deadline_at = deadline_at
        self.registry = registry
        self.stop_reason: StopReason | None = None
        self.phase: asyncio.Task | None = None
        self.phase_deadline: float | None = None
        self.cleanup_confirmed = True
        self.terminal: RunResult | None = None
        self.finished = asyncio.Event()

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline_at - asyncio.get_running_loop().time())

    @property
    def duration_ms(self) -> int:
        return max(0, int((asyncio.get_running_loop().time() - self.registered_at) * 1000))

    def _mark_deadline(self) -> None:
        if self.stop_reason is None and asyncio.get_running_loop().time() >= self.deadline_at:
            self.stop_reason = "deadline"

    def require_running(self) -> None:
        self._mark_deadline()
        if self.stop_reason is not None or self.terminal is not None:
            raise StopRequested()

    def request_stop(
        self, reason: StopReason
    ) -> AnalysisCancelAccepted | AnalysisCancelAlreadyTerminal:
        if self.terminal is not None:
            return AnalysisCancelAlreadyTerminal(
                runId=str(self.id),
                status="already_terminal",
                terminalStatus=self.terminal.status,
            )
        if self.stop_reason is None:
            self._mark_deadline()
            self.stop_reason = self.stop_reason or reason
            if self.phase is not None and not self.phase.done() and not self.phase.cancelling():
                self.phase.cancel()
        return AnalysisCancelAccepted(runId=str(self.id), status="accepted")

    async def perform(
        self, operation: Callable[[], Awaitable[T]], *, timeout_s: float | None = None
    ) -> T:
        """Run one phase after an atomic dispatch check and retain its cleanup receipt."""

        self.require_running()
        loop = asyncio.get_running_loop()
        deadline = (
            self.deadline_at
            if timeout_s is None
            else min(self.deadline_at, loop.time() + timeout_s)
        )
        self.phase_deadline = deadline
        phase_started = False

        async def start_phase() -> T:
            nonlocal phase_started
            self.require_running()
            if loop.time() >= deadline:
                raise TimeoutError
            phase_started = True
            return await operation()

        phase = asyncio.create_task(start_phase())
        self.phase = phase
        try:
            done, _ = await asyncio.wait({phase}, timeout=max(0, deadline - loop.time()))
            if phase not in done:
                if deadline == self.deadline_at and self.stop_reason is None:
                    self.stop_reason = "deadline"
                await self._cancel_phase(phase, phase_started=phase_started)
                self.require_running()
                raise TimeoutError
            result = phase.result()
            self.require_running()
            if timeout_s is not None and loop.time() >= deadline:
                raise TimeoutError
            return result
        except PhaseCanceled as error:
            self.cleanup_confirmed &= error.cleanup_confirmed
            raise StopRequested() from None
        except asyncio.CancelledError:
            await self._cancel_phase(phase, phase_started=phase_started)
            raise StopRequested() from None
        finally:
            self.phase = None
            self.phase_deadline = None

    async def _cancel_phase(self, phase: asyncio.Task, *, phase_started: bool) -> None:
        """Cancel and settle a phase without losing its explicit cleanup receipt."""

        if not phase.done() and not phase.cancelling():
            phase.cancel()
        try:
            await phase
        except PhaseCanceled as error:
            self.cleanup_confirmed &= error.cleanup_confirmed
        except asyncio.CancelledError:
            if phase_started:
                self.cleanup_confirmed = False
        except Exception:
            # An operation which exits unusually while handling cancellation cannot
            # prove that its external resource has reached a terminal state.
            self.cleanup_confirmed = False

    def mark_cleanup_unconfirmed(self) -> None:
        self.cleanup_confirmed = False

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
