"""Run-owned semantic manifest and encrypted plans, results and user proposals."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from compass_agent.database import Base
from compass_agent.runtime.entities import DOCUMENT


class SemanticRunBinding(Base):
    __tablename__ = "semantic_run_binding"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_run.id"), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    workspace_id: Mapped[UUID] = mapped_column(Uuid)
    catalog_kind: Mapped[str] = mapped_column(String(20))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    partition_releases: Mapped[list] = mapped_column(DOCUMENT)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SemanticMetricOffer(Base):
    __tablename__ = "semantic_metric_offer"
    offer_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("agent_session.id"), index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_run.id"))
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    private_payload: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
