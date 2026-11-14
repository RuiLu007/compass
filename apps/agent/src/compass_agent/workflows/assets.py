"""Owner-scoped immutable scripts with versioned metadata."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    LargeBinary,
    String,
    Text,
    func,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, Session, mapped_column

from compass_agent.database import Base
from compass_agent.models.errors import ModelError
from compass_agent.schemas import (
    TenantScope,
    TrustedContext,
    WorkflowAssetList,
    WorkflowAssetPatch,
    WorkflowAssetView,
    WorkflowEntryContract,
)

type EntryContract = WorkflowEntryContract
DYNAMIC_CONTRACT = "agent_workflow@1"

MAX_SCRIPT_BYTES = 16384
UNIQUE_SCRIPT = "uq_workflow_asset_scope_script"


class WorkflowAsset(Base):
    __tablename__ = "workflow_asset"
    __table_args__ = (
        CheckConstraint("name = btrim(name) AND name <> ''", name="ck_workflow_asset_name").ddl_if(
            dialect="postgresql"
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_workflow_asset_status"),
        CheckConstraint(
            "definition_version = 1 AND runner_api_version = 1", name="ck_workflow_asset_versions"
        ),
        CheckConstraint(
            "entry_contract = btrim(entry_contract) AND entry_contract <> ''",
            name="ck_workflow_asset_contract",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            f"octet_length(script_source) BETWEEN 1 AND {MAX_SCRIPT_BYTES}",
            name="ck_workflow_asset_source",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint("octet_length(script_sha256) = 32", name="ck_workflow_asset_sha256").ddl_if(
            dialect="postgresql"
        ),
        CheckConstraint("metadata_revision >= 1", name="ck_workflow_asset_metadata_revision"),
        CheckConstraint(
            "(entry_contract = 'agent_workflow@1' AND description IS NOT NULL "
            "AND length(btrim(description)) > 0 AND parameters IS NOT NULL "
            "AND jsonb_typeof(parameters) = 'object') OR "
            "(entry_contract <> 'agent_workflow@1' AND description IS NULL AND parameters IS NULL)",
            name="ck_workflow_asset_tool_definition",
        ).ddl_if(dialect="postgresql"),
    )

    asset_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    workspace_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    definition_version: Mapped[int] = mapped_column(server_default=text("1"))
    runner_api_version: Mapped[int] = mapped_column(server_default=text("1"))
    entry_contract: Mapped[str] = mapped_column(String(64))
    script_source: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(500))
    parameters: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
    )
    script_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    metadata_revision: Mapped[int] = mapped_column(server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


SCRIPT_IDENTITY = (
    WorkflowAsset.tenant_id,
    WorkflowAsset.workspace_id,
    WorkflowAsset.owner_user_id,
    WorkflowAsset.runner_api_version,
    WorkflowAsset.entry_contract,
    WorkflowAsset.script_sha256,
)
NOT_DELETED = WorkflowAsset.deleted_at.is_(None)
Index(UNIQUE_SCRIPT, *SCRIPT_IDENTITY, unique=True, postgresql_where=NOT_DELETED)


Index(
    "ix_workflow_asset_scope_updated",
    WorkflowAsset.tenant_id,
    WorkflowAsset.workspace_id,
    WorkflowAsset.owner_user_id,
    WorkflowAsset.updated_at.desc(),
    WorkflowAsset.asset_id.desc(),
)


def scope_of(context: TrustedContext) -> tuple[UUID, UUID, UUID]:
    if not isinstance(context.scope, TenantScope) or context.purpose != "chat":
        raise ModelError("forbidden", 403)
    return context.scope.tenant_id, context.scope.workspace_id, context.actor_user_id


def filters(context: TrustedContext):
    tenant, workspace, owner = scope_of(context)
    return (
        WorkflowAsset.tenant_id == tenant,
        WorkflowAsset.workspace_id == workspace,
        WorkflowAsset.owner_user_id == owner,
    )


def normalize_source(source: str) -> str:
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip() or "\0" in source or len(source.encode()) > MAX_SCRIPT_BYTES:
        raise ValueError("invalid_script")
    return source


def valid_name(name: str) -> str:
    value = name.strip()
    if not value or len(value) > 80 or any(ord(char) < 32 for char in value):
        raise ModelError("validation_failed", 422)
    return value


def view(
    row: WorkflowAsset, *, include_source: bool = False, include_parameters: bool = False
) -> WorkflowAssetView:
    return WorkflowAssetView(
        assetId=row.asset_id,
        name=row.name,
        status=cast(Literal["active", "disabled"], row.status),
        definitionVersion=1,
        entryContract=cast(EntryContract, row.entry_contract),
        metadataRevision=row.metadata_revision,
        scriptSource=row.script_source if include_source else None,
        description=row.description,
        parameters=row.parameters if include_source or include_parameters else None,
    )


class WorkflowAssets:
    def __init__(self, engine: Engine):
        self.engine = engine

    def list(
        self,
        context: TrustedContext,
        *,
        cursor: str | None = None,
        active: bool = False,
        name: str | None = None,
    ) -> WorkflowAssetList:
        stmt = select(WorkflowAsset).where(*filters(context), NOT_DELETED)
        if cursor:
            try:
                updated, asset_id = cursor.split("|")
                point = datetime.fromisoformat(updated)
                if point.tzinfo is None:
                    raise ValueError
                stmt = stmt.where(
                    tuple_(WorkflowAsset.updated_at, WorkflowAsset.asset_id)
                    < tuple_(point, UUID(asset_id))
                )
            except ValueError:
                raise ModelError("validation_failed", 422) from None
        if active:
            stmt = stmt.where(WorkflowAsset.status == "active")
        if name:
            stmt = stmt.where(func.lower(WorkflowAsset.name) == name.casefold())
        with Session(self.engine) as session:
            rows = list(
                session.scalars(
                    stmt.order_by(
                        WorkflowAsset.updated_at.desc(), WorkflowAsset.asset_id.desc()
                    ).limit(21)
                )
            )
            return WorkflowAssetList(
                items=[view(row) for row in rows[:20]],
                nextCursor=f"{rows[19].updated_at.isoformat()}|{rows[19].asset_id}"
                if len(rows) > 20
                else None,
            )

    def get(
        self, context: TrustedContext, asset_id: UUID, *, active: bool = False
    ) -> WorkflowAssetView:
        stmt = select(WorkflowAsset).where(
            *filters(context), WorkflowAsset.asset_id == asset_id, NOT_DELETED
        )
        if active:
            stmt = stmt.where(WorkflowAsset.status == "active")
        with Session(self.engine) as session:
            row = session.scalar(stmt)
            if row is None:
                raise ModelError("not_found", 404)
            if active and sha256(row.script_source.encode()).digest() != row.script_sha256:
                raise ModelError("unavailable", 503)
            return view(row, include_source=True)

    def active_tools(self, context: TrustedContext, *, limit: int) -> list[WorkflowAssetView]:
        stmt = (
            select(WorkflowAsset)
            .where(
                *filters(context),
                NOT_DELETED,
                WorkflowAsset.status == "active",
                WorkflowAsset.entry_contract == DYNAMIC_CONTRACT,
            )
            .order_by(WorkflowAsset.created_at.desc(), WorkflowAsset.asset_id.desc())
            .limit(limit)
        )
        with Session(self.engine) as session:
            return [view(row, include_parameters=True) for row in session.scalars(stmt)]

    def save(
        self,
        context: TrustedContext,
        name: str,
        source: str,
        contract: EntryContract,
        *,
        description: str | None = None,
        parameters: dict | None = None,
    ) -> WorkflowAssetView:
        with Session(self.engine) as session, session.begin():
            return view(
                save_asset(session, context, name, source, contract, description, parameters)
            )

    def patch(
        self, context: TrustedContext, asset_id: UUID, command: WorkflowAssetPatch
    ) -> WorkflowAssetView:
        with Session(self.engine) as session, session.begin():
            scope = (*filters(context), WorkflowAsset.asset_id == asset_id, NOT_DELETED)
            row = session.scalar(
                update(WorkflowAsset)
                .where(
                    *scope,
                    WorkflowAsset.metadata_revision == command.metadata_revision,
                )
                .values(
                    name=valid_name(command.name),
                    status=command.status,
                    metadata_revision=WorkflowAsset.metadata_revision + 1,
                    updated_at=func.now(),
                )
                .returning(WorkflowAsset)
            )
            if row is None:
                if session.scalar(select(WorkflowAsset.asset_id).where(*scope)) is None:
                    raise ModelError("not_found", 404)
                raise ModelError("version_conflict", 409)
            return view(row, include_parameters=True)

    def delete(self, context: TrustedContext, asset_id: UUID, metadata_revision: int) -> None:
        with Session(self.engine) as session, session.begin():
            row = session.scalar(
                select(WorkflowAsset)
                .where(*filters(context), WorkflowAsset.asset_id == asset_id)
                .with_for_update()
            )
            if row is None:
                raise ModelError("not_found", 404)
            if row.deleted_at is not None:
                return
            if row.metadata_revision != metadata_revision:
                raise ModelError("version_conflict", 409)
            row.status = "disabled"
            row.deleted_at = row.updated_at = session.execute(select(func.now())).scalar_one()
            row.metadata_revision += 1


def save_asset(
    session: Session,
    context: TrustedContext,
    name: str,
    source: str,
    contract: EntryContract,
    description: str | None,
    parameters: dict | None,
) -> WorkflowAsset:
    tenant, workspace, owner = scope_of(context)
    digest = sha256(source.encode()).digest()
    stmt = (
        insert(WorkflowAsset)
        .values(
            asset_id=uuid4(),
            tenant_id=tenant,
            workspace_id=workspace,
            owner_user_id=owner,
            name=valid_name(name),
            script_source=source,
            entry_contract=contract,
            script_sha256=digest,
            description=description,
            parameters=parameters,
        )
        .on_conflict_do_nothing(index_elements=SCRIPT_IDENTITY, index_where=NOT_DELETED)
        .returning(WorkflowAsset)
    )
    existing = (
        select(WorkflowAsset)
        .where(
            *filters(context),
            NOT_DELETED,
            WorkflowAsset.script_sha256 == digest,
            WorkflowAsset.runner_api_version == 1,
            WorkflowAsset.entry_contract == contract,
        )
        .with_for_update()
    )
    while True:
        row = session.scalar(stmt) or session.scalar(existing)
        # A concurrent delete can remove the conflicting asset before the lookup.
        if row is None:
            continue
        if row.parameters != parameters:
            raise ModelError("workflow_parameters_conflict", 409)
        return row
