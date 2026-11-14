"""Explicit sample inputs for Retail regression tests."""

from compass_agent.schemas import RunPeriod, WorkflowPeriods

RETAIL_PLAN_PRESET = (
    "比较 2025-11-17 至 11-23 和 11-24 至 11-30 两周的渠道实付金额，"
    "找到变化最大的渠道，再查询这个渠道的商品贡献并核对前三项。"
)
DEFAULT_PERIODS = WorkflowPeriods(
    baseline=RunPeriod(start="2025-11-17", end="2025-11-24"),
    current=RunPeriod(start="2025-11-24", end="2025-12-01"),
)
