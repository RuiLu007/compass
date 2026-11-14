"""Generic request context and model input assembly."""

from dataclasses import dataclass
from uuid import UUID

from compass_agent.harness.context import AgentDefinition, build_messages, model_input
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    AnalysisRunInput,
    ChatMessage,
    GenerateInput,
    ToolDefinition,
)
from compass_agent.tools.catalog import definitions as catalog_definitions
from compass_agent.tools.catalog_contracts import CatalogContext


@dataclass(frozen=True, slots=True)
class RunRoute:
    provider_id: UUID
    binding_version: int
    model_label: str
    scene_id: str
    route_prompt: str
    stream: bool
    thinking: bool
    deepseek: bool = False
    tools_enabled: bool = False


def initial_messages(
    definition: AgentDefinition,
    command: AnalysisRunInput,
    route: RunRoute,
    *,
    controlled=False,
    workflow=False,
):
    instructions = route.route_prompt
    if command.constraints and command.constraints.plan == "required":
        instructions += "\n本轮先调用 todo_write 登记计划，执行后依据实际结果回答用户。"
    if workflow:
        instructions += (
            f"\n每轮最多发出 {RUN_STREAM_LIMITS['toolsPerStep']} 个工具调用。"
            "工具列表中若有符合目标和参数的个人工作流，可直接调用以获取本次结果。"
            "用户要求沉淀已验证的方法时，在最终回答前调用 create_workflow。"
        )
    return build_messages(definition, command, instructions)


def builtin_definitions(definition: AgentDefinition, *, database=False, sandbox=False):
    return [
        entry.definition for entry in definition.tools if entry.definition is not None
    ] + catalog_definitions(
        CatalogContext(definition.builtin_tools, database=database, sandbox=sandbox)
    )


def generate_input(
    definition,
    messages: list[ChatMessage],
    continuation_token,
    route: RunRoute,
    *,
    controlled=False,
    extra_tools: tuple[ToolDefinition, ...] = (),
) -> GenerateInput:
    return model_input(
        messages,
        continuation_token,
        tools=[*builtin_definitions(definition), *extra_tools],
        stream=route.stream,
        thinking=route.thinking,
        controlled=controlled,
        deepseek=route.deepseek,
    )


def compass_definition() -> AgentDefinition:
    return AgentDefinition(builtin_tools=frozenset({"todo", "database", "python", "workflow"}))
