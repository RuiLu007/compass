"""Plans, durable triggers and the two runtime role control records."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from compass_agent.database import Base


class RuntimeControl(Base):
    __tablename__ = "agent_runtime_control"

    key: Mapped[str] = mapped_column(String(24), primary_key=True)
    mode: Mapped[str | None] = mapped_column(String(16))
    policy_sha256: Mapped[str | None] = mapped_column(String(64))
    holder_id: Mapped[UUID | None] = mapped_column(Uuid)
    generation: Mapped[int] = mapped_column(BigInteger, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentSchedule(Base):
    __tablename__ = "agent_schedule"
    __table_args__ = (
        CheckConstraint(
            "status IN ('enabled','disabled','deleted') AND revision > 0 "
            "AND ((status = 'enabled') = (next_fire_at IS NOT NULL))",
            name="ck_agent_schedule_state",
        ),
        Index("ix_agent_schedule_owner", "tenant_id", "workspace_id", "owner_user_id", "id"),
        Index(
            "ix_agent_schedule_due",
            "next_fire_at",
            "id",
            postgresql_where=text("status = 'enabled'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    workspace_id: Mapped[UUID] = mapped_column(Uuid)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(120))
    cron: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(128))
    input_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(12))
    revision: Mapped[int] = mapped_column(BigInteger)
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentScheduleTrigger(Base):
    __tablename__ = "agent_schedule_trigger"
    __table_args__ = (
        UniqueConstraint("schedule_id", "scheduled_at", name="uq_agent_trigger_slot"),
        UniqueConstraint("run_id", name="uq_agent_trigger_run"),
        CheckConstraint(
            "delivery_status IN ('pending','dispatching','retry_wait','reconcile_pending',"
            "'submitted','delivery_failed','skipped') AND delivery_attempts BETWEEN 0 AND 8 "
            "AND ((delivery_status = 'submitted') = (run_id IS NOT NULL)) "
            "AND ((run_id IS NOT NULL) = (accepted_at IS NOT NULL))",
            name="ck_agent_trigger_state",
        ),
        Index("ix_agent_trigger_history", "schedule_id", "scheduled_at", "id"),
        Index(
            "ix_agent_trigger_delivery",
            "next_attempt_at",
            "scheduled_at",
            "id",
            postgresql_where=text(
                "delivery_status::text = ANY (ARRAY['pending','retry_wait','dispatching']::text[])"
            ),
            sqlite_where=text("delivery_status IN ('pending','retry_wait','dispatching')"),
        ),
        Index(
            "ix_agent_trigger_reconcile",
            "next_attempt_at",
            "scheduled_at",
            "id",
            postgresql_where=text("delivery_status = 'reconcile_pending'"),
        ),
        Index(
            "ix_agent_trigger_sync",
            "observed_at",
            "id",
            postgresql_where=text("delivery_status = 'submitted' AND finished_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    schedule_id: Mapped[UUID] = mapped_column(ForeignKey("agent_schedule.id"))
    schedule_revision: Mapped[int] = mapped_column(BigInteger)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    coalesced_from_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    delivery_status: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(80))
    delivery_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    delivery_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_token: Mapped[UUID | None] = mapped_column(Uuid)
    delivery_generation: Mapped[int | None] = mapped_column(BigInteger)
    delivery_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_run.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_run_status: Mapped[str | None] = mapped_column(String(16))
    observed_run_version: Mapped[int | None] = mapped_column(BigInteger)
    observed_reason_code: Mapped[str | None] = mapped_column(String(80))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
