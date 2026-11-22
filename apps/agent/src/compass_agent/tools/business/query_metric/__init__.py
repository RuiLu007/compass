"""通过已发布执行绑定按期间、维度和过滤查询正式指标。比较时传两个可比期间。返回受信结果引用、口径、单位与数据截止。"""

from typing import Literal
from uuid import UUID

from pydantic import Field

from compass_agent.semantic.tool import Arguments, Parameters, tool


class Input(Arguments):
    query_ref: UUID
    operation: Literal["query", "compare", "drilldown"] = "query"
    parameters: Parameters | None = None
    comparison: Parameters | None = Field(default=None, description="比较时填写用户所指的对照期。")
    parent_group: dict[str, str | int | bool] | Literal["total"] | None = Field(
        default=None, description="下钻起点。填写 total 或已绑定的父级筛选条件。"
    )


SPEC = tool("query_metric", "query", "query", Input)
