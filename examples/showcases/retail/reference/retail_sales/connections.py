"""Workspace-owned connection records, credential sealing and initial import."""

import asyncio
import ssl
from uuid import UUID, uuid4

import psycopg
import pymysql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr, ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from compass_agent.models.errors import ModelError
from compass_agent.models.secrets import SecretStore
from compass_agent.schemas import (
    DataConnectionList,
    DataConnectionSave,
    DataConnectionView,
    RetailConnectionSave,
    RetailConnectionStatus,
    RetailConnectionTest,
    RetailConnectionView,
    TenantScope,
)
from compass_agent.settings import Settings
from compass_agent.tools.business.retail_sales.entities import DataConnection
from compass_agent.tools.business.retail_sales.handler import (
    RetailBinding,
    RetailIdentityMismatch,
    check_retail_identity,
    open_retail_connection,
    supports_bounded_cancel,
)

LOGIN_FIELDS = ("host", "port", "database_name", "role", "sslmode")
DEFAULT_RETAIL_NAME = "本地 Retail"


class RetailConnections:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.key = settings.compass_model_encryption_key
        self.retail_endpoints = self._endpoints(settings.compass_retail_allowed_endpoints)
        self.endpoints = self.retail_endpoints | self._endpoints(
            settings.compass_data_allowed_endpoints
        )

    @staticmethod
    def _endpoints(value: str) -> set[str]:
        return {endpoint.strip().lower() for endpoint in value.split(",") if endpoint.strip()}

    def _require_endpoint(self, value: DataConnection | DataConnectionSave) -> None:
        allowed = self.retail_endpoints if value.retail_tenant_id is not None else self.endpoints
        if f"{value.host.lower()}:{value.port}" not in allowed:
            raise ModelError("endpoint_not_allowed")

    @staticmethod
    def _query(scope: TenantScope):
        return select(DataConnection).where(
            DataConnection.workspace_id == scope.workspace_id,
            DataConnection.tenant_id == scope.tenant_id,
        )

    @staticmethod
    def _secret_purpose(scope: TenantScope, connection_id: UUID) -> str:
        if connection_id == scope.workspace_id:
            return f"retail_connection:{scope.tenant_id}:{scope.workspace_id}"
        return f"data_connection:{scope.tenant_id}:{scope.workspace_id}:{connection_id}"

    def _seal(self, scope: TenantScope, connection_id: UUID, password: SecretStr) -> str:
        raw = password.get_secret_value()
        if not raw or "\x00" in raw:
            raise ModelError("validation_failed")
        return SecretStore(self.key).seal(
            self._secret_purpose(scope, connection_id), {"password": raw}
        )

    def _password(self, scope: TenantScope, row: DataConnection) -> SecretStr:
        self._require_endpoint(row)
        secret = SecretStore(self.key).open(
            row.password_ciphertext, self._secret_purpose(scope, row.id)
        )
        password = secret.get("password")
        if not isinstance(password, str) or not password or "\x00" in password:
            raise ModelError("unavailable", 503)
        return SecretStr(password)

    def status(self, scope: TenantScope) -> RetailConnectionStatus:
        with Session(self.engine) as session:
            row = session.scalar(self._query(scope).where(DataConnection.id == scope.workspace_id))
            return RetailConnectionStatus(
                connection=RetailConnectionView.model_validate(row, from_attributes=True)
                if row
                else None
            )

    def list(self, scope: TenantScope) -> DataConnectionList:
        with Session(self.engine) as session:
            return DataConnectionList(
                items=[
                    DataConnectionView.model_validate(row, from_attributes=True)
                    for row in session.scalars(
                        self._query(scope).order_by(
                            (DataConnection.id == scope.workspace_id).desc(),
                            DataConnection.name,
                        )
                    )
                ]
            )

    def save(self, scope: TenantScope, command: RetailConnectionSave) -> RetailConnectionView:
        value = self.save_connection(
            scope,
            DataConnectionSave.model_validate(
                {**command.model_dump(), "name": DEFAULT_RETAIL_NAME, "engine": "postgresql"}
            ),
            scope.workspace_id if command.revision > 0 else None,
            preserve_name=True,
        )
        return RetailConnectionView.model_validate(value, from_attributes=True)

    def save_connection(
        self,
        scope: TenantScope,
        command: DataConnectionSave,
        connection_id: UUID | None,
        *,
        preserve_name: bool = False,
    ) -> DataConnectionView:
        self._require_endpoint(command)
        retail = command.retail_tenant_id is not None
        if command.engine == "mysql" and (
            retail or command.sslmode not in {"disable", "verify-full"}
        ):
            raise ModelError("validation_failed")
        if not retail and command.name == DEFAULT_RETAIL_NAME:
            raise ModelError("validation_failed")
        if retail:
            # Both API surfaces share the Retail validation contract and the same persisted row.
            try:
                RetailConnectionSave.model_validate(command.model_dump(exclude={"name", "engine"}))
            except ValidationError:
                raise ModelError("validation_failed") from None
        creating = connection_id is None
        if creating != (command.revision == 0):
            raise ModelError("validation_failed")
        target = connection_id or (scope.workspace_id if retail else uuid4())
        try:
            with Session(self.engine) as session, session.begin():
                row = session.scalar(self._query(scope).where(DataConnection.id == target))
                if row is None and not creating:
                    raise ModelError("not_found", 404)
                if retail != (target == scope.workspace_id):
                    raise ModelError("validation_failed")
                if command.revision != (row.revision if row else 0):
                    raise ModelError("version_conflict", 409)
                if row is not None and row.engine != command.engine:
                    raise ModelError("validation_failed")
                if command.password is not None:
                    ciphertext = self._seal(scope, target, command.password)
                elif row is None or any(
                    getattr(row, field) != getattr(command, field) for field in LOGIN_FIELDS
                ):
                    raise ModelError("validation_failed")
                else:
                    ciphertext = row.password_ciphertext

                values = command.model_dump(exclude={"password", "revision"})
                if row is not None and preserve_name:
                    values["name"] = row.name
                values.update(password_ciphertext=ciphertext, revision=command.revision + 1)
                if creating:
                    statement = self._insert(scope, target, values)
                else:
                    statement = (
                        update(DataConnection)
                        .where(
                            DataConnection.id == target,
                            DataConnection.tenant_id == scope.tenant_id,
                            DataConnection.workspace_id == scope.workspace_id,
                            DataConnection.revision == command.revision,
                        )
                        .values(**values)
                    )
                saved = session.scalar(
                    statement.returning(DataConnection).execution_options(populate_existing=True)
                )
                if saved is None:
                    raise ModelError("version_conflict", 409)
                return DataConnectionView.model_validate(saved, from_attributes=True)
        except IntegrityError as error:
            if isinstance(error.orig, psycopg.errors.UniqueViolation):
                raise ModelError("alias_conflict", 409) from None
            raise

    @staticmethod
    def _insert(scope: TenantScope, connection_id: UUID, values: dict):
        return (
            insert(DataConnection)
            .values(
                id=connection_id,
                tenant_id=scope.tenant_id,
                workspace_id=scope.workspace_id,
                **values,
            )
            .on_conflict_do_nothing(index_elements=[DataConnection.id])
        )

    def seed_default(self, settings: Settings) -> bool:
        legacy, dsn, error = settings.retail_run_configuration()
        if error:
            raise ModelError("validation_failed")
        if legacy is None or dsn is None:
            return False
        scope = TenantScope(
            kind="tenant", tenant_id=legacy.tenant_id, workspace_id=legacy.workspace_id
        )
        with Session(self.engine) as session, session.begin():
            if (
                session.scalar(self._query(scope).where(DataConnection.id == scope.workspace_id))
                is not None
            ):
                return False
            try:
                values = conninfo_to_dict(dsn.get_secret_value())
                if set(values) - {"host", "port", "dbname", "user", "password", "sslmode"}:
                    raise ValueError()
                legacy_command = RetailConnectionSave.model_validate(
                    {
                        "host": values["host"],
                        "port": int(str(values.get("port", "5432"))),
                        "database_name": values["dbname"],
                        "role": values["user"],
                        "sslmode": values.get("sslmode", "prefer"),
                        "retail_tenant_id": legacy.retail_tenant_id,
                        "revision": 0,
                        "password": values["password"],
                    }
                )
                command = DataConnectionSave.model_validate(
                    {
                        **legacy_command.model_dump(),
                        "name": DEFAULT_RETAIL_NAME,
                        "engine": "postgresql",
                    }
                )
            except (KeyError, ValueError, psycopg.Error, ValidationError):
                raise ModelError("validation_failed") from None
            if command.role != legacy.role or command.password is None:
                raise ModelError("validation_failed")
            self._require_endpoint(command)
            fields = command.model_dump(exclude={"password", "revision"})
            fields.update(
                password_ciphertext=self._seal(scope, scope.workspace_id, command.password),
                revision=1,
            )
            return (
                session.scalar(
                    self._insert(scope, scope.workspace_id, fields).returning(DataConnection.id)
                )
                is not None
            )

    def binding_for_run(self, scope: TenantScope) -> RetailBinding | None:
        if not supports_bounded_cancel():
            return None
        try:
            with Session(self.engine) as session:
                session.execute(text("SET LOCAL statement_timeout = '3s'"))
                row = session.scalar(
                    self._query(scope).where(DataConnection.id == scope.workspace_id)
                )
                if row is None:
                    return None
                view = DataConnectionView.model_validate(row, from_attributes=True)
                return retail_binding(scope, view, self._password(scope, row))
        except (ModelError, SQLAlchemyError, psycopg.Error):
            return None

    def test_target(self, scope: TenantScope) -> tuple[int, RetailBinding | None]:
        view, password = self.test_connection_target(scope, scope.workspace_id)
        return (
            view.revision,
            retail_binding(scope, view, password)
            if password is not None and supports_bounded_cancel()
            else None,
        )

    def test_connection_target(
        self, scope: TenantScope, connection_id: UUID
    ) -> tuple[DataConnectionView, SecretStr | None]:
        with Session(self.engine) as session:
            row = session.scalar(self._query(scope).where(DataConnection.id == connection_id))
            if row is None:
                raise ModelError("not_found", 404)
            try:
                password = self._password(scope, row)
            except ModelError:
                password = None
            return DataConnectionView.model_validate(row, from_attributes=True), password


