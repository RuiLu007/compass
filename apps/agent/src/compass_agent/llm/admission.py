"""Event-loop-owned capacity shared by all model services in one process."""

import asyncio


class RuntimeOverloaded(Exception):
    pass


class ExecutionGate:
    def __init__(self, capacity: int = 8, max_inflight: int = 32):
        if not 1 <= capacity <= min(max_inflight, 64) or max_inflight > 256:
            raise ValueError("invalid execution limits")
        self.capacity = capacity
        self.max_inflight = max_inflight
        self.inflight = self.active = self.quarantined = 0
        self.peak_active = self.peak_inflight = 0
        self.slots = asyncio.BoundedSemaphore(capacity)

    def enter(self):
        if self.inflight >= self.max_inflight:
            raise RuntimeOverloaded()
        self.inflight += 1
        self.peak_inflight = max(self.peak_inflight, self.inflight)

    def leave(self):
        if self.inflight <= 0:
            raise RuntimeError("logical slot released twice")
        self.inflight -= 1

    def start_attempt(self):
        self.active += 1
        self.peak_active = max(self.peak_active, self.active + self.quarantined)

    def finish_attempt(self, *, cleanup_confirmed: bool):
        self.active -= 1
        if cleanup_confirmed:
            self.slots.release()
        else:
            self.quarantined += 1

    def snapshot(self):
        return dict(
            capacity=self.capacity,
            max_inflight=self.max_inflight,
            active=self.active,
            inflight=self.inflight,
            quarantined=self.quarantined,
            available=self.capacity - self.active - self.quarantined,
            peak_active=self.peak_active,
            peak_inflight=self.peak_inflight,
        )
