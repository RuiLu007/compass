"""One model-call lifecycle: capability, deadline, cancellation and resource ownership."""

import asyncio
import logging
from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType

from compass_agent.llm.capabilities import validate_input
from compass_agent.llm.contracts import (
    AdapterKind,
    CallSession,
    DecisionOutput,
    DeltaSink,
    ExecutionRequest,
    GenerationOutput,
    ProviderAdapter,
    ProviderProgress,
    RuntimeCancelled,
    RuntimeResult,
)
from compass_agent.llm.errors import RuntimeErrorInfo, RuntimeFailure
from compass_agent.llm.transport import CallResources

logger = logging.getLogger(__name__)


class Runtime:
    def __init__(self, adapters: Mapping[AdapterKind, ProviderAdapter]):
        if not adapters or set(adapters) - {"litellm_chat", "systemone_decision"}:
            raise ValueError("unsupported adapter mapping")
        self.adapters = MappingProxyType(dict(adapters))

    async def invoke(
        self, request: ExecutionRequest, emit: DeltaSink | None = None, *, observation=None
    ) -> RuntimeResult:
        # Copy caller-owned nested DTOs before any suspension or adapter mutation.
        request = deepcopy(request)
        spec = request.access.spec
        progress = ProviderProgress(
            GenerationOutput() if request.input.kind == "generate" else DecisionOutput()
        )
        session = CallSession(
            CallResources(max_response_bytes=request.policy.limits.wire_response_bytes),
            progress,
            emit,
            observation,
        )
        resources = session.resources
        loop = asyncio.get_running_loop()
        status = "failed"
        failure = None
        unknown = False
        canceled = False
        adapter_started = False
        try:
            async with asyncio.timeout_at(request.policy.deadline_monotonic):
                if loop.time() >= request.policy.deadline_monotonic:
                    raise TimeoutError()
                if not 0 < spec.binding_version <= spec.config_version:
                    raise RuntimeFailure("validation_failed")
                binding = request.context.continuation_binding
                if (
                    binding.provider != spec.provider_id
                    or binding.binding_version != spec.binding_version
                ):
                    raise RuntimeFailure("continuation_invalid")
                validate_input(
                    spec, request.input, request.context.capability_mode, request.policy.limits
                )
                adapter = self.adapters.get(spec.adapter)
                if adapter is None:
                    raise RuntimeFailure("capability_not_supported")
                owner = asyncio.current_task()
                if owner is not None and owner.cancelling():
                    raise asyncio.CancelledError()
                adapter_started = True
                if observation is not None:
                    observation.capture_input(request)
                output = await adapter.invoke(request, session)
                if output.kind != request.input.kind:
                    raise RuntimeFailure("provider_protocol_error")
                if not progress.normal_eof or progress.finish_reason not in {"stop", "tool_calls"}:
                    raise RuntimeFailure("stream_interrupted")
                if progress.validation.status == "invalid":
                    raise RuntimeFailure("schema_validation_failed")
                progress.output = output
                status = "completed"
        except asyncio.CancelledError:
            canceled = True
        except Exception as exc:
            code = resources.error_code or (
                exc.error.code
                if isinstance(exc, RuntimeFailure)
                else "model_timeout"
                if isinstance(exc, TimeoutError)
                else "internal_error"
            )
            recovery = (
                exc.error.recovery
                if isinstance(exc, RuntimeFailure) and not resources.has_partial
                else "never"
            )
            failure = RuntimeErrorInfo(code, recovery)
            status = (
                "incomplete"
                if resources.has_partial or code in {"stream_interrupted", "output_truncated"}
                else "failed"
            )
            unknown = (
                recovery == "never"
                and resources.sent
                and code
                in {
                    "model_timeout",
                    "model_unavailable_upstream",
                    "stream_interrupted",
                    "internal_error",
                }
            )
        finally:
            cleanup = asyncio.create_task(resources.close())
            deadline = loop.time() + request.policy.cleanup_timeout_s
            owner = asyncio.current_task()
            cancellations = owner.cancelling() if owner else 0
            while not cleanup.done() and loop.time() < deadline:
                try:
                    await asyncio.wait_for(asyncio.shield(cleanup), max(0, deadline - loop.time()))
                except asyncio.CancelledError:
                    current = owner.cancelling() if owner else 0
                    canceled = canceled or current > cancellations
                    cancellations = current
                except Exception:
                    break
            if not cleanup.done():
                resources.cleanup_confirmed = False
                cleanup.cancel()
                cleanup.add_done_callback(self._consume_cleanup)
                logger.warning(
                    "Model resource cleanup exceeded limit; call_id=%s", request.context.call_id
                )
            else:
                self._consume_cleanup(cleanup)
        if canceled:
            status, failure, unknown = "canceled", None, resources.sent
        result = RuntimeResult(
            call_id=request.context.call_id,
            provider_id=spec.provider_id,
            config_version=spec.config_version,
            binding_version=spec.binding_version,
            requested_model=spec.api_model,
            status=status,
            duration_ms=max(0, int((loop.time() - request.policy.started_monotonic) * 1000)),
            output=deepcopy(progress.output),
            usage=progress.usage.model_copy(deep=True),
            validation=progress.validation.model_copy(deep=True),
            response_model=progress.response_model,
            upstream_request_id=progress.upstream_request_id,
            finish_reason=progress.finish_reason,
            error=failure,
            upstream_outcome_unknown=unknown,
            upstream_sent=resources.sent,
            adapter_started=adapter_started,
            cleanup_confirmed=resources.cleanup_confirmed
            and cleanup.done()
            and not cleanup.cancelled()
            and cleanup.exception() is None,
        )
        if canceled:
            raise RuntimeCancelled(result)
        return result

    @staticmethod
    def _consume_cleanup(task):
        if not task.cancelled():
            task.exception()
