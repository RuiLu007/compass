"""Read-only ToolSpecs backed by the same authorized document reader."""

from pydantic import BaseModel, ConfigDict, ValidationError

from compass_agent.models.errors import ModelError
from compass_agent.prompt_assets import tool_text
from compass_agent.schemas import ToolDefinition, ToolFunction
from compass_agent.semantic.client import SourceUnavailable
from compass_agent.tools.catalog_contracts import ToolSpec
from compass_agent.tools.contracts import (
    ExecutionReceipt,
    InProcessBinding,
    RegisteredTool,
    ToolPolicy,
    ToolRejected,
)
from compass_agent.tools.registry import bounded_payload, parse_arguments


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


def document_tool(name, op, args_type):
    def definition(_):
        return ToolDefinition(
            function=ToolFunction(
                name=name, description=tool_text(name), parameters=args_type.model_json_schema()
            )
        )

    def register(context):
        service = context.documents
        assert service is not None

        def authorize(args, execution):
            execution.require_running()
            if execution.caller != "model":
                raise ToolRejected("tool_scope_denied")
            try:
                args_type.model_validate(args)
            except ValidationError:
                raise ToolRejected("invalid_parameters") from None

        async def execute(raw, execution):
            args = args_type.model_validate(raw).model_dump(mode="json", exclude_none=True)
            try:
                value = await service.run(op, args, execution)
            except SourceUnavailable as exc:
                code = "rate_limited" if exc.status == 429 else "document_service_unavailable"
                payload = {"status": "unavailable", "retryable": True, "code": code}
                if exc.retry_after is not None:
                    payload["retryAfterSeconds"] = exc.retry_after
                return ExecutionReceipt(execution.call_id, "error", "confirmed", payload, code)
            except (ModelError, ToolRejected) as exc:
                code = (
                    "document_unavailable"
                    if exc.code in {"forbidden", "document_unavailable"}
                    else exc.code
                )
                return ExecutionReceipt(
                    execution.call_id,
                    "rejected",
                    "confirmed",
                    {"status": "unavailable", "retryable": False, "code": code},
                    code,
                )
            return ExecutionReceipt(execution.call_id, "ok", "confirmed", value)

        return RegisteredTool(
            name,
            definition(context),
            ToolPolicy(),
            InProcessBinding(execute),
            parse_arguments,
            authorize,
            bounded_payload,
            parallel_safe=True,
        )

    return ToolSpec(
        name,
        "business",
        "documents",
        definition,
        lambda c: c.documents is not None,
        context_factory=register,
        parallel_safe=True,
    )
