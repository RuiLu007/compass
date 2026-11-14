"""Restricted Retail sales query and its domain adapters."""

from compass_agent.tools.business.retail_sales.handler import retail_tool_definition
from compass_agent.tools.catalog_contracts import ToolSpec, retail_available

SPEC = ToolSpec(
    "retail_sales",
    "business",
    "retail",
    lambda context: retail_tool_definition(),
    retail_available,
    parallel_safe=True,
)
