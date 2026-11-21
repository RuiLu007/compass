"""搜索当前发布及授权范围内的术语、指标和实体，按名称、别名和业务域检索。"""

from typing import Literal
from uuid import UUID

from pydantic import Field

from compass_agent.semantic.tool import Arguments, tool


class Input(Arguments):
    query: str = Field(max_length=256)
    kinds: list[str] | None = None
    domain: str | None = None
    scope_filter: UUID | None = None
    cursor: UUID | Literal[0] | None = None
    limit: int = Field(default=12, ge=1, le=50)


SPEC = tool("search_semantics", "search", "read", Input)
