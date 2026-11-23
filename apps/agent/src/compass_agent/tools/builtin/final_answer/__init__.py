"""Submit a private answer candidate through the ordinary tool dispatcher."""

from pydantic import ValidationError

from compass_agent.context_runtime.answer_candidates import submit
from compass_agent.context_runtime.answers import ANSWER, answer_schema
from compass_agent.prompt_assets import tool_text
from compass_agent.schemas import ToolDefinition, ToolFunction
from compass_agent.tools.catalog_contracts import ToolSpec
from compass_agent.tools.contracts import (
    InProcessBinding,
    RegisteredTool,
    ToolPolicy,
    ToolRejected,
)
from compass_agent.tools.registry import bounded_payload, parse_arguments


def definition(_):
    return ToolDefinition(
        function=ToolFunction(
            name="final_answer",
            description=tool_text("final_answer"),
            parameters=answer_schema(),
        )
    )


def authorize(args, context):
    context.require_running()
    if context.caller != "model":
        raise ToolRejected("tool_scope_denied")
    try:
        ANSWER.validate_python(args)
    except ValidationError:
        raise ToolRejected("invalid_answer_candidate") from None


def registration():
    return RegisteredTool(
        "final_answer",
        definition(None),
        ToolPolicy(),
        InProcessBinding(submit),
        parse_arguments,
        authorize,
        bounded_payload,
        completion=True,
        public_projection="status_only",
    )


SPEC = ToolSpec(
    "final_answer", "builtin", "completion", definition, lambda context: True, factory=registration
)
