from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from compass_agent.database import Base


class LegacyConnectionBase(DeclarativeBase):
    pass


class RetailConnection(LegacyConnectionBase):
    __tablename__ = "retail_connection"
    __table_args__ = (
        CheckConstraint("port BETWEEN 1 AND 65535", name="ck_retail_connection_port"),
        CheckConstraint("retail_tenant_id > 0", name="ck_retail_connection_tenant"),
        CheckConstraint("revision > 0", name="ck_retail_connection_revision"),
    )

    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int]
    database_name: Mapped[str] = mapped_column(String(63))
    role: Mapped[str] = mapped_column(String(63))
    sslmode: Mapped[str] = mapped_column(String(11))
    retail_tenant_id: Mapped[int] = mapped_column(BigInteger)
    password_ciphertext: Mapped[str] = mapped_column(Text)
    revision: Mapped[int]


class DataConnection(Base):
    __tablename__ = "data_connection"
    __table_args__ = (
        CheckConstraint("port BETWEEN 1 AND 65535", name="ck_data_connection_port"),
        CheckConstraint("revision > 0", name="ck_data_connection_revision"),
        CheckConstraint("engine IN ('postgresql', 'mysql')", name="ck_data_connection_engine"),
        CheckConstraint(
            "(retail_tenant_id IS NULL AND id <> workspace_id) OR "
            "(retail_tenant_id IS NOT NULL AND retail_tenant_id > 0 "
            "AND engine = 'postgresql' AND id = workspace_id)",
            name="ck_data_connection_retail",
        ),
        UniqueConstraint("workspace_id", "name", name="uq_data_connection_workspace_name"),
        Index("ix_data_connection_scope", "tenant_id", "workspace_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID]
    tenant_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(80))
    engine: Mapped[str] = mapped_column(String(12))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int]
    database_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(128))
    sslmode: Mapped[str] = mapped_column(String(11))
    retail_tenant_id: Mapped[int | None] = mapped_column(BigInteger)
    password_ciphertext: Mapped[str] = mapped_column(Text)
    revision: Mapped[int]
