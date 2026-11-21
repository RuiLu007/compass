"""读取已发布定义、统计粒度、期间、单位、适用维度与可见关系。"""

from uuid import UUID

from compass_agent.semantic.tool import Arguments, tool


class Input(Arguments):
    asset_id: UUID
    version_id: UUID | None = None


SPEC = tool("get_metric_definition", "definition", "read", Input)
