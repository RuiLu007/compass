"""Thin model-routing flow; no Harness, task dispatch, retries or recovery engine."""

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any
from uuid import uuid5

from starlette.concurrency import run_in_threadpool

from compass_agent.llm.policy import LogicalPolicy
from compass_agent.models import RoutingGrant
from compass_agent.models.errors import ModelError
from compass_agent.routing.config import POLICY
from compass_agent.routing.dictionary import catalog, features_for
from compass_agent.routing.intent import decision_input, flash_input, from_flash, from_jev
from compass_agent.routing.prompts import MODE_PROMPTS, OFF_TOPIC_PROMPT
from compass_agent.schemas import (
    CallOutcome,
    ChatMessage,
    ClassificationEvent,
    Intent,
    ModelRoute,
    ProviderView,
    RoutedCallResponse,
    RouteSelectedEvent,
    RoutingFeatures,
    RoutingMetrics,
)
from compass_agent.settings import get_settings


@dataclass(frozen=True, slots=True)
class SelectedRoute:
    intent: Intent
    features: RoutingFeatures
    routing: ModelRoute
    provider: ProviderView
    prompt: str
    off_topic: bool
    metrics: RoutingMetrics


class RoutingService:
    def __init__(self, models, settings=None):
        self.models = models
        self.settings = settings or get_settings()

    async def provider(self, role):
        configured = getattr(self.settings, f"compass_routing_{role}_provider_id")
        return await run_in_threadpool(self.models.repository.routing_provider, role, configured)

    async def _stage(
        self,
        command,
        context,
        role,
        provider_id,
        value,
        stage_deadline,
        kind,
        metrics,
        *,
        emit=None,
        check=None,
        expected_binding_version=None,
    ):
        if check:
            check()
        physical_id = command.call_id if role == "inference" else uuid5(command.call_id, role)
        grant = (
            RoutingGrant(context.actor_user_id, context.scope, physical_id, provider_id, value.kind)
            if kind == "classifier"
            else None
        )
        self.models.stage = role
        prepared = await self.models.prepare(
            physical_id,
            provider_id,
            value,
            context,
            deadline=stage_deadline,
            grant=grant,
            expected_binding_version=expected_binding_version,
        )
        result = await self.models.run(
            prepared,
            value,
            emit=emit if role == "inference" else None,
            logical_policy=LogicalPolicy(
                max_attempts=2 if role == "inference" and value.kind == "generate" else 1
            ),
        )
        if kind == "classifier":
            metrics.classification_calls.append(
                CallOutcome.model_validate(result.model_dump(include=set(CallOutcome.model_fields)))
            )
        if result.error_code == "record_write_failed":
            raise ModelError("record_write_failed", 503)
        return result

    async def select_route(
        self,
        command,
        context,
        *,
        deadline=None,
        emit=None,
        check=None,
        metrics=None,
    ) -> SelectedRoute:
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = deadline or started + POLICY.parent_seconds
        metrics = metrics or RoutingMetrics()
        classification_deadline = min(deadline - 1, started + POLICY.classification_seconds)
        intent = None
        if self.settings.compass_routing_mode == "jev_with_flash":
            metrics.fallback_reason = "jev_unavailable"
            try:
                if check:
                    check()
                jev_id, _ = await self.provider("jev")
                if emit:
                    await emit(ClassificationEvent(call_id=command.call_id, stage="jev"))
                decision = await self._stage(
                    command,
                    context,
                    "intent_jev",
                    jev_id,
                    decision_input(command.input),
                    min(classification_deadline, loop.time() + POLICY.jev_seconds),
                    "classifier",
                    metrics,
                    check=check,
                )
                intent = from_jev(decision)
                if intent is None:
                    metrics.fallback_reason = "jev_unavailable_or_uncertain"
            except ModelError as exc:
                if exc.code not in {
                    "route_binding_missing",
                    "capability_not_supported",
                    "response_too_large",
                    "model_unavailable",
                }:
                    raise
        elif self.settings.compass_routing_mode != "flash_only":
            raise ModelError("route_binding_missing", 503)
        if intent is None:
            if check:
                check()
            flash_id, _ = await self.provider("flash")
            if emit:
                await emit(ClassificationEvent(call_id=command.call_id, stage="flash"))
            prediction = await self._stage(
                command,
                context,
                "intent_flash",
                flash_id,
                flash_input(command.input),
                min(classification_deadline, loop.time() + POLICY.flash_seconds),
                "classifier",
                metrics,
                check=check,
            )
            intent = from_flash(prediction)
            metrics.classifier_source = "flash"
        else:
            metrics.classifier_source = "jev"
            metrics.fallback_reason = None
        features = features_for(intent)
        route_observation = (
            self.models.root_observation.child("compass.route")
            if getattr(self.models, "root_observation", None)
            else None
        )
        with route_observation if route_observation else nullcontext():
            selection = command.selection()
            off_topic = intent.scene_id == "SC-39"
            if check:
                check()
            if off_topic:
                provider_id, _ = await self.provider("flash")
                reason = "off_topic_flash"
            elif selection.mode == "fixed":
                provider_id, reason = selection.provider_id, "user_fixed_main_model"
            else:
                role = "pro" if features.difficulty in {"L2", "L3"} else "flash"
                provider_id, _ = await self.provider(role)
                reason = "difficulty_" + features.difficulty
            if check:
                check()
            view = await run_in_threadpool(self.models.repository.get, provider_id)
            routing = ModelRoute(
                mode=selection.mode,
                provider_id=provider_id,
                model=view.api_model,
                reason=reason,
                dictionary_version=catalog()[0],
                routing_version=POLICY.version,
            )
            if route_observation:
                route_observation.set(
                    category_id=intent.category_id,
                    scene_id=intent.scene_id,
                    difficulty=features.difficulty,
                    mode=selection.mode,
                    reason=reason,
                    provider_id=str(provider_id),
                    model=view.api_model,
                    dictionary_version=catalog()[0],
                    routing_version=POLICY.version,
                )
        if emit:
            await emit(
                RouteSelectedEvent(
                    call_id=command.call_id,
                    intent=intent,
                    features=features,
                    routing=routing,
                )
            )
        return SelectedRoute(
            intent=intent,
            features=features,
            routing=routing,
            provider=view,
            prompt=OFF_TOPIC_PROMPT if off_topic else MODE_PROMPTS[features.response_mode],
            off_topic=off_topic,
            metrics=metrics,
        )

    async def run(self, command, context, emit=None) -> Any:
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + POLICY.parent_seconds
        response = RoutedCallResponse(call_id=command.call_id, status="failed")
        metrics = response.metrics
        try:
            async with asyncio.timeout_at(deadline):
                selected = await self.select_route(
                    command,
                    context,
                    deadline=deadline,
                    emit=emit,
                    metrics=metrics,
                )
                response.intent = selected.intent
                response.features = selected.features
                response.routing = selected.routing
                updates: dict = {
                    "messages": [
                        ChatMessage(role="system", content=selected.prompt),
                        *[m for m in command.input.messages if m.role != "system"],
                    ]
                }
                if selected.off_topic:
                    updates.update(
                        thinking=False,
                        reasoning_effort=None,
                        tools=[],
                        output_format="text",
                        output_schema=None,
                        max_tokens=512,
                        continuation_token=None,
                    )
                generated = command.input.model_copy(update=updates)
                result = await self._stage(
                    command,
                    context,
                    "inference",
                    selected.provider.id,
                    generated,
                    deadline,
                    "inference",
                    metrics,
                    emit=emit,
                    expected_binding_version=selected.provider.binding_version,
                )
                response.result = result
                response.status = result.status
                response.error_code = result.error_code
        except ModelError as exc:
            if exc.code == "call_already_exists":
                raise
            response.error_code = exc.code
        except TimeoutError:
            response.error_code = "model_timeout"
        finally:
            metrics.duration_ms = max(0, int((loop.time() - started) * 1000))
            usages = [x.usage for x in metrics.classification_calls]
            if response.result:
                usages.append(response.result.usage)
            metrics.usage_complete = bool(usages) and all(
                x.input_tokens is not None and x.output_tokens is not None for x in usages
            )
        return response
