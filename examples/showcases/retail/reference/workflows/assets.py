"""Owner-scoped immutable scripts with versioned metadata."""

from datetime import datetime
from hashlib import sha256
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
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
)
from compass_agent.tools.builtin.run_dynamic_workflow import EntryContract

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
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "owner_user_id",
            "runner_api_version",
            "entry_contract",
            "script_sha256",
            name=UNIQUE_SCRIPT,
        ),
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
    script_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    metadata_revision: Mapped[int] = mapped_column(server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


def view(row: WorkflowAsset, *, include_source: bool = False) -> WorkflowAssetView:
    return WorkflowAssetView(
        assetId=row.asset_id,
        name=row.name,
        status=cast(Literal["active", "disabled"], row.status),
        definitionVersion=1,
        entryContract=cast(EntryContract, row.entry_contract),
        metadataRevision=row.metadata_revision,
        scriptSource=row.script_source if include_source else None,
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
        stmt = select(WorkflowAsset).where(*filters(context))
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
        stmt = select(WorkflowAsset).where(*filters(context), WorkflowAsset.asset_id == asset_id)
        if active:
            stmt = stmt.where(WorkflowAsset.status == "active")
        with Session(self.engine) as session:
            row = session.scalar(stmt)
            if row is None:
                raise ModelError("not_found", 404)
            if active and sha256(row.script_source.encode()).digest() != row.script_sha256:
                raise ModelError("unavailable", 503)
            return view(row, include_source=True)

    def save(
        self, context: TrustedContext, name: str, source: str, contract: EntryContract
    ) -> WorkflowAssetView:
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
            )
            .on_conflict_do_nothing(constraint=UNIQUE_SCRIPT)
            .returning(WorkflowAsset)
        )
        with Session(self.engine) as session, session.begin():
            row = session.scalar(stmt)
            if row is None:
                row = session.scalars(
                    select(WorkflowAsset).where(
                        *filters(context),
                        WorkflowAsset.script_sha256 == digest,
                        WorkflowAsset.runner_api_version == 1,
                        WorkflowAsset.entry_contract == contract,
                    )
                ).one()
            return view(row)

    def patch(
        self, context: TrustedContext, asset_id: UUID, command: WorkflowAssetPatch
    ) -> WorkflowAssetView:
        with Session(self.engine) as session, session.begin():
            scope = (*filters(context), WorkflowAsset.asset_id == asset_id)
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
            return view(row)
