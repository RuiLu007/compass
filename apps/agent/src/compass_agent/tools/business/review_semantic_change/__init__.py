"""检查草稿来源、声明、依赖、冲突与已登记影响，说明阻断项及修正建议。"""

from uuid import UUID

from compass_agent.semantic.tool import Arguments, tool


class Input(Arguments):
    version_id: UUID


SPEC = tool("review_semantic_change", "validate", "draft", Input)
