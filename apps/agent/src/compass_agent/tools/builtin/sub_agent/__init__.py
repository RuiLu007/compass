"""One prompt starts an isolated child Agent."""

from pydantic import Field

from compass_agent.schemas import RunContractModel, ToolDefinition, ToolFunction
from compass_agent.tools.catalog_contracts import ToolSpec

NAME = "sub_agent"


class SubAgentInput(RunContractModel):
    prompt: str = Field(min_length=1, max_length=8000, pattern=r"\S")


def definition(_context):
    return ToolDefinition(
        function=ToolFunction(
            name=NAME,
            description=(
                "将一个明确的只读分析子任务交给独立 Agent。"
                "在 prompt 提供必要背景、数据范围和预期输出。仅返回子任务结论。"
            ),
            parameters=SubAgentInput.model_json_schema(),
        )
    )


SPEC = ToolSpec(
    NAME, "builtin", "workflow", definition, lambda context: context.workflow, orchestrates=True
)
