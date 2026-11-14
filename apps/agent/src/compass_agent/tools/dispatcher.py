"""Single-call admission and execution; batch scheduling lives in batch.py."""

import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from jsonschema import Draft202012Validator

from compass_agent.execution_context import current_execution, recorded
from compass_agent.schemas import ToolCall
from compass_agent.tools.artifacts import ArtifactStore
from compass_agent.tools.contracts import (
    ExecutionReceipt,
    InProcessBinding,
    McpBinding,
    PhaseCanceled,
    PreparedToolCall,
    ResourceUnconfirmed,
    SandboxBinding,
    StopRequested,
    ToolBackend,
    ToolBusinessStopped,
    ToolExecutionContext,
    ToolExecutionEvent,
    ToolExecutionResult,
    ToolRejected,
    json_bytes,
)
from compass_agent.tools.registry import ToolRegistry


class ExecutionSlots:
    def __init__(self, process_limit: int = 4, run_limit: int = 2):
        self.process_limit = process_limit
        self.run_limit = run_limit
        self._active: dict[str, object] = {}
        self._quarantined: dict[str, tuple[str, str | None]] = {}
        self._changed = asyncio.Condition()

    @property
    def active(self) -> int:
        return len(self._active)

    @property
    def quarantined(self) -> int:
        return len(self._quarantined)

    @asynccontextmanager
    async def acquire(self, context: ToolExecutionContext):
        token = str(uuid4())
        async with self._changed:
            while (
                len(self._active) >= self.process_limit
                or sum(value == context.run_id for value in self._active.values()) >= self.run_limit
            ):
                context.require_running()
                async with asyncio.timeout(context.control.remaining):
                    await self._changed.wait()
            context.require_running()
            self._active[token] = context.run_id
        try:
            yield token
        finally:
            async with self._changed:
                if token not in self._quarantined:
                    self._active.pop(token, None)
                self._changed.notify_all()

    def quarantine(self, token: str, execution_id: str, environment: str | None = None) -> None:
        self._quarantined[token] = (execution_id, environment)

    def pending(self, environment: str) -> set[str]:
        return {
            identifier for identifier, kind in self._quarantined.values() if kind == environment
        }

    async def confirm_cleanup(self, execution_id: str, *, environment: str | None = None) -> None:
        async with self._changed:
            for token, (identifier, kind) in tuple(self._quarantined.items()):
                if identifier == execution_id and (environment is None or kind == environment):
                    self._quarantined.pop(token)
                    self._active.pop(token, None)
            self._changed.notify_all()


class CallControl:
    """A tool has its own deadline; the Run owns cancellation of the whole batch."""

    def __init__(self, parent, deadline: float):
        self.parent = parent
        self.id = parent.id
        self.deadline_at = deadline
        self.phase_deadline = deadline
        self.cleanup_confirmed = True

    @property
    def stop_reason(self):
        return self.parent.stop_reason

    @property
    def remaining(self) -> float:
        return max(0, self.deadline_at - asyncio.get_running_loop().time())

    def require_running(self) -> None:
        self.parent.require_running()
        if self.remaining <= 0:
            raise TimeoutError()

    def mark_cleanup_unconfirmed(self) -> None:
        self.cleanup_confirmed = False
        self.parent.mark_cleanup_unconfirmed()

    async def perform(self, operation, *, timeout_s=None):
        self.require_running()
        timeout = timeout_s if timeout_s is not None and timeout_s < self.remaining else None
        async with asyncio.timeout(timeout):
            return await operation()


