"""One plan management service for the HTTP and approved Chat entry points."""

import asyncio
import json
from uuid import uuid4

from sqlalchemy import select, tuple_, update

from compass_agent.models.errors import ModelError
from compass_agent.routing.service import RoutingService
from compass_agent.runtime.entities import AgentRun, AgentSession
from compass_agent.scheduler.cron import Cron
from compass_agent.scheduler.entities import AgentSchedule, AgentScheduleTrigger
from compass_agent.schemas import (
    ScheduleAutoRouting,
    ScheduleCreate,
    ScheduleDelete,
    ScheduleFields,
    ScheduleInput,
    SchedulePage,
    SchedulePreviewResult,
    ScheduleUpdate,
    ScheduleView,
    ScheduleWriteReceipt,
    TriggerPage,
    TriggerView,
)

PlanCommand = ScheduleCreate | ScheduleUpdate | ScheduleDelete
OPEN_DELIVERIES = ("pending", "dispatching", "retry_wait", "reconcile_pending")
PAGE_SIZE = 20
EXECUTION_FIELDS = {"question", "selection", "workflow_asset_ref", "constraints"}


def ownership(context):
    if context.scope.kind != "tenant":
        raise ModelError("forbidden", 403)
    return (
        AgentSchedule.tenant_id == context.scope.tenant_id,
        AgentSchedule.workspace_id == context.scope.workspace_id,
        AgentSchedule.owner_user_id == context.actor_user_id,
    )


def plan_input(store, plan):
    return store.private.open(plan.id, "schedule", plan.input_ciphertext)


def plan_receipt(plan):
    return ScheduleWriteReceipt(
        id=plan.id, revision=plan.revision, status=plan.status, nextFireAt=plan.next_fire_at
    )


def safe_run_reason(code):
    from compass_agent.models.errors import MESSAGES

    if code is None:
        return None
    if code in MESSAGES or code in {
        "queued",
        "user_paused",
        "user_canceled",
        "execution_unknown",
        "cleanup_unconfirmed",
        "process_interrupted",
        "lease_expired",
        "run_budget_exhausted",
        "budget_exhausted",
        "tool_not_authorized",
        "tool_unavailable",
        "workflow_unavailable",
        "workspace_tool_unavailable",
        "timeout",
        "deadline",
        "run_timeout",
        "generation_budget_exhausted",
        "budget_policy_mismatch",
    }:
        return code
    return "run_execution_issue"


def trigger_view(trigger, session_id=None):
    return TriggerView(
        id=trigger.id,
        scheduleId=trigger.schedule_id,
        scheduleRevision=trigger.schedule_revision,
        scheduledAt=trigger.scheduled_at,
        detectedAt=trigger.detected_at,
        coalescedFromAt=trigger.coalesced_from_at,
        deliveryStatus=trigger.delivery_status,
        reasonCode=trigger.reason_code,
        deliveryAttempts=trigger.delivery_attempts,
        nextAttemptAt=trigger.next_attempt_at,
        runId=trigger.run_id if session_id else None,
        sessionId=session_id,
        runStatus=trigger.observed_run_status,
        runReasonCode=safe_run_reason(trigger.observed_reason_code),
        observedAt=trigger.observed_at,
        finishedAt=trigger.finished_at,
    )


def clear_delivery(trigger):
    trigger.delivery_token = trigger.delivery_generation = trigger.delivery_expires_at = None
    trigger.next_attempt_at = None


