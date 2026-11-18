"""Database-clock scanning, trigger claims and summary synchronization."""

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from compass_agent.models.errors import ModelError
from compass_agent.runtime.private_state import PrivateState
from compass_agent.scheduler.cron import Cron
from compass_agent.scheduler.entities import AgentSchedule, AgentScheduleTrigger
from compass_agent.scheduler.lease import clock, lock_role, require_live
from compass_agent.scheduler.service import OPEN_DELIVERIES, clear_delivery

MAX_ATTEMPTS = 8
DELIVERY_WINDOW = timedelta(minutes=30)
TOKEN_TTL = timedelta(seconds=10)
BATCH_SIZE = 32
MAX_INFLIGHT = 4
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryClaim:
    trigger_id: UUID
    token: UUID
    attempt: int
    reconciliation: bool


def exhausted(trigger, now):
    return (
        trigger.delivery_attempts >= MAX_ATTEMPTS
        or trigger.delivery_deadline_at is None
        or trigger.delivery_deadline_at <= now
    )


def requires_reconciliation(trigger, role, now):
    return trigger.delivery_status == "dispatching" and (
        trigger.delivery_generation != role.generation or trigger.delivery_expires_at <= now
    )


def begin_reconciliation(trigger, now, reason):
    clear_delivery(trigger)
    trigger.delivery_status, trigger.reason_code = "reconcile_pending", reason
    trigger.next_attempt_at = now