class ToolDispatcher:
    def __init__(
        self,
        registry: ToolRegistry,
        slots: ExecutionSlots,
        artifacts: ArtifactStore,
        *,
        sandbox: ToolBackend | None = None,
        mcp: ToolBackend | None = None,
    ):
        self.registry, self.slots, self.artifacts = registry, slots, artifacts
        self.sandbox, self.mcp = sandbox, mcp

    def prepare(self, call: ToolCall, context: ToolExecutionContext) -> PreparedToolCall:
        context.require_running()
        tool = self.registry.resolve(call.function.name)
        if context.caller == "model" and (
            tool.definition is None or tool.name not in context.visible_names
        ):
            raise ToolRejected("tool_not_exposed")
        try:
            args = tool.parse_arguments(call.function.arguments)
            if tool.definition and not Draft202012Validator(
                tool.definition.function.parameters
            ).is_valid(cast(Any, args)):
                raise ToolRejected("invalid_parameters")
            tool.authorize(args, replace(context, call_id=call.id))
            return PreparedToolCall(call.id, tool, args)
        except ToolRejected:
            raise
        except (ValueError, TypeError, RecursionError) as error:
            raise ToolRejected("invalid_parameters") from error

    def admission(
        self, prepared: PreparedToolCall, context: ToolExecutionContext
    ) -> ToolExecutionResult | None:
        context.require_running()
        context = replace(context, call_id=prepared.call_id)
        tool = prepared.tool
        tool.authorize(prepared.args, context)
        if tool.policy.approval == "required":
            assert tool.prepare_proposal is not None
            journal = current_execution.get()
            if journal:
                proposal = tool.prepare_proposal(prepared.args, context)
                journal.pending_proposal = {
                    "toolName": tool.name,
                    "callId": prepared.call_id,
                    "stepId": str(context.step_id),
                    "args": prepared.args,
                    "proposal": proposal.model_dump(mode="json", by_alias=True),
                }
                if journal.approves(prepared, context.step_id):
                    return None
            return ToolExecutionResult(
                prepared.call_id, "waiting", proposal=tool.prepare_proposal(prepared.args, context)
            )
        if tool.binding is None:
            return ToolExecutionResult(prepared.call_id, "rejected", reason_code="tool_unavailable")
        return None

    async def revalidate(self, prepared, context):
        context.require_running()
        if prepared.tool.revalidate is not None:
            async with asyncio.timeout(context.control.remaining):
                await prepared.tool.revalidate(context)
            context.require_running()

    async def execute(
        self, prepared: PreparedToolCall, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        start = monotonic()
        context = replace(context, call_id=prepared.call_id)
        receipt = None
        result: ToolExecutionResult | None = None
        try:
            await self.revalidate(prepared, context)
            result = self.admission(prepared, context)
            if result is not None:
                return result
            orchestration = (
                isinstance(prepared.tool.binding, InProcessBinding)
                and prepared.tool.binding.orchestrates
            )
            async with nullcontext(None) if orchestration else self.slots.acquire(context) as slot:
                await self.revalidate(prepared, context)
                result = self.admission(prepared, context)
                if result is not None:
                    return result
                context.budget.start_tool()
                context.on_start(prepared)
                control = CallControl(
                    context.control,
                    min(
                        context.control.deadline_at,
                        context.control.deadline_at
                        if orchestration
                        else asyncio.get_running_loop().time()
                        + (
                            106
                            if isinstance(prepared.tool.binding, SandboxBinding)
                            else prepared.tool.timeout_s
                        ),
                    ),
                )
                call_context = replace(context, control=control)
                try:
                    tool_deadline = (
                        control.deadline_at
                        if control.deadline_at < context.control.deadline_at
                        else None
                    )
                    async with asyncio.timeout_at(tool_deadline):
                        receipt = await self._invoke(prepared, call_context)
                except PhaseCanceled as error:
                    receipt = ExecutionReceipt(
                        prepared.call_id,
                        "canceled",
                        "confirmed" if error.cleanup_confirmed else "unconfirmed",
                        reason_code="tool_canceled",
                    )
                except TimeoutError:
                    receipt = ExecutionReceipt(
                        prepared.call_id, "timeout", "unconfirmed", reason_code="tool_timeout"
                    )
                except asyncio.CancelledError:
                    receipt = ExecutionReceipt(
                        prepared.call_id, "canceled", "unconfirmed", reason_code="tool_canceled"
                    )
                except ResourceUnconfirmed:
                    receipt = ExecutionReceipt(
                        prepared.call_id,
                        "unknown",
                        "unconfirmed",
                        reason_code="cleanup_unconfirmed",
                    )
                except ToolRejected as error:
                    receipt = ExecutionReceipt(
                        prepared.call_id, "rejected", "not_started", reason_code=error.code
                    )
                except ToolBusinessStopped:
                    raise
                except Exception:
                    receipt = ExecutionReceipt(
                        prepared.call_id, "error", "unconfirmed", reason_code="tool_failed"
                    )
                if receipt.cleanup == "unconfirmed":
                    if slot is not None:
                        self.slots.quarantine(
                            slot,
                            receipt.execution_id,
                            prepared.tool.binding.environment if prepared.tool.binding else None,
                        )
                    context.control.mark_cleanup_unconfirmed()
                    result = ToolExecutionResult(
                        prepared.call_id, "unknown", "unconfirmed", receipt.reason_code
                    )
                elif control.remaining <= 0:
                    result = ToolExecutionResult(
                        prepared.call_id, "timeout", receipt.cleanup, "tool_timeout"
                    )
                else:
                    context.require_running()
                    result = self._receive(prepared, receipt, context)
                return result
        except ToolRejected as error:
            result = ToolExecutionResult(prepared.call_id, "rejected", reason_code=error.code)
            return result
        except ToolBusinessStopped:
            result = ToolExecutionResult(
                prepared.call_id, "canceled", "confirmed", "tool_business_stopped"
            )
            raise
        except (StopRequested, TimeoutError):
            result = ToolExecutionResult(
                prepared.call_id,
                "canceled",
                cleanup=receipt.cleanup if receipt else "not_started",
                reason_code="run_stopped",
            )
            return result
        finally:
            if result is not None:
                binding = prepared.tool.binding
                context.emit(
                    ToolExecutionEvent(
                        context.run_id,
                        context.step_id,
                        prepared.call_id,
                        prepared.tool.name,
                        binding.environment if binding else "none",
                        prepared.tool.policy.effect,
                        prepared.tool.policy.approval,
                        result.status,
                        result.cleanup,
                        int((monotonic() - start) * 1000),
                        result.reason_code,
                        receipt.environment_id if receipt else None,
                        json_bytes(
                            result.payload
                            if result.payload is not None
                            else {"status": result.status, "code": result.reason_code}
                        ).decode(),
                    )
                )

    async def _invoke(self, prepared, context):
        if not isinstance(
            prepared.tool.binding, InProcessBinding
        ) or prepared.tool.policy.effect in {"external_write", "unknown"}:
            import base64
            from dataclasses import asdict

            async def invoke():
                receipt = await self._invoke_live(prepared, context)
                value = asdict(receipt)
                value["file"] = base64.b64encode(receipt.file).decode() if receipt.file else None
                return value

            value = await recorded(
                "tool",
                f"physical:{prepared.call_id}",
                {
                    "tool": prepared.tool.name,
                    "args": prepared.args,
                    "owner": [str(v) for v in context.owner],
                    "actionId": (
                        (
                            current_execution.get().approved_action(prepared, context.step_id) or {}
                        ).get("actionId")
                        if current_execution.get()
                        else None
                    ),
                },
                invoke,
                dict,
                definition=current_execution.get().binding_snapshot(prepared.tool)
                if current_execution.get()
                else None,
            )
            value["file"] = (
                base64.b64decode(value["file"], validate=True) if value["file"] else None
            )
            return ExecutionReceipt(**value)
        return await self._invoke_live(prepared, context)

    async def _invoke_live(self, prepared, context):
        binding = prepared.tool.binding
        if isinstance(binding, InProcessBinding):
            return await binding.handler(prepared.args, context)
        if isinstance(binding, SandboxBinding) and self.sandbox:
            return await self.sandbox.execute(binding, prepared.args, context)
        if isinstance(binding, McpBinding) and self.mcp:
            return await self.mcp.execute(binding, prepared.args, context)
        raise ToolRejected("tool_backend_unavailable")

    def _receive(self, prepared, receipt, context):
        try:
            payload = (
                prepared.tool.validate_payload(receipt.payload)
                if receipt.payload is not None or receipt.status in {"ok", "empty"}
                else None
            )
        except Exception:
            return ToolExecutionResult(
                prepared.call_id, "error", receipt.cleanup, "invalid_tool_result"
            )
        if receipt.status not in {"ok", "empty"}:
            return ToolExecutionResult(
                prepared.call_id,
                receipt.status,
                receipt.cleanup,
                receipt.reason_code,
                payload=payload,
            )
        refs, artifact_status = (), "none"
        if receipt.file is not None:
            try:
                refs = (self.artifacts.register(receipt.file, context),)
                artifact_status = "ready"
            except ToolRejected as error:
                artifact_status = error.code
            except Exception:
                artifact_status = "artifact_receive_failed"
        return ToolExecutionResult(
            prepared.call_id,
            receipt.status,
            receipt.cleanup,
            payload=payload,
            artifacts=refs,
            artifact_status=artifact_status,
        )
