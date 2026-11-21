from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from compass_agent.database import Base
from compass_agent.runtime.entities import DOCUMENT

PROMPT_TABLES = frozenset(
    {"prompt_file", "prompt_version", "prompt_generation", "prompt_head", "prompt_command"}
)


def now():
    return datetime.now(UTC)


class PromptFile(Base):
    __tablename__ = "prompt_file"
    __table_args__ = (
        UniqueConstraint("portable_key", name="uq_prompt_file_portable"),
        CheckConstraint("origin IN ('bundled','online')", name="ck_prompt_origin"),
        CheckConstraint(
            "edit_rev >= 1 AND max_version >= 0 AND active_epoch >= 0", name="ck_prompt_counters"
        ),
        CheckConstraint(
            "(draft_body IS NULL AND draft_base_version IS NULL AND draft_base_epoch IS NULL)"
            " OR (draft_body IS NOT NULL AND draft_base_epoch IS NOT NULL)",
            name="ck_prompt_draft",
        ),
        CheckConstraint(
            "(last_import_body IS NULL) = (last_import_hash IS NULL)", name="ck_prompt_import"
        ),
    )
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    portable_key: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)
    edit_rev: Mapped[int] = mapped_column(BigInteger)
    max_version: Mapped[int] = mapped_column(BigInteger)
    active_epoch: Mapped[int] = mapped_column(BigInteger)
    draft_body: Mapped[str | None] = mapped_column(Text)
    draft_base_version: Mapped[int | None] = mapped_column(BigInteger)
    draft_base_epoch: Mapped[int | None] = mapped_column(BigInteger)
    last_import_body: Mapped[str | None] = mapped_column(Text)
    last_import_hash: Mapped[str | None] = mapped_column(CHAR(64))
    updated_by: Mapped[UUID] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptVersion(Base):
    __tablename__ = "prompt_version"
    __table_args__ = (CheckConstraint("version > 0 AND byte_count > 0", name="ck_prompt_version"),)
    path: Mapped[str] = mapped_column(ForeignKey("prompt_file.path"), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    body: Mapped[str] = mapped_column(Text)
    byte_count: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    note: Mapped[str] = mapped_column(Text)


class PromptGeneration(Base):
    __tablename__ = "prompt_generation"
    __table_args__ = (
        UniqueConstraint("snapshot_no", name="uq_prompt_snapshot"),
        CheckConstraint(
            "(action = 'file' AND snapshot_no IS NULL) OR (action IN ('all','restore') "
            "AND snapshot_no > 0 AND snapshot_no IS NOT NULL)",
            name="ck_prompt_snapshot",
        ),
        CheckConstraint(
            "(action = 'restore') = (restored_from_id IS NOT NULL)", name="ck_prompt_restore"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("prompt_generation.id"))
    manifest: Mapped[dict] = mapped_column(DOCUMENT)
    manifest_digest: Mapped[str] = mapped_column(CHAR(64))
    policy_version: Mapped[str] = mapped_column(Text)
    policy_digest: Mapped[str] = mapped_column(CHAR(64))
    action: Mapped[str] = mapped_column(Text)
    snapshot_no: Mapped[int | None] = mapped_column(BigInteger)
    restored_from_id: Mapped[int | None] = mapped_column(ForeignKey("prompt_generation.id"))
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    note: Mapped[str] = mapped_column(Text)


class PromptHead(Base):
    __tablename__ = "prompt_head"
    __table_args__ = (CheckConstraint("id = 1", name="ck_prompt_singleton"),)
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    generation: Mapped[int | None] = mapped_column(ForeignKey("prompt_generation.id"))
    last_snapshot_no: Mapped[int] = mapped_column(BigInteger)


class PromptCommand(Base):
    __tablename__ = "prompt_command"
    actor_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    request_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    action: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(CHAR(64))
    receipt: Mapped[dict] = mapped_column(DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
