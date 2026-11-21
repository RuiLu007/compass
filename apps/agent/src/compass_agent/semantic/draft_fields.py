"""Project suggested copy and the verified source into the editable metric form."""

import re

from pydantic import BaseModel, ValidationInfo, field_validator


class SuggestedDraftFields(BaseModel):
    name: str | None = None
    code: str | None = None
    domain: str | None = None
    summary: str | None = None
    responsible: str | None = None
    grain: str | None = None
    unit: str | None = None
    formula: str | None = None
    event_time: str | None = None
    timezone: str | None = None
    aggregation: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def editable_text(cls, value, info: ValidationInfo):
        if not isinstance(value, str):
            return None
        value = value.strip()
        limit = 2400 if info.field_name in {"summary", "formula"} else 160
        if not value or len(value) > limit:
            return None
        if info.field_name == "code" and not re.fullmatch(r"[a-z][a-z0-9_.-]{0,119}", value):
            return None
        return value


def result_preview(original, suggested, ref):
    fields = SuggestedDraftFields.model_validate(original).model_dump(exclude_none=True)
    for key in ("dimensions", "filters"):
        fields[key] = [s[:160] for s in original.get(key, []) if isinstance(s, str) and s.strip()][
            :40
        ]
    fields.update(
        SuggestedDraftFields.model_validate(suggested or {}).model_dump(exclude_none=True)
    )
    fields.setdefault("code", "open_" + str(ref).replace("-", "")[:12])
    return fields


def confirmed_fields(original, fields, ref):
    scopes = {"owner_scope_id", "usage_scope_id"}
    lists = {"dimensions", "filters"}
    valid_keys = SuggestedDraftFields.model_fields.keys() | scopes | lists
    if fields.keys() <= valid_keys and all(isinstance(fields.get(k, []), list) for k in lists):
        return fields
    repaired = result_preview(original, fields, ref)
    repaired.update({key: fields[key] for key in scopes if fields.get(key)})
    return repaired
