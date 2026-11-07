"""Agent-owned model configuration and metadata. No conversation content is stored."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    column,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.functions import FunctionElement

from compass_agent.database import Base


class NormalizedAlias(FunctionElement):
    """Portable unit-test SQL and PostgreSQL's reflected expression spelling."""

    type = String()
    inherit_cache = True


@compiles(NormalizedAlias)
def alias_sql(element, compiler, **kwargs):
    return "lower(trim(" + compiler.process(element.clauses, **kwargs) + "))"


@compiles(NormalizedAlias, "postgresql")
def alias_postgresql(element, compiler, **kwargs):
    return "lower(TRIM(BOTH FROM " + compiler.process(element.clauses, **kwargs) + "))"


class Provider(Base):
    __tablename__ = "model_provider"
    __table_args__ = (
        CheckConstraint("kind IN ('llm','decision')", name="ck_model_provider_kind"),
        CheckConstraint(
            "adapter IN ('litellm_chat','systemone_decision')", name="ck_model_provider_adapter"
        ),
        CheckConstraint("kind = 'llm' OR chat_visible = false", name="ck_model_provider_chat_kind"),
        CheckConstraint(
            "version > 0 AND binding_version > 0 AND binding_version <= version",
            name="ck_model_provider_version",
        ),
        Index("uq_model_provider_alias", NormalizedAlias(column("alias")), unique=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    catalog_id: Mapped[str] = mapped_column(String(200))
    vendor: Mapped[str] = mapped_column(String(200))
    api_model: Mapped[str] = mapped_column(String(200))
    sdk_model: Mapped[str | None] = mapped_column(String(200))
    alias: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20))
    adapter: Mapped[str] = mapped_column(String(30))
    base_url: Mapped[str] = mapped_column(String(2048))
    api_key_ciphertext: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    chat_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer)
    binding_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[UUID] = mapped_column(Uuid)


class ModelChange(Base):
    __tablename__ = "model_change"
    __table_args__ = (Index("ix_model_change_provider_time", "provider_id", "created_at"),)
    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("model_provider.id"))
    actor_user_id: Mapped[UUID] = mapped_column(Uuid)
    request_mac: Mapped[str] = mapped_column(String(64))
    before_version: Mapped[int | None] = mapped_column(Integer)
    after_version: Mapped[int] = mapped_column(Integer)
    changed_fields: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelCall(Base):
    __tablename__ = "model_call"
    __table_args__ = (
        CheckConstraint("purpose IN ('test','chat')", name="ck_model_call_purpose"),
        CheckConstraint(
            "status IN ('running','completed','failed','canceled','incomplete')",
            name="ck_model_call_status",
        ),
        CheckConstraint(
            "(purpose = 'test' AND tenant_id IS NULL AND workspace_id IS NULL) "
            "OR (purpose = 'chat' AND tenant_id IS NOT NULL AND workspace_id IS NOT NULL)",
            name="ck_model_call_scope",
        ),
        Index("ix_model_call_provider_time", "provider_id", "purpose", "started_at"),
        Index("ix_model_call_actor_time", "tenant_id", "actor_user_id", "started_at"),
    )
    call_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("model_provider.id"))
    config_version: Mapped[int] = mapped_column(Integer)
    binding_version: Mapped[int] = mapped_column(Integer)
    requested_model: Mapped[str] = mapped_column(String(200))
    response_model: Mapped[str | None] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(20))
    actor_user_id: Mapped[UUID] = mapped_column(Uuid)
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid)
    workspace_id: Mapped[UUID | None] = mapped_column(Uuid)
    capability_summary: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    execution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))
    upstream_request_id: Mapped[str | None] = mapped_column(String(200))
    upstream_outcome_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
