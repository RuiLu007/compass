"""Lazy access to the installed LiteLLM SDK and its provider/model resolver."""

import os
from functools import lru_cache

from compass_agent.llm.errors import RuntimeFailure


@lru_cache
def sdk():
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    import litellm

    from compass_agent.observability.privacy import install_dependency_filters

    install_dependency_filters()
    litellm.suppress_debug_info = True
    litellm.turn_off_message_logging = True
    return litellm


@lru_cache(maxsize=16384)
def resolve_model(identifier: str) -> tuple[str, str, str | None]:
    try:
        # Resolve explicit prefixes from the SDK registry without running provider auth.
        # Some get_llm_provider branches initiate OAuth while discovering api_base.
        prefix, separator, model = identifier.partition("/")
        if separator and prefix in sdk().provider_list:
            if not model:
                raise ValueError()
            return model, prefix, None
        model, vendor, _, base = sdk().get_llm_provider(model=identifier)
        if not model or not vendor:
            raise ValueError()
        return model, vendor, base
    except Exception as exc:
        raise RuntimeFailure("capability_not_supported") from exc
