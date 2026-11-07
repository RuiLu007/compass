"""Offline generation catalog and System One decision presets."""

from functools import lru_cache
from importlib.metadata import version

from compass_agent.llm.capabilities import capabilities
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.sdk import resolve_model, sdk
from compass_agent.schemas import CatalogItem, CatalogPage

PRESETS = [
    CatalogItem(
        id="deepseek/deepseek-v4-pro",
        vendor="deepseek",
        api_model="deepseek-v4-pro",
        kind="llm",
        adapter_supported=True,
        chat_supported=True,
        source="project_verified",
        base_url="https://api.deepseek.com",
        capabilities=capabilities("llm", "deepseek", "deepseek-v4-pro"),
    ),
    CatalogItem(
        id="deepseek/deepseek-flash",
        vendor="deepseek",
        api_model="deepseek-flash",
        kind="llm",
        adapter_supported=True,
        chat_supported=True,
        source="project_verified",
        base_url="https://api.deepseek.com",
        capabilities=capabilities("llm", "deepseek", "deepseek-flash"),
    ),
    CatalogItem(
        id="aliyun/decision-model-preview",
        vendor="aliyun",
        api_model="decision-model-preview",
        kind="decision",
        adapter_supported=True,
        source="systemone",
        base_url=None,
        capabilities=capabilities("decision", "aliyun"),
    ),
    CatalogItem(
        id="typesafe/jev-latest",
        vendor="typesafe",
        api_model="jev-latest",
        kind="decision",
        adapter_supported=True,
        source="systemone",
        base_url="https://api.typesafe.ai/v1",
        capabilities=capabilities("decision", "typesafe"),
    ),
]


@lru_cache
def catalog_items() -> dict[str, CatalogItem]:
    rows = {}
    for key, value in sdk().model_cost.items():
        if not isinstance(value, dict) or value.get("mode") not in {
            "chat",
            "responses",
            "completion",
        }:
            continue
        try:
            declared_vendor = str(value.get("litellm_provider", ""))
            prefix = key.partition("/")[0]
            identifier = (
                key
                if prefix in sdk().provider_list
                else (
                    declared_vendor + "/" + key if declared_vendor in sdk().provider_list else key
                )
            )
            api_model, vendor, base = resolve_model(identifier)
        except RuntimeFailure:
            continue
        rows[key] = CatalogItem(
            id=key,
            vendor=vendor,
            api_model=api_model,
            kind="llm",
            adapter_supported=True,
            chat_supported=True,
            source="litellm",
            base_url=base,
            capabilities=capabilities("llm", vendor, api_model),
        )
    rows.update({item.id: item for item in PRESETS})
    return rows


def catalog_page(q: str, vendor: str | None, page: int, page_size: int) -> CatalogPage:
    rows = sorted(
        (
            v
            for v in catalog_items().values()
            if (not vendor or v.vendor == vendor)
            and q.casefold() in (v.id + v.vendor + v.api_model).casefold()
        ),
        key=lambda v: (v.source == "litellm", v.vendor, v.id),
    )
    return CatalogPage(
        items=rows[(page - 1) * page_size : page * page_size],
        total=len(rows),
        page=page,
        page_size=page_size,
        sdk_version=version("litellm-core"),
    )


def binding(catalog_id: str, api_model: str) -> tuple[str, str, str, str | None]:
    namespace, separator, name = catalog_id.partition("/")
    if namespace in {"aliyun", "typesafe", "systemone"}:
        if not separator or not name.strip() or not api_model.strip():
            raise RuntimeFailure("validation_failed")
        return namespace, "decision", "systemone_decision", None
    _, vendor, _ = resolve_model(catalog_id)
    # API model names may themselves contain slashes (OpenRouter, Bedrock, HF).
    model, resolved_vendor, _ = resolve_model(vendor + "/" + api_model)
    if resolved_vendor != vendor:
        raise RuntimeFailure("capability_not_supported")
    return vendor, "llm", "litellm_chat", vendor + "/" + model
