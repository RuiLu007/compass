"""Application composition of query tools from current workspace authorization."""

import asyncio

from compass_agent.schemas import TenantScope
from compass_agent.tools.builtin.database_query.handler import registration
from compass_agent.tools.contracts import PhaseCanceled


def provider(connections):
    async def load(trusted, control, emit):
        if not isinstance(trusted.scope, TenantScope):
            return ()

        from compass_agent.documents.client import restrict_connections, verify
        from compass_agent.execution_context import current_execution

        journal = current_execution.get()
        snapshot = getattr(journal, "search_snapshot", None)
        if snapshot and "detail" not in snapshot["kinds"]:
            return ()

        async def current():
            if snapshot and not await asyncio.to_thread(
                verify, journal.store.source_settings, trusted, journal.run_id, kinds=["detail"]
            ):
                return ()

            loading = asyncio.create_task(
                asyncio.to_thread(connections.bindings_for_run, trusted.scope)
            )
            try:
                return restrict_connections(await asyncio.shield(loading), snapshot)
            except asyncio.CancelledError:
                await asyncio.wait({loading}, timeout=0.5)
                raise PhaseCanceled(cleanup_confirmed=loading.done()) from None

        bindings = await current()
        if snapshot and not bindings:
            return ()
        control.require_running()
        return (registration(bindings, emit=emit, current=current),)

    return load