def postgres_dsn(connection: DataConnectionView, password: SecretStr) -> str:
    return make_conninfo(
        host=connection.host,
        port=connection.port,
        dbname=connection.database_name,
        user=connection.role,
        password=password.get_secret_value(),
        sslmode=connection.sslmode,
    )


def retail_binding(
    scope: TenantScope, connection: DataConnectionView, password: SecretStr
) -> RetailBinding:
    assert connection.retail_tenant_id is not None
    return RetailBinding(
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        role=connection.role,
        retail_tenant_id=connection.retail_tenant_id,
        dsn=SecretStr(postgres_dsn(connection, password)),
    )


def probe_mysql(connection: DataConnectionView, password: SecretStr) -> bool:
    tls_disabled = connection.sslmode == "disable"
    with (
        pymysql.connect(
            host=connection.host,
            port=connection.port,
            user=connection.role,
            password=password.get_secret_value(),
            database=connection.database_name,
            connect_timeout=3,
            read_timeout=3,
            write_timeout=3,
            ssl_disabled=tls_disabled,
            ssl=None if tls_disabled else ssl.create_default_context(),
            local_infile=False,
            charset="utf8mb4",
        ) as client,
        client.cursor() as cursor,
    ):
        cursor.execute("SELECT 1")
        return cursor.fetchone() == (1,)


