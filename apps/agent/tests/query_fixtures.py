"""Compose test connections through the same public tool registration as the product."""

from compass_agent.tools.builtin.database_query.handler import registration


def provider_for(resolve):
    async def provider(trusted, control, emit):
        binding = resolve(trusted.scope)
        return (registration((binding,) if binding else (), emit=emit),)

    return provider
