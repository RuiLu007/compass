"""Full-owner scoping and revision fencing for personal MCP connections."""

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from compass_agent.models.errors import ModelError
from compass_agent.models.secrets import SecretStore
from compass_agent.schemas import (
    McpConnectionSave,
    McpConnectionView,
    McpToolSelection,
    TrustedContext,
)
from compass_agent.tools.mcp.entities import McpConnection

Owner = tuple[UUID, UUID, UUID]


def owner_key(context: TrustedContext) -> Owner:
    if context.scope.kind != "tenant":
        raise ModelError("forbidden", 403)
    return context.scope.tenant_id, context.scope.workspace_id, context.actor_user_id


@dataclass(frozen=True)
class ConnectionConfig:
    id: UUID
    owner: Owner
    revision: int
    name: str
    endpoint: str
    token: SecretStr = field(repr=False)
    required: bool
    max_inflight: int
    tool_policies: tuple[McpToolSelection, ...]
    selected_tools: Literal["all"] | tuple[str, ...] = "all"


class McpConfigStore(Protocol):
    def load(self, owner: Owner) -> tuple[ConnectionConfig, ...]: ...
    def current(self, config: ConnectionConfig) -> bool: ...


class McpRepository:
    def __init__(self, engine: Engine, encryption_key: str):
        self.engine, self.encryption_key = engine, encryption_key

    @property
    def secrets(self) -> SecretStore:
        return SecretStore(self.encryption_key)

    @staticmethod
    def query(owner: Owner):
        return select(McpConnection).where(
            McpConnection.tenant_id == owner[0],
            McpConnection.workspace_id == owner[1],
            McpConnection.actor_user_id == owner[2],
        )

    @staticmethod
    def purpose(owner: Owner, identifier: UUID):
        return "mcp:" + ":".join(map(str, (*owner, identifier)))

    @staticmethod
    def view(row: McpConnection) -> McpConnectionView:
        return McpConnectionView(
            id=row.id,
            revision=row.revision,
            name=row.name,
            endpoint=row.endpoint,
            has_token=bool(row.token_ciphertext),
            enabled=row.enabled,
            required=row.required,
            max_inflight=row.max_inflight,
            selected_tools=row.tools["selected_tools"],
            tool_policies=[McpToolSelection.model_validate(t) for t in row.tools["tool_policies"]],
        )

    def list(self, owner: Owner) -> list[McpConnectionView]:
        with Session(self.engine) as session:
            return [
                self.view(row)
                for row in session.scalars(self.query(owner).order_by(McpConnection.id))
            ]

    def save(
        self, owner: Owner, command: McpConnectionSave, identifier: UUID | None
    ) -> McpConnectionView:
        if (identifier is None) != (command.revision == 0):
            raise ModelError("validation_failed")
        try:
            with Session(self.engine) as session, session.begin():
                # Serialize owner configuration changes and the connection cap across replicas.
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "mcp-owner:" + ":".join(map(str, owner))},
                )
                rows = list(session.scalars(self.query(owner).with_for_update()))
                row = next((r for r in rows if r.id == identifier), None)
                if identifier is not None and row is None:
                    raise ModelError("not_found", 404)
                if row and row.revision != command.revision:
                    raise ModelError("version_conflict", 409)
                if row is None:
                    if len(rows) >= 4:
                        raise ModelError("validation_failed")
                    row = McpConnection(
                        id=uuid4(),
                        tenant_id=owner[0],
                        workspace_id=owner[1],
                        actor_user_id=owner[2],
                        revision=0,
                        token_ciphertext="",
                    )
                    session.add(row)
                if command.token is not None:
                    token = command.token.get_secret_value()
                    if len(token.encode()) > 8192 or any(ord(c) < 32 for c in token):
                        raise ModelError("validation_failed")
                    row.token_ciphertext = (
                        self.secrets.seal(self.purpose(owner, row.id), {"token": token})
                        if token
                        else ""
                    )
                elif row.revision and row.endpoint != command.endpoint and row.token_ciphertext:
                    raise ModelError("validation_failed")
                for field_name in ("name", "endpoint", "enabled", "required", "max_inflight"):
                    setattr(row, field_name, getattr(command, field_name))
                row.transport = "streamable_http"
                row.auth_type = "bearer" if row.token_ciphertext else "none"
                row.tools = {
                    "selected_tools": command.selected_tools,
                    "tool_policies": [tool.model_dump() for tool in command.tool_policies],
                }
                row.revision += 1
                session.flush()
                return self.view(row)
        except IntegrityError:
            raise ModelError("alias_conflict", 409) from None

    def delete(self, owner: Owner, identifier: UUID, revision: int) -> None:
        with Session(self.engine) as session, session.begin():
            row = session.scalar(
                self.query(owner).where(McpConnection.id == identifier).with_for_update()
            )
            if row is None:
                raise ModelError("not_found", 404)
            if row.revision != revision:
                raise ModelError("version_conflict", 409)
            session.execute(delete(McpConnection).where(McpConnection.id == row.id))

    def load(self, owner: Owner, identifier: UUID | None = None) -> tuple[ConnectionConfig, ...]:
        with Session(self.engine) as session:
            query = self.query(owner)
            query = (
                query.where(McpConnection.id == identifier)
                if identifier
                else query.where(McpConnection.enabled.is_(True))
            )
            values = []
            for row in session.scalars(query.order_by(McpConnection.id)):
                secret = (
                    self.secrets.open(row.token_ciphertext, self.purpose(owner, row.id))
                    if row.token_ciphertext
                    else {"token": ""}
                )
                values.append(
                    ConnectionConfig(
                        row.id,
                        owner,
                        row.revision,
                        row.name,
                        row.endpoint,
                        SecretStr(secret["token"]),
                        row.required,
                        row.max_inflight,
                        tuple(
                            McpToolSelection.model_validate(t) for t in row.tools["tool_policies"]
                        ),
                        "all"
                        if row.tools["selected_tools"] == "all"
                        else tuple(row.tools["selected_tools"]),
                    )
                )
            if identifier and not values:
                raise ModelError("not_found", 404)
            return tuple(values)

    def current(self, config: ConnectionConfig) -> bool:
        with Session(self.engine) as session:
            return (
                session.scalar(
                    self.query(config.owner).where(
                        McpConnection.id == config.id,
                        McpConnection.revision == config.revision,
                        McpConnection.enabled.is_(True),
                    )
                )
                is not None
            )
