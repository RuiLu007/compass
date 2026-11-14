"""Agent-owned personal MCP configuration; secrets never leave the repository port."""

from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from compass_agent.database import Base


class McpConnection(Base):
    __tablename__ = "mcp_connection"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_mcp_revision"),
        CheckConstraint("transport = 'streamable_http'", name="ck_mcp_transport"),
        CheckConstraint("auth_type IN ('none', 'bearer')", name="ck_mcp_auth_type"),
        CheckConstraint("max_inflight BETWEEN 1 AND 2", name="ck_mcp_inflight"),
        UniqueConstraint(
            "tenant_id", "workspace_id", "actor_user_id", "name", name="uq_mcp_owner_name"
        ),
        Index("ix_mcp_owner", "tenant_id", "workspace_id", "actor_user_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    workspace_id: Mapped[UUID]
    actor_user_id: Mapped[UUID]
    revision: Mapped[int]
    name: Mapped[str] = mapped_column(String(80))
    transport: Mapped[str] = mapped_column(String(24))
    auth_type: Mapped[str] = mapped_column(String(12))
    endpoint: Mapped[str] = mapped_column(String(2048))
    token_ciphertext: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool]
    required: Mapped[bool]
    max_inflight: Mapped[int]
    tools: Mapped[dict] = mapped_column(JSON)
