"""Short Agent/main transactions. Provider calls never run inside these units."""

import time
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from compass_agent.llm.capabilities import (
    capabilities,
    capability_summary,
    chat_supported,
    validate_input,
)
from compass_agent.llm.catalog import binding
from compass_agent.llm.contracts import (
    AdapterKind,
    ContinuationBinding,
    InvocationContext,
    ModelKind,
    ModelSpec,
)
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.policy import CallPolicy
from compass_agent.models import PreparedCall, RoutingGrant
from compass_agent.models.entities import ModelCall, ModelChange, Provider
from compass_agent.models.errors import ModelError
from compass_agent.models.secrets import SecretStore
from compass_agent.models.validation import validate_connection_params, validate_endpoint
from compass_agent.schemas import (
    AvailableModel,
    AvailableModels,
    CallRecordView,
    ExecutionSummary,
    GenerateInput,
    ModelChangePage,
    ModelChangeView,
    ModelUsage,
    ProviderCreate,
    ProviderPage,
    ProviderPatch,
    ProviderView,
    SavedProvider,
    TenantScope,
    TestSummary,
    TrustedContext,
)


def now():
    return datetime.now(UTC)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ModelRepository:
    def __init__(self, engine, secrets: SecretStore, allowed_origins: str):
        self.engine = engine
        self.secrets = secrets
        self.allowed_origins = allowed_origins

    def saved(self, change):
        return SavedProvider(
            provider_id=change.provider_id,
            version=change.after_version,
            operation_id=change.operation_id,
        )

    def operation(self, operation_id: UUID):
        with Session(self.engine) as session:
            change = session.get(ModelChange, operation_id)
            if change is None:
                raise ModelError("not_found", 404)
            return self.saved(change)

    def save(
        self,
        command: ProviderCreate | ProviderPatch,
        context: TrustedContext,
        provider_id: UUID | None = None,
    ):
        if command.connection_params is not None:
            validate_connection_params(command.connection_params, self.allowed_origins)
        fingerprint = self.secrets.fingerprint(
            {"provider_id": str(provider_id), "command": command.model_dump(mode="json")}
        )
        try:
            with Session(self.engine) as session, session.begin():
                previous = session.get(ModelChange, command.operation_id)
                if previous is not None:
                    if (
                        previous.actor_user_id != context.actor_user_id
                        or previous.request_mac != fingerprint
                    ):
                        raise ModelError("operation_conflict", 409)
                    return self.saved(previous)
                if provider_id is None:
                    if not isinstance(command, ProviderCreate):
                        raise ModelError("validation_failed")
                    row = Provider(
                        id=uuid4(),
                        version=1,
                        binding_version=1,
                        created_at=now(),
                        updated_at=now(),
                        updated_by=context.actor_user_id,
                        enabled=False,
                        chat_visible=False,
                        base_url="",
                    )
                    old = None
                    before = None
                else:
                    row = session.scalar(
                        select(Provider).where(Provider.id == provider_id).with_for_update()
                    )
                    if row is None:
                        raise ModelError("not_found", 404)
                    # A concurrent replay may have committed while this UPDATE lock waited.
                    previous = session.get(ModelChange, command.operation_id)
                    if previous is not None:
                        if (
                            previous.actor_user_id != context.actor_user_id
                            or previous.request_mac != fingerprint
                        ):
                            raise ModelError("operation_conflict", 409)
                        return self.saved(previous)
                    if (
                        not isinstance(command, ProviderPatch)
                        or row.version != command.expected_version
                    ):
                        raise ModelError("version_conflict", 409, row.version)
                    before = row.version
                    old = (row.catalog_id, row.api_model, row.base_url)
                    row.version += 1
                values = command.model_dump(
                    exclude_unset=True,
                    exclude={"operation_id", "expected_version", "api_key", "connection_params"},
                )
                for key, value in values.items():
                    if value is not None:
                        setattr(row, key, value.strip() if isinstance(value, str) else value)
                if not row.alias or not row.api_model or not row.catalog_id:
                    raise ModelError("validation_failed")
                # Resolve only on new bindings; a removed catalog entry remains executable.
                if old is None or (row.catalog_id, row.api_model) != old[:2]:
                    row.vendor, row.kind, row.adapter, row.sdk_model = binding(
                        row.catalog_id, row.api_model
                    )
                row.base_url = validate_endpoint(row.base_url, row.adapter, self.allowed_origins)
                if row.chat_visible and not chat_supported(row):
                    raise ModelError("chat_model_not_supported")
                if (
                    old is None
                    or command.api_key is not None
                    or command.connection_params is not None
                    or old[:2] != (row.catalog_id, row.api_model)
                ):
                    previous_key, previous_params = (
                        ("", {})
                        if old is None
                        else self.secrets.open_connection(row.id, row.api_key_ciphertext)
                    )
                    params = validate_connection_params(
                        command.connection_params
                        if command.connection_params is not None
                        else previous_params,
                        self.allowed_origins,
                    )
                    key = command.api_key if command.api_key is not None else previous_key
                    if row.kind == "decision" and (not key.strip() or params):
                        raise ModelError("validation_failed")
                    row.api_key_ciphertext = self.secrets.seal_key(
                        row.id,
                        key,
                        params,
                    )
                if not row.api_key_ciphertext:
                    raise ModelError("validation_failed")
                if (
                    old is None
                    or old != (row.catalog_id, row.api_model, row.base_url)
                    or command.api_key is not None
                    or command.connection_params is not None
                ):
                    row.binding_version = row.version
                row.updated_at = now()
                row.updated_by = context.actor_user_id
                session.add(row)
                session.flush()
                fields = sorted(
                    k
                    for k in command.model_fields_set
                    if k not in {"operation_id", "expected_version"}
                    and (
                        k not in {"api_key", "connection_params"} or getattr(command, k) is not None
                    )
                )
                change = ModelChange(
                    operation_id=command.operation_id,
                    provider_id=row.id,
                    actor_user_id=context.actor_user_id,
                    request_mac=fingerprint,
                    before_version=before,
                    after_version=row.version,
                    changed_fields=fields,
                    created_at=now(),
                )
                session.add(change)
                session.flush()
                return self.saved(change)
        except RuntimeFailure as exc:
            raise ModelError(exc.error.code) from exc
        except IntegrityError as exc:
            with Session(self.engine) as session:
                previous = session.get(ModelChange, command.operation_id)
                if previous:
                    if (
                        previous.actor_user_id == context.actor_user_id
                        and previous.request_mac == fingerprint
                    ):
                        return self.saved(previous)
                    raise ModelError("operation_conflict", 409) from exc
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_model_provider_alias" or (
                self.engine.dialect.name == "sqlite" and "uq_model_provider_alias" in str(exc.orig)
            ):
                raise ModelError("alias_conflict", 409) from exc
            raise ModelError("unavailable", 503) from exc

    def view(self, row, session):
        latest = session.scalar(
            select(ModelCall)
            .where(ModelCall.provider_id == row.id, ModelCall.purpose == "test")
            .order_by(ModelCall.started_at.desc())
            .limit(1)
        )
        summary = (
            None
            if latest is None
            else TestSummary(
                call_id=latest.call_id,
                status=self.effective_status(latest),
                binding_version=latest.binding_version,
                started_at=latest.started_at,
                capabilities=latest.capability_summary,
                stale=latest.binding_version != row.binding_version,
            )
        )
        return ProviderView(
            id=row.id,
            catalog_id=row.catalog_id,
            vendor=row.vendor,
            api_model=row.api_model,
            alias=row.alias,
            kind=row.kind,
            base_url=row.base_url,
            has_api_key=bool(self.secrets.open_key(row.id, row.api_key_ciphertext)),
            has_connection_params=bool(
                self.secrets.open_connection(row.id, row.api_key_ciphertext)[1]
            ),
            enabled=row.enabled,
            chat_visible=row.chat_visible,
            chat_supported=chat_supported(row),
            version=row.version,
            binding_version=row.binding_version,
            capabilities=capabilities(row.kind, row.vendor, row.api_model),
            latest_test=summary,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get(self, provider_id):
        with Session(self.engine) as session:
            row = session.get(Provider, provider_id)
            if row is None:
                raise ModelError("not_found", 404)
            return self.view(row, session)

    def list(self, q, kind, page, page_size):
        with Session(self.engine) as session:
            filters = []
            if q:
                filters.append(
                    Provider.alias.ilike(
                        "%" + q.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"
                    )
                    | Provider.api_model.ilike(
                        "%" + q.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"
                    )
                )
            if kind:
                filters.append(Provider.kind == kind)
            total = session.scalar(select(func.count()).select_from(Provider).where(*filters)) or 0
            rows = session.scalars(
                select(Provider)
                .where(*filters)
                .order_by(Provider.updated_at.desc(), Provider.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return ProviderPage(
                items=[self.view(row, session) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    def available(self):
        with Session(self.engine) as session:
            rows = session.scalars(
                select(Provider)
                .where(Provider.enabled, Provider.chat_visible)
                .order_by(Provider.alias, Provider.id)
            )
            return AvailableModels(
                items=[
                    AvailableModel(
                        id=row.id,
                        alias=row.alias,
                        api_model=row.api_model,
                        kind="llm",
                        capabilities=capabilities(row.kind, row.vendor, row.api_model),
                    )
                    for row in rows
                    if chat_supported(row)
                ]
            )

    def routing_provider(self, role, configured_id=None):
        """Resolve a code-configured role; return identity only, never credentials."""
        models = {"flash": {"deepseek-flash", "deepseek-v4-flash"}, "pro": {"deepseek-v4-pro"}}
        with Session(self.engine) as session:
            query = select(Provider).where(Provider.enabled)
            if configured_id:
                query = query.where(Provider.id == configured_id)
            elif role == "jev":
                query = query.where(Provider.kind == "decision")
            else:
                query = query.where(Provider.api_model.in_(models[role]), Provider.kind == "llm")
            row = session.scalar(query.order_by(Provider.updated_at.desc(), Provider.id).limit(1))
            if row is None or (row.kind == "decision") != (role == "jev"):
                raise ModelError("route_binding_missing", 503)
            return row.id, row.api_model

    def changes(self, provider_id, page, page_size):
        with Session(self.engine) as session:
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(ModelChange)
                    .where(ModelChange.provider_id == provider_id)
                )
                or 0
            )
            rows = session.scalars(
                select(ModelChange)
                .where(ModelChange.provider_id == provider_id)
                .order_by(ModelChange.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return ModelChangePage(
                items=[
                    ModelChangeView(
                        operation_id=r.operation_id,
                        provider_id=r.provider_id,
                        actor_user_id=r.actor_user_id,
                        before_version=r.before_version,
                        after_version=r.after_version,
                        changed_fields=r.changed_fields,
                        created_at=r.created_at,
                    )
                    for r in rows
                ],
                total=total,
                page=page,
                page_size=page_size,
            )

    def begin(
        self,
        call_id,
        provider_id,
        request,
        context,
        policy=None,
        started_at=None,
        grant=None,
        expected_binding_version=None,
    ):
        started = started_at or now()
        mono = time.monotonic()
        policy = policy or CallPolicy(mono, mono + 180)
        try:
            with Session(self.engine) as session, session.begin():
                row = session.scalar(
                    select(Provider).where(Provider.id == provider_id).with_for_update(read=True)
                )
                if row is None:
                    raise ModelError("not_found", 404)
                if (
                    expected_binding_version is not None
                    and row.binding_version != expected_binding_version
                ):
                    raise ModelError("model_unavailable", 409)
                if grant is not None:
                    if (
                        not isinstance(grant, RoutingGrant)
                        or grant.actor_user_id != context.actor_user_id
                        or grant.scope != context.scope
                        or grant.call_id != call_id
                        or grant.provider_id != provider_id
                        or grant.kind != request.kind
                        or context.purpose != "chat"
                        or not isinstance(context.scope, TenantScope)
                        or not row.enabled
                        or (row.kind == "decision") != (request.kind == "decision")
                    ):
                        raise ModelError("forbidden", 403)
                elif context.purpose == "chat":
                    if not chat_supported(row) or not isinstance(request, GenerateInput):
                        raise ModelError("chat_model_not_supported")
                    if (
                        not isinstance(context.scope, TenantScope)
                        or not row.enabled
                        or not row.chat_visible
                    ):
                        raise ModelError("model_unavailable", 409)
                elif context.purpose != "test" or context.scope.kind != "platform":
                    raise ModelError("forbidden", 403)
                validate_input(
                    row, request, "diagnostic" if context.purpose == "test" else "verified"
                )
                validate_endpoint(row.base_url, row.adapter, self.allowed_origins)
                spec = ModelSpec(
                    row.id,
                    row.version,
                    row.binding_version,
                    row.vendor,
                    cast(ModelKind, row.kind),
                    cast(AdapterKind, row.adapter),
                    row.api_model,
                    row.sdk_model,
                    row.base_url,
                )
                scope = context.scope
                session.add(
                    ModelCall(
                        call_id=call_id,
                        provider_id=row.id,
                        config_version=row.version,
                        binding_version=row.binding_version,
                        requested_model=row.api_model,
                        purpose=context.purpose,
                        actor_user_id=context.actor_user_id,
                        tenant_id=scope.tenant_id if isinstance(scope, TenantScope) else None,
                        workspace_id=scope.workspace_id if isinstance(scope, TenantScope) else None,
                        capability_summary=capability_summary(request),
                        status="running",
                        started_at=started,
                        deadline_at=started
                        + timedelta(seconds=policy.deadline_monotonic - policy.started_monotonic),
                        upstream_outcome_unknown=False,
                    )
                )
                session.flush()
                prepared = PreparedCall(
                    spec,
                    InvocationContext(
                        call_id,
                        "diagnostic" if context.purpose == "test" else "verified",
                        ContinuationBinding(
                            actor=context.actor_user_id,
                            scope=context.scope.model_copy(deep=True),
                            provider=row.id,
                            binding_version=row.binding_version,
                        ),
                    ),
                    policy,
                    started,
                    started
                    + timedelta(seconds=policy.deadline_monotonic - policy.started_monotonic),
                    row.api_key_ciphertext,
                )
            return prepared
        except RuntimeFailure as exc:
            raise ModelError(exc.error.code) from exc
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "model_call_pkey" or (
                self.engine.dialect.name == "sqlite" and "model_call.call_id" in str(exc.orig)
            ):
                raise ModelError("call_already_exists", 409) from exc
            raise ModelError("unavailable", 503) from exc

    def finish(self, result):
        values = dict(
            execution=result.execution.model_dump(mode="json") if result.execution else None,
            status=result.status,
            response_model=result.response_model,
            duration_ms=result.duration_ms,
            usage=result.usage.model_dump(),
            error_code=result.error_code,
            upstream_request_id=result.upstream_request_id,
            upstream_outcome_unknown=result.upstream_outcome_unknown,
        )
        with Session(self.engine) as session, session.begin():
            if self.engine.dialect.name == "postgresql":
                session.execute(text("SET LOCAL statement_timeout = '1800ms'"))
            changed = session.scalar(
                update(ModelCall)
                .where(ModelCall.call_id == result.call_id, ModelCall.status == "running")
                .values(**values, finished_at=now())
                .returning(ModelCall.call_id)
            )
            if changed is None:
                row = session.get(ModelCall, result.call_id)
                return row is not None and all(
                    getattr(row, key) == value for key, value in values.items()
                )
        return True

    @staticmethod
    def effective_status(row):
        return (
            "incomplete"
            if row.status == "running" and aware(row.deadline_at) < now()
            else row.status
        )

    def call(self, call_id, context):
        with Session(self.engine) as session:
            row = session.get(ModelCall, call_id)
            scope = context.scope
            allowed = (
                row is not None
                and row.actor_user_id == context.actor_user_id
                and row.purpose == context.purpose
            )
            if row is not None:
                allowed = allowed and (
                    (scope.kind == "platform" and row.tenant_id is None)
                    or (
                        isinstance(scope, TenantScope)
                        and row.tenant_id == scope.tenant_id
                        and row.workspace_id == scope.workspace_id
                    )
                )
            if not allowed or row is None:
                raise ModelError("not_found", 404)
            status = self.effective_status(row)
            return CallRecordView(
                execution=ExecutionSummary.model_validate(row.execution)
                if row.execution is not None
                else None,
                call_id=row.call_id,
                provider_id=row.provider_id,
                config_version=row.config_version,
                binding_version=row.binding_version,
                purpose="test" if row.purpose == "test" else "chat",
                status=status,
                requested_model=row.requested_model,
                response_model=row.response_model,
                started_at=row.started_at,
                finished_at=row.finished_at,
                duration_ms=row.duration_ms,
                usage=ModelUsage.model_validate(row.usage or {}),
                error_code="stream_interrupted" if status != row.status else row.error_code,
                upstream_outcome_unknown=row.upstream_outcome_unknown or status != row.status,
            )
