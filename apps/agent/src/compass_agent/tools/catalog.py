"""Shipped generic tools; per-Run providers supply authorized bindings."""

from compass_agent.tools.builtin.create_workflow import SPEC as CREATE_WORKFLOW
from compass_agent.tools.builtin.database_query import SPEC as DATABASE_QUERY
from compass_agent.tools.builtin.python_analysis import SPEC as PYTHON_ANALYSIS
from compass_agent.tools.builtin.sub_agent import SPEC as SUB_AGENT
from compass_agent.tools.builtin.todo_write import SPEC as TODO_WRITE
from compass_agent.tools.catalog_contracts import CatalogContext, ToolSpec

TOOLS: tuple[ToolSpec, ...] = (
    TODO_WRITE,
    DATABASE_QUERY,
    PYTHON_ANALYSIS,
    SUB_AGENT,
    CREATE_WORKFLOW,
)


def selected(context: CatalogContext) -> tuple[ToolSpec, ...]:
    return tuple(
        spec for spec in TOOLS if spec.capability in context.enabled and spec.available(context)
    )


def definitions(context: CatalogContext):
    return [value for spec in selected(context) if (value := spec.definition(context)) is not None]


def registrations(context: CatalogContext, handler):
    return [spec.register(context, handler) for spec in selected(context)]
