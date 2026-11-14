"""Provider profile persistence and authorized read projections."""

from typing import Literal

from compass_agent.llm.capabilities import supported_parameters
from compass_agent.llm.catalog import catalog_token_limits
from compass_agent.models.entities import Provider
from compass_agent.models.errors import ModelError
from compass_agent.schemas import (
    AvailableGeneration,
    GenerationProfile,
    GenerationProfileInput,
    GenerationProfileView,
    ModelTokenLimits,
)


def stored_profile(row: Provider) -> GenerationProfile | None:
    if row.generation_profile is None:
        return None
    return GenerationProfile.model_validate(row.generation_profile)


def build_profile(
    row: Provider, command: GenerationProfileInput, previous: GenerationProfile | None
) -> GenerationProfile:
    if row.kind != "llm":
        raise ModelError("validation_failed")
    snapshot = previous.catalog_snapshot if previous else None
    if command.import_catalog:
        snapshot = catalog_token_limits(row.vendor, row.api_model)
        if snapshot is None:
            raise ModelError("catalog_token_limits_unavailable")
    profile = GenerationProfile(
        limits=command.limits, defaults=command.defaults, catalog_snapshot=snapshot
    )
    effective = profile.resolve_limits()
    default = command.defaults.max_tokens
    if default is not None and (
        (effective.max_output_tokens is not None and default > effective.max_output_tokens)
        or (
            effective.context_window_tokens is not None
            and default >= effective.context_window_tokens
        )
    ):
        raise ModelError("invalid_generation_defaults")
    return profile


def profile_view(row: Provider) -> GenerationProfileView | None:
    profile = stored_profile(row)
    if profile is None:
        return None
    effective = profile.resolve_limits()
    sources: dict[str, Literal["override", "litellm", "unknown"]] = {}
    for name, value in effective.model_dump().items():
        sources[name] = (
            "override"
            if name in profile.limits.model_fields_set
            else "litellm"
            if value is not None
            else "unknown"
        )
    return GenerationProfileView(
        limits=profile.limits,
        defaults=profile.defaults,
        catalog_snapshot=profile.catalog_snapshot,
        effective_limits=effective,
        limit_sources=sources,
    )


def available_generation(row: Provider) -> AvailableGeneration:
    profile = stored_profile(row)
    parameters = supported_parameters(row.vendor, row.api_model)
    default = profile.defaults.max_tokens if profile else None
    return AvailableGeneration(
        binding_version=row.binding_version,
        limits=profile.resolve_limits() if profile else ModelTokenLimits(),
        default_max_tokens=default if default is not None else 8192,
        supported_parameters=(
            [name for name in ("max_tokens", "temperature", "top_p") if name in parameters]
            if parameters is not None
            else None
        ),
    )
