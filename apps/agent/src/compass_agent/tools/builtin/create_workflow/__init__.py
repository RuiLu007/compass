"""Model-generated workflow definition and trial inputs."""

from pydantic import Field

from compass_agent.schemas import RunContractModel, ToolDefinition, ToolFunction
from compass_agent.tools.catalog_contracts import ToolSpec

NAME = "create_workflow"


class CreateWorkflowInput(RunContractModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    parameters: dict = Field(description="调用此工作流的 JSON Schema。顶层 type 必须为 object。")
    script_source: str = Field(alias="scriptSource", min_length=1, max_length=16384)
    args: dict = Field(description="本次用来试跑的实际参数，符合 parameters。")


def definition(_context):
    return ToolDefinition(
        function=ToolFunction(
            name=NAME,
            description=(
                "用户要求沉淀已验证的分析方法时，编写可复用的 JavaScript 编排并试跑。"
                "scriptSource 填写异步函数体，第一行可直接写 const 参数 = args.字段。用 "
                'await agent("sub_agent", {prompt: "完整子任务"}) 调用子 Agent，返回 {answer}。'
                "用 await parallel([() => agent(...), () => agent(...)]) 并行独立任务。"
                "每次最多 3 个子任务，同时最多 2 个，单个子任务入参最多 4 KiB。"
                "以 return {status: 'ok', result: ...} "
                "提交可复用结果，数据不足时返回 {status: 'needs_data', result: ...}。"
                "将会变化的数据范围作为参数，并给子任务提供所需的表、口径和范围。"
                "收到 invalid_workflow_script 时，修正 JavaScript 语法后再试跑。"
                "试跑成功后由用户确认保存。"
            ),
            parameters=CreateWorkflowInput.model_json_schema(by_alias=True),
        )
    )


SPEC = ToolSpec(
    NAME, "builtin", "workflow", definition, lambda context: context.workflow, orchestrates=True
)
