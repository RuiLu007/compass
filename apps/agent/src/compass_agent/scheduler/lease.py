"""The Scheduler role lease is independent of per-Run execution leases."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from compass_agent.models.errors import ModelError
from compass_agent.runtime.deployment import require_database_revision, require_internal_mode
from compass_agent.scheduler.entities import RuntimeControl

LEASE_TTL = timedelta(seconds=15)
HEARTBEAT_SECONDS = 3


def clock(tx) -> datetime:
    value = tx.scalar(select(func.clock_timestamp()))
    assert isinstance(value, datetime)
    return value


@dataclass(frozen=True)
class SchedulerLease:
    boot_id: UUID
    generation: int
    expires_at: datetime


def lock_role(tx, *, write=False):
    require_internal_mode(tx)
    row = tx.scalar(
        select(RuntimeControl)
        .where(RuntimeControl.key == "scheduler")
        .with_for_update(read=not write)
    )
    if row is None:
        raise ModelError("scheduler_unavailable", 503)
    return row


def require_live(tx, row, lease=None):
    now = clock(tx)
    if (
        row is None
        or row.holder_id is None
        or row.expires_at is None
        or row.expires_at <= now
        or (lease and (row.holder_id, row.generation) != (lease.boot_id, lease.generation))
    ):
        raise ModelError("scheduler_lease_lost", 409)
    return now


class SchedulerLeaseStore:
    def __init__(self, engine):
        self.engine = engine

    def acquire(self, boot_id):
        with Session(self.engine) as tx, tx.begin():
            require_database_revision(tx)
            role = lock_role(tx, write=True)
            now = clock(tx)
            if role.expires_at and role.expires_at > now:
                return None
            role.holder_id = boot_id
            role.generation += 1
            expiry = now + LEASE_TTL
            role.expires_at = expiry
            return SchedulerLease(boot_id, role.generation, expiry)

    def renew(self, lease):
        with Session(self.engine) as tx, tx.begin():
            role = lock_role(tx, write=True)
            now = require_live(tx, role, lease)
            role.expires_at = now + LEASE_TTL

    def release(self, lease):
        with Session(self.engine) as tx, tx.begin():
            role = lock_role(tx, write=True)
            if (role.holder_id, role.generation) == (lease.boot_id, lease.generation):
                role.holder_id, role.expires_at = None, None

    def ready(self):
        with Session(self.engine) as tx, tx.begin():
            require_database_revision(tx)
            require_internal_mode(tx)
            role = tx.get(RuntimeControl, "scheduler")
            require_live(tx, role)
