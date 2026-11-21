"""解析本次提问指向的正式指标。多个合法口径时由原会话用户选择。用签发的 query_ref 计算。"""

from uuid import UUID

from pydantic import Field

from compass_agent.semantic.tool import Arguments, Parameters, tool


class Input(Arguments):
    mention: str = Field(min_length=1, max_length=160)
    metric_id: UUID | None = None
    dimensions: list[str] = Field(default_factory=list, max_length=8)
    parameters: Parameters | None = None
    historical_as_of: str | None = Field(
        default=None,
        max_length=40,
        description="User-specified historical data cutoff; omit to use the Run start.",
    )


SPEC = tool("resolve_semantics", "resolve", "read", Input)