async def probe_data_connection(
    scope: TenantScope, connection: DataConnectionView, password: SecretStr | None
) -> RetailConnectionTest:
    revision = connection.revision
    if password is None:
        return RetailConnectionTest(revision=revision, status="unavailable")
    if connection.retail_tenant_id is not None:
        binding = retail_binding(scope, connection, password) if supports_bounded_cancel() else None
        return await probe_connection(revision, binding)
    try:
        async with asyncio.timeout(8):
            if connection.engine == "mysql":
                success = await asyncio.to_thread(probe_mysql, connection, password)
            else:
                async with await psycopg.AsyncConnection.connect(
                    postgres_dsn(connection, password), connect_timeout=3, autocommit=True
                ) as client:
                    cursor = await client.execute("SELECT 1")
                    success = await cursor.fetchone() == (1,)
    except (TimeoutError, OSError, psycopg.Error, pymysql.MySQLError):
        success = False
    return RetailConnectionTest(revision=revision, status="ok" if success else "connection_failed")


async def probe_connection(revision: int, binding: RetailBinding | None) -> RetailConnectionTest:
    if binding is None:
        return RetailConnectionTest(revision=revision, status="unavailable")
    try:
        async with asyncio.timeout(8):
            async with await open_retail_connection(binding, uuid4()) as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute("BEGIN READ ONLY")
                    await check_retail_identity(cursor, binding)
    except (
        RetailIdentityMismatch,
        psycopg.errors.InsufficientPrivilege,
        psycopg.errors.UndefinedTable,
        psycopg.errors.UndefinedColumn,
        psycopg.errors.InvalidSchemaName,
    ):
        return RetailConnectionTest(revision=revision, status="identity_mismatch")
    except (TimeoutError, psycopg.Error):
        return RetailConnectionTest(revision=revision, status="connection_failed")
    return RetailConnectionTest(revision=revision, status="ok")
