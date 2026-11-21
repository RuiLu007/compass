"""One prompt starts an isolated child Agent."""

from pydantic import Field

from compass_agent.prompt_assets import tool_text
from compass_agent.schemas import RunContractModel, ToolDefinition, ToolFunction
from compass_agent.tools.catalog_contracts import ToolSpec

NAME = "sub_agent"


class SubAgentInput(RunContractModel):
    prompt: str = Field(min_length=1, max_length=8000, pattern=r"\S")


def definition(_context):
    return ToolDefinition(
        function=ToolFunction(
            name=NAME,
            description=(tool_text("sub_agent")),
            parameters=SubAgentInput.model_json_schema(),
        )
    )


SPEC = ToolSpec(
    NAME, "builtin", "workflow", definition, lambda context: context.workflow, orchestrates=True
)
