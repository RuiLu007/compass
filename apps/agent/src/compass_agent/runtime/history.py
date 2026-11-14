"""Decode persisted request versions without reopening retired execution paths."""

from compass_agent.models.errors import ModelError


def decode_command(data: dict) -> dict:
    value = dict(data)
    if value.pop("showcase", None):
        raise ModelError("workflow_unavailable", 409)
    constraints = value.get("constraints")
    if constraints:
        constraints = dict(constraints)
        constraints.pop("verification", None)
        value["constraints"] = constraints or None
    return value


def same_request(saved: dict, current: dict) -> bool:
    # Only absent retired defaults are equivalent; a substantive retired request cannot
    # be replaced by a new ordinary request under the same idempotency key.
    value = dict(saved)
    if value.get("showcase") is not None:
        return False
    value.pop("showcase", None)
    constraints = value.get("constraints")
    if isinstance(constraints, dict):
        constraints = dict(constraints)
        if constraints.get("verification") is not None:
            return False
        constraints.pop("verification", None)
        value["constraints"] = constraints or None
    return value == current


def retry_payload(data: dict, saved: dict) -> dict:
    """Normalize only a previously accepted submission using its historical shape."""
    from compass_agent.schemas import SessionSubmit

    value = dict(data)
    marker = object()
    showcase = value.pop("showcase", marker)
    constraints = value.get("constraints")
    verification = marker
    if isinstance(constraints, dict):
        constraints = dict(constraints)
        verification = constraints.pop("verification", marker)
        value["constraints"] = constraints or None
    normalized = SessionSubmit.model_validate(value).model_dump(mode="json", by_alias=True)
    if normalized["constraints"] is None:
        normalized.pop("constraints")
    if showcase is not marker or "showcase" in saved:
        normalized["showcase"] = None if showcase is marker else showcase
    saved_constraints = saved.get("constraints") or {}
    if verification is not marker or "verification" in saved_constraints:
        normalized.setdefault("constraints", {})["verification"] = (
            None if verification is marker else verification
        )
    return normalized