class ScheduleService:
    def __init__(self, store, access, models, settings):
        self.store, self.access, self.models, self.settings = store, access, models, settings

    async def read_access(self, context):
        await self.access.check(context, purpose="schedule_manage")

    async def can_open_runs(self, context):
        try:
            await self.access.check(context.model_copy(update={"purpose": "chat"}))
        except ModelError as error:
            if error.status == 403:
                return False
            raise
        return True

    @staticmethod
    def owned(tx, context, schedule_id, *, lock=False):
        query = select(AgentSchedule).where(AgentSchedule.id == schedule_id, *ownership(context))
        row = tx.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise ModelError("schedule_not_found", 404)
        return row

    @staticmethod
    def trigger_query(with_session):
        query = select(AgentScheduleTrigger)
        if with_session:
            query = (
                query.add_columns(AgentSession.id)
                .outerjoin(AgentRun, AgentScheduleTrigger.run_id == AgentRun.id)
                .outerjoin(
                    AgentSession,
                    (AgentSession.id == AgentRun.session_id) & AgentSession.deleted_at.is_(None),
                )
            )
        return query

    def view(self, plan, latest):
        return ScheduleView(
            **plan_receipt(plan).model_dump(),
            **ScheduleInput.model_validate_json(
                json.dumps(plan_input(self.store, plan))
            ).model_dump(),
            name=plan.name,
            cron=plan.cron,
            timezone=plan.timezone,
            updatedAt=plan.updated_at,
            latestTrigger=latest,
        )

    async def preview(self, context, rule):
        await self.read_access(context)
        cron = Cron.parse(rule.cron, rule.timezone)
        warning = None
        auto_routing = None
        if rule.selection:
            await self.access.check(context, purpose="schedule_run")
            if rule.selection.mode == "auto":
                routing = RoutingService(self.models, self.settings)
                flash, pro = await asyncio.gather(
                    self.route_available(routing, "flash"), self.route_available(routing, "pro")
                )
                auto_routing = ScheduleAutoRouting(flash=flash, pro=pro)
            elif not await self.validate_selection(rule.selection):
                warning = "capability_not_supported"
        return SchedulePreviewResult(
            times=await asyncio.to_thread(self._preview, cron),
            warningCode=warning,
            autoRouting=auto_routing,
        )

    @staticmethod
    async def route_available(routing, role):
        try:
            await routing.provider(role)
        except ModelError as error:
            if error.code != "route_binding_missing":
                raise
            return False
        return True

    def _preview(self, cron):
        with self.store.transaction() as tx:
            return cron.preview(self.store.scheduler.clock(tx))

    async def list(self, context, q=None, status=None, cursor=None):
        await self.read_access(context)
        return await asyncio.to_thread(
            self._list, context, q, status, cursor, await self.can_open_runs(context)
        )

    def _list(self, context, q, status, cursor, with_session):
        with self.store.transaction() as tx:
            query = select(AgentSchedule).where(*ownership(context))
            if status:
                query = query.where(AgentSchedule.status == status)
            else:
                query = query.where(AgentSchedule.status != "deleted")
            if q and q.strip():
                query = query.where(AgentSchedule.name.icontains(q.strip(), autoescape=True))
            if cursor:
                query = query.where(AgentSchedule.id > cursor)
            plans = tx.scalars(query.order_by(AgentSchedule.id).limit(PAGE_SIZE + 1)).all()
            page = plans[:PAGE_SIZE]
            trigger = AgentScheduleTrigger
            latest = {
                row[0].schedule_id: trigger_view(*row)
                for row in tx.execute(
                    self.trigger_query(with_session)
                    .where(trigger.schedule_id.in_([p.id for p in page]))
                    .distinct(trigger.schedule_id)
                    .order_by(trigger.schedule_id, trigger.scheduled_at.desc(), trigger.id.desc())
                )
            }
            return SchedulePage(
                items=[self.view(p, latest.get(p.id)) for p in page],
                nextCursor=str(page[-1].id) if len(plans) > PAGE_SIZE else None,
            )

    async def get(self, context, schedule_id):
        await self.read_access(context)
        return await asyncio.to_thread(
            self._get, context, schedule_id, await self.can_open_runs(context)
        )

    def _get(self, context, schedule_id, with_session):
        with self.store.transaction() as tx:
            plan = self.owned(tx, context, schedule_id)
            trigger = AgentScheduleTrigger
            latest = tx.execute(
                self.trigger_query(with_session)
                .where(trigger.schedule_id == schedule_id)
                .order_by(trigger.scheduled_at.desc(), trigger.id.desc())
                .limit(1)
            ).first()
            return self.view(plan, trigger_view(*latest) if latest else None)

    async def history(self, context, schedule_id, cursor=None):
        await self.read_access(context)
        return await asyncio.to_thread(
            self._history, context, schedule_id, cursor, await self.can_open_runs(context)
        )

    def _history(self, context, schedule_id, cursor, with_session):
        with self.store.transaction() as tx:
            self.owned(tx, context, schedule_id)
            trigger = AgentScheduleTrigger
            query = self.trigger_query(with_session).where(trigger.schedule_id == schedule_id)
            if cursor:
                previous = tx.get(trigger, cursor)
                if previous is None or previous.schedule_id != schedule_id:
                    raise ModelError("schedule_invalid_cursor", 422)
                query = query.where(
                    tuple_(trigger.scheduled_at, trigger.id) < (previous.scheduled_at, cursor)
                )
            rows = tx.execute(
                query.order_by(trigger.scheduled_at.desc(), trigger.id.desc()).limit(PAGE_SIZE + 1)
            ).all()
            page = rows[:PAGE_SIZE]
            return TriggerPage(
                items=[trigger_view(*row) for row in page],
                nextCursor=str(page[-1][0].id) if len(rows) > PAGE_SIZE else None,
            )

    async def validate_model(self, fields):
        if fields.workflow_asset_ref is not None:
            raise ModelError("workflow_unavailable", 422)
        return await self.validate_selection(fields.selection)

    async def validate_selection(self, selection):
        if selection.mode == "auto":
            return True
        model = await asyncio.to_thread(self.models.repository.get, selection.provider_id)
        if not (model.enabled and model.chat_visible and model.chat_supported):
            raise ModelError("model_unavailable", 422)
        return model.capabilities.tools.state == "supported"

    @staticmethod
    def command_payload(schedule_id, command):
        return {
            "kind": "schedule",
            "scheduleId": str(schedule_id) if schedule_id else None,
            **command.model_dump(mode="json", by_alias=True, exclude_unset=True),
        }

    def previous(self, tx, context, schedule_id, command):
        return self.store.command(
            tx, context, command.request_id, self.command_payload(schedule_id, command)
        )

    @staticmethod
    def fields(store, plan, command):
        if isinstance(command, ScheduleCreate):
            result = ScheduleFields.model_validate(command.model_dump(exclude={"request_id"}))
        elif isinstance(command, ScheduleUpdate) and plan is not None:
            fields = {
                **plan_input(store, plan),
                "name": plan.name,
                "cron": plan.cron,
                "timezone": plan.timezone,
                "enabled": plan.status == "enabled",
                **command.patch.model_dump(by_alias=True, exclude_unset=True),
            }
            result = ScheduleFields.model_validate(fields)
        else:
            return None
        name, question = result.name.strip(), result.question.strip()
        if not name or not question or (result.workflow_asset_ref and result.constraints):
            raise ModelError("validation_failed", 422)
        return result.model_copy(
            update={"name": name, "question": question, "cron": " ".join(result.cron.split())}
        )

    @staticmethod
    def check_revision(plan, command):
        if plan is None:
            return
        if plan.revision != command.expected_revision:
            raise ModelError("schedule_revision_changed", 409, plan.revision)
        if plan.status == "deleted":
            raise ModelError("schedule_not_found", 404)

    @staticmethod
    def changes_input(command):
        return bool(
            EXECUTION_FIELDS.intersection(command.patch.model_fields_set) or command.patch.enabled
        )

    async def write_access(self, context, command, *, approval=False):
        await self.access.check(
            context, purpose="schedule_owner" if approval else "schedule_manage"
        )
        if isinstance(command, ScheduleCreate) or (
            isinstance(command, ScheduleUpdate) and self.changes_input(command)
        ):
            await self.access.check(context, purpose="schedule_run")

    def _prepare(self, context, schedule_id, command, *, replay=False):
        with self.store.transaction() as tx:
            if replay and (old := self.previous(tx, context, schedule_id, command)):
                return None, ScheduleWriteReceipt.model_validate_json(json.dumps(old.response))
            plan = self.owned(tx, context, schedule_id) if schedule_id else None
            self.check_revision(plan, command)
            return self.fields(self.store, plan, command), None

    async def write(self, context, schedule_id, command):
        await self.write_access(context, command)
        fields, old = await asyncio.to_thread(
            self._prepare, context, schedule_id, command, replay=True
        )
        if old:
            return old
        if fields and (
            isinstance(command, ScheduleCreate)
            or isinstance(command, ScheduleUpdate)
            and self.changes_input(command)
        ):
            await self.validate_model(fields)
        return await asyncio.to_thread(self._write, context, schedule_id, command)

    def _write(self, context, schedule_id, command):
        with self.store.transaction() as tx:
            if old := self.previous(tx, context, schedule_id, command):
                return ScheduleWriteReceipt.model_validate_json(json.dumps(old.response))
            return self.apply(tx, context, schedule_id, command)

    def apply(self, tx, context, schedule_id, command, *, before_effect=None):
        plan = self.owned(tx, context, schedule_id, lock=True) if schedule_id else None
        self.check_revision(plan, command)
        if before_effect:
            before_effect()
        fields = self.fields(self.store, plan, command)
        clock = self.store.scheduler.clock(tx)
        if fields is None:
            assert plan is not None
            plan.status, plan.next_fire_at = "deleted", None
        else:
            cron = Cron.parse(fields.cron, fields.timezone)
            next_fire = cron.nearest(clock)
            reschedule = plan is None or (
                plan.status != "enabled"
                or plan.cron != fields.cron
                or plan.timezone != fields.timezone
            )
            if plan is None:
                if context.scope.kind != "tenant":
                    raise ModelError("forbidden", 403)
                plan = AgentSchedule(
                    id=uuid4(),
                    tenant_id=context.scope.tenant_id,
                    workspace_id=context.scope.workspace_id,
                    owner_user_id=context.actor_user_id,
                    created_at=clock,
                    revision=0,
                )
                tx.add(plan)
            plan.name, plan.cron, plan.timezone = fields.name, fields.cron, fields.timezone
            plan.status = "enabled" if fields.enabled else "disabled"
            if not fields.enabled:
                plan.next_fire_at = None
            elif reschedule:
                plan.next_fire_at = next_fire
            plan.input_ciphertext = self.store.private.seal(
                plan.id,
                "schedule",
                fields.model_dump(
                    mode="json", by_alias=True, include=set(ScheduleInput.model_fields)
                ),
            )
        plan.revision += 1
        plan.updated_at = clock
        tx.flush()
        tx.execute(
            update(AgentScheduleTrigger)
            .where(
                AgentScheduleTrigger.schedule_id == plan.id,
                AgentScheduleTrigger.delivery_status == "pending",
                AgentScheduleTrigger.delivery_attempts == 0,
            )
            .values(
                delivery_status="skipped",
                reason_code="schedule_changed",
                next_attempt_at=None,
                input_ciphertext=None,
            )
        )
        receipt = plan_receipt(plan)
        self.store.record_command(
            tx,
            context,
            command.request_id,
            "schedule",
            self.command_payload(schedule_id, command),
            None,
            None,
            receipt.model_dump(mode="json", by_alias=True),
        )
        return receipt
