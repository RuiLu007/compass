"""Factory defaults for callers constructing an explicit Agent definition."""

from compass_agent.prompt_assets import factory_text

SYSTEM_PROMPT = factory_text("shared/fragments/base.md")
DATABASE_PROMPT = factory_text("shared/tools/database-query.md")
