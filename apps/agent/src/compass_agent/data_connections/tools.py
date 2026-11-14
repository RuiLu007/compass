"""Application composition of query tools from current workspace authorization."""

import asyncio

from compass_agent.schemas import TenantScope
from compass_agent.tools.builtin.database_query.handler import registration
from compass_agent.tools.contracts import PhaseCanceled


def provider(connections):
    async def load(trusted, control, emit):
        if not isinstance(trusted.scope, TenantScope):
            return ()

        async def current():
            loading = asyncio.create_task(
                asyncio.to_thread(connections.bindings_for_run, trusted.scope)
            )
            try:
                return await asyncio.shield(loading)
            except asyncio.CancelledError:
                await asyncio.wait({loading}, timeout=0.5)
                raise PhaseCanceled(cleanup_confirmed=loading.done()) from None

        bindings = await current()
        control.require_running()
        return (registration(bindings, emit=emit, current=current),)

    return load
