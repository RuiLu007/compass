"""从授权素材或同会话临时查询回执发现候选。说明来源、位置、置信度、公式与缺项。保存前向用户展示可编辑预览并等待确认。"""

from typing import Literal
from uuid import UUID

from pydantic import Field

from compass_agent.semantic.draft_fields import SuggestedDraftFields
from compass_agent.semantic.tool import Arguments, tool


class Input(Arguments):
    source_refs: list[UUID] | None = Field(default=None, max_length=8)
    owner_scope_id: UUID | None = None
    usage_scope_id: UUID | None = None
    result_ref: UUID | None = Field(
        default=None,
        description="可选。用户指的是刚才的查询时可以省略，系统会在当前会话查找并让用户选择。",
    )
    trigger: Literal["requested", "suggest"] = Field(
        default="requested",
        description="用户要求保存或选择时为 requested。只有主动推荐时填 suggest。",
    )
    fields: SuggestedDraftFields | None = None


SPEC = tool("discover_semantic_candidates", "discover", "draft", Input)