class ScheduleRepository:
    def __init__(self, engine, key):
        self.engine, self.private = engine, PrivateState(key)

    @staticmethod
    def has_overlap(tx, schedule_id):
        return (
            tx.execute(
                text("""
                SELECT 1 FROM agent_schedule_trigger AS trigger
                LEFT JOIN agent_scheduled_run_summary AS run ON run.id = trigger.run_id
                WHERE trigger.schedule_id = :id AND (
                    trigger.delivery_status IN (
                        'pending','dispatching','retry_wait','reconcile_pending'
                    )
                    OR (trigger.delivery_status = 'submitted' AND run.finished_at IS NULL)
                ) LIMIT 1
            """),
                {"id": schedule_id},
            ).first()
            is not None
        )

    def scan(self, lease, limit=BATCH_SIZE):
        observations = []
        with Session(self.engine) as tx, tx.begin():
            role = lock_role(tx)
            plans = tx.scalars(
                select(AgentSchedule)
                .where(AgentSchedule.status == "enabled", AgentSchedule.next_fire_at <= clock(tx))
                .order_by(AgentSchedule.next_fire_at, AgentSchedule.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for plan in plans:
                now = require_live(tx, role, lease)
                cron = Cron.parse(plan.cron, plan.timezone)
                due = cron.nearest(now, previous=True)
                if plan.next_fire_at is None or plan.next_fire_at > due:
                    continue
                identifier = uuid4()
                skipped = self.has_overlap(tx, plan.id)
                snapshot = None
                if not skipped:
                    payload = self.private.open(plan.id, "schedule", plan.input_ciphertext)
                    snapshot = self.private.seal(identifier, "schedule_trigger", payload)
                detected = require_live(tx, role, lease)
                inserted = tx.scalar(
                    insert(AgentScheduleTrigger)
                    .values(
                        id=identifier,
                        schedule_id=plan.id,
                        schedule_revision=plan.revision,
                        scheduled_at=due,
                        detected_at=detected,
                        coalesced_from_at=plan.next_fire_at if due > plan.next_fire_at else None,
                        input_ciphertext=snapshot,
                        delivery_status="skipped" if skipped else "pending",
                        reason_code="schedule_overlap" if skipped else None,
                        delivery_deadline_at=None if skipped else detected + DELIVERY_WINDOW,
                        next_attempt_at=None if skipped else detected,
                    )
                    .on_conflict_do_nothing(constraint="uq_agent_trigger_slot")
                    .returning(AgentScheduleTrigger.id)
                )
                if inserted:
                    observations.append((plan.id, inserted, detected - due, skipped))
                plan.next_fire_at = cron.nearest(now)
        for schedule_id, trigger_id, delay, skipped in observations:
            logger.info(
                "schedule.detected schedule_id=%s trigger_id=%s discovery_ms=%d status=%s",
                schedule_id,
                trigger_id,
                delay.total_seconds() * 1000,
                "skipped_overlap" if skipped else "pending",
            )
        return len(observations)

    def waiting(self, lease):
        with Session(self.engine) as tx, tx.begin():
            now = require_live(tx, lock_role(tx), lease)
            return tx.execute(
                text("""
                    SELECT count(*), COALESCE(EXTRACT(EPOCH FROM (:now - min(detected_at))), 0)
                    FROM agent_schedule_trigger
                    WHERE delivery_status::text =
                        ANY(ARRAY['pending','retry_wait','dispatching']::text[])
                       OR delivery_status = 'reconcile_pending'
                """),
                {"now": now},
            ).one()

    def claim(self, lease, *, reconciliation, limit=MAX_INFLIGHT):
        trigger = AgentScheduleTrigger
        with Session(self.engine) as tx, tx.begin():
            role = lock_role(tx)
            current = require_live(tx, role, lease)
            eligible = (
                and_(
                    trigger.delivery_status == "reconcile_pending",
                    trigger.next_attempt_at <= current,
                )
                if reconciliation
                else or_(
                    and_(
                        trigger.delivery_status.in_(("pending", "retry_wait")),
                        trigger.next_attempt_at <= current,
                    ),
                    and_(
                        trigger.delivery_status == "dispatching",
                        or_(
                            trigger.delivery_generation != role.generation,
                            trigger.delivery_expires_at <= current,
                        ),
                    ),
                )
            )
            candidates = tx.execute(
                select(trigger.id, trigger.schedule_id)
                .where(eligible)
                .order_by(trigger.next_attempt_at, trigger.scheduled_at, trigger.id)
                .limit(limit)
            ).all()
            claims = []
            for identifier, schedule_id in candidates:
                plan = tx.scalar(
                    select(AgentSchedule)
                    .where(AgentSchedule.id == schedule_id)
                    .with_for_update(skip_locked=True)
                )
                if plan is None:
                    continue
                row = tx.scalar(
                    select(trigger)
                    .where(trigger.id == identifier)
                    .with_for_update(skip_locked=True)
                )
                now = require_live(tx, role, lease)
                if row is None or row.delivery_status not in OPEN_DELIVERIES:
                    continue
                if not reconciliation:
                    if row.delivery_status == "reconcile_pending":
                        continue
                    if row.delivery_attempts == 0 and (
                        plan.status != "enabled" or row.schedule_revision != plan.revision
                    ):
                        clear_delivery(row)
                        row.delivery_status, row.reason_code = "skipped", "schedule_changed"
                        row.input_ciphertext = None
                        continue
                    if requires_reconciliation(row, role, now):
                        begin_reconciliation(row, now, "delivery_interrupted")
                        continue
                    if exhausted(row, now):
                        begin_reconciliation(row, now, row.reason_code or "delivery_expired")
                        continue
                    if row.delivery_status not in {"pending", "retry_wait"}:
                        continue
                    row.delivery_status = "dispatching"
                    row.delivery_attempts += 1
                elif row.delivery_status != "reconcile_pending":
                    continue
                token = uuid4()
                row.delivery_token = token
                row.delivery_generation = role.generation
                assert role.expires_at is not None
                row.delivery_expires_at = min(role.expires_at, now + TOKEN_TTL)
                if reconciliation:
                    row.next_attempt_at = row.delivery_expires_at
                claims.append(DeliveryClaim(row.id, token, row.delivery_attempts, reconciliation))
            return claims

    def failure(self, lease, claim, reason, delay, *, permanent=False):
        with Session(self.engine) as tx, tx.begin():
            role = lock_role(tx)
            row = tx.scalar(
                select(AgentScheduleTrigger)
                .where(AgentScheduleTrigger.id == claim.trigger_id)
                .with_for_update()
            )
            now = require_live(tx, role, lease)
            if row is None or row.delivery_token != claim.token:
                return
            if (
                row.delivery_expires_at is None
                or row.delivery_expires_at <= now
                or row.delivery_generation != role.generation
            ):
                return
            retry_at = now + timedelta(seconds=delay)
            if claim.reconciliation:
                clear_delivery(row)
                row.next_attempt_at = retry_at
            elif (
                permanent
                or exhausted(row, now)
                or row.delivery_deadline_at is None
                or retry_at >= row.delivery_deadline_at
            ):
                begin_reconciliation(
                    row,
                    now if permanent or exhausted(row, now) else row.delivery_deadline_at,
                    reason,
                )
            else:
                clear_delivery(row)
                row.delivery_status, row.reason_code, row.next_attempt_at = (
                    "retry_wait",
                    reason,
                    retry_at,
                )

    def sync(self, lease, limit=BATCH_SIZE):
        accepted = []
        completed = []
        with Session(self.engine) as tx, tx.begin():
            role = lock_role(tx)
            rows = tx.execute(
                text("""
                    SELECT trigger.id, trigger.schedule_id, trigger.observed_run_version,
                           trigger.accepted_at - trigger.detected_at AS admission_age,
                           run.id AS run_id, run.version, run.status, run.reason_code,
                           run.finished_at
                    FROM agent_schedule_trigger AS trigger
                    JOIN agent_scheduled_run_summary AS run ON trigger.run_id = run.id
                    WHERE trigger.delivery_status = 'submitted' AND trigger.finished_at IS NULL
                    AND (trigger.observed_run_version IS NULL
                         OR trigger.observed_run_version < run.version)
                    ORDER BY trigger.observed_at NULLS FIRST, trigger.id
                    LIMIT :limit FOR UPDATE OF trigger SKIP LOCKED
                """),
                {"limit": limit},
            ).all()
            for row in rows:
                now = require_live(tx, role, lease)
                tx.execute(
                    text("""
                        UPDATE agent_schedule_trigger
                        SET observed_run_version=:version, observed_run_status=:status,
                            observed_reason_code=:reason, observed_at=:now, finished_at=:finished
                        WHERE id=:id AND (
                            observed_run_version IS NULL OR observed_run_version < :version
                        )
                    """),
                    dict(
                        id=row.id,
                        version=row.version,
                        status=row.status,
                        reason=row.reason_code,
                        now=now,
                        finished=row.finished_at,
                    ),
                )
                if row.observed_run_version is None:
                    accepted.append(row)
                if row.finished_at:
                    completed.append((row.id, row.status, now - row.finished_at))
        for row in accepted:
            logger.info(
                "schedule.accepted schedule_id=%s trigger_id=%s run_id=%s admission_ms=%d",
                row.schedule_id,
                row.id,
                row.run_id,
                row.admission_age.total_seconds() * 1000,
            )
        for trigger_id, status, lag in completed:
            logger.info(
                "schedule.result_synced trigger_id=%s run_status=%s sync_ms=%d",
                trigger_id,
                status,
                lag.total_seconds() * 1000,
            )
        return len(rows)


def verify_delivery(tx, role, trigger, token, status):
    now = require_live(tx, role)
    if (
        trigger.delivery_status != status
        or trigger.delivery_token != token
        or trigger.delivery_generation != role.generation
        or trigger.delivery_expires_at is None
        or trigger.delivery_expires_at <= now
    ):
        raise ModelError("scheduler_delivery_stale", 409)
    return now
