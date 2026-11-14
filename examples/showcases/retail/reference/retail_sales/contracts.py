"""State ports consumed by trusted Retail handlers, independent of Runtime storage."""

from collections.abc import Mapping
from typing import Any, Protocol

from compass_agent.harness.contracts import Step, ToolResult, TraceSink
from compass_agent.schemas import (
    ToolCall,
    ToolDefinition,
    WorkflowDraftView,
    WorkflowPeriods,
)
from compass_agent.tools.business.database_query import DatabaseResult
from compass_agent.tools.business.retail_sales.export import ExportCandidate
from compass_agent.tools.business.retail_sales.handler import RetailBinding, RetailQueryResult
from compass_agent.tools.business.retail_sales.session import RetailSession


class WorkflowSession(Protocol):
    run: RetailSession
    diagnosis_periods: WorkflowPeriods | None
    save_offer: WorkflowDraftView | None

    @property
    def registered(self) -> Mapping[str, Any]: ...
    def tool_definitions(self) -> tuple[ToolDefinition, ...]: ...
    def owns_tool(self, name: str) -> bool: ...
    async def tool(self, call: ToolCall) -> dict: ...
    async def load_catalog(self) -> str: ...
    async def dynamic(self, request: Any) -> dict: ...
    def event(
        self, source: str, key: str, status: str, detail: str | None = None
    ) -> str | None: ...


class RetailRoute(Protocol):
    retail_binding: RetailBinding | None
    database_enabled: bool
    database_only: bool

    @property
    def tools_enabled(self) -> bool: ...


class RetailActionState(Protocol):
    export_candidate: ExportCandidate | None
    database_results: dict[str, DatabaseResult]
    retail_results: dict[str, RetailQueryResult]
    hybrid: WorkflowSession | None
    trace: TraceSink | None

    def record_tool_result(self, step: Step, result: ToolResult) -> None: ...
