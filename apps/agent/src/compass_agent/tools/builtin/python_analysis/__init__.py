"""Authorized input references and code bounds for the Python sandbox tool."""

from compass_agent.schemas import ToolDefinition, ToolFunction
from compass_agent.tools.catalog_contracts import ToolSpec
from compass_agent.tools.contracts import (
    RegisteredTool,
    SandboxBinding,
    ToolPolicy,
    ToolRejected,
    json_bytes,
)
from compass_agent.tools.registry import bounded_payload, parse_arguments


def python_analysis_tool() -> RegisteredTool:
    def authorize(args, context):
        if not isinstance(args, dict) or not isinstance(args.get("code"), str):
            raise ToolRejected("invalid_parameters")
        source = context.sources.get(args.get("source_query_id"))
        if source is None or source.owner != context.owner or source.run_id != context.run_id:
            raise ToolRejected("sandbox_source_denied")
        if len(args["code"].encode()) > 16 * 1024 or len(json_bytes(source.data)) > 128 * 1024:
            raise ToolRejected("sandbox_input_limit")

    return RegisteredTool(
        "python_analysis",
        ToolDefinition(
            type="function",
            function=ToolFunction(
                name="python_analysis",
                description=(
                    "在独立 Python 环境中分析本轮已授权的查询结果。data 是输入数据；"
                    "将 JSON 结果赋给 result，可写 /workspace/chart.png 生成 PNG。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "source_query_id": {"type": "string", "maxLength": 128},
                        "code": {"type": "string", "minLength": 1, "maxLength": 16384},
                    },
                    "required": ["source_query_id", "code"],
                    "additionalProperties": False,
                },
            ),
        ),
        ToolPolicy("local_artifact", "none"),
        SandboxBinding(),
        parse_arguments,
        authorize,
        bounded_payload,
        parallel_safe=True,
    )


SPEC = ToolSpec(
    "python_analysis",
    "builtin",
    "python",
    lambda context: python_analysis_tool().definition,
    lambda context: context.sandbox,
    parallel_safe=True,
    factory=python_analysis_tool,
)
