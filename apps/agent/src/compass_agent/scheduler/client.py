"""Internal HTTP client exposing only trigger delivery and reconciliation."""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from random import uniform
from typing import Literal

import httpx
from pydantic import ValidationError

from compass_agent.schemas import TriggerAdmission


@dataclass(frozen=True)
class DeliveryResponse:
    state: Literal["accepted", "stale", "closed", "ready", "retry"]
    reason: str | None = None
    delay: float = 0
    permanent: bool = False


def wait_seconds(attempt: int, retry_after: str | None):
    base = min(120.0, 2.0 ** max(1, attempt))
    requested = (
        min(int(retry_after), 86400)
        if retry_after
        and retry_after.isascii()
        and retry_after.isdecimal()
        and len(retry_after) < 10
        else 0
    )
    if retry_after and not requested and not retry_after.isdecimal():
        try:
            timestamp = parsedate_to_datetime(retry_after)
            if timestamp.tzinfo is not None:
                requested = min(
                    86400, max(0, int((timestamp - datetime.now(UTC)).total_seconds()) + 1)
                )
        except (TypeError, ValueError, OverflowError):
            pass
    return max(requested, min(120.0, base + uniform(0, base / 4)))


class RunAdmissionClient:
    def __init__(self, url, token):
        self.http = httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=4,
            trust_env=False,
            follow_redirects=False,
        )

    async def close(self):
        await self.http.aclose()

    async def deliver(self, claim):
        operation = "reconcile" if claim.reconciliation else "submit"
        delay = wait_seconds(claim.attempt, None)
        try:
            response = await self.http.post(
                f"/internal/scheduler/triggers/{claim.trigger_id}/{operation}",
                json={"deliveryToken": str(claim.token)},
            )
            if response.status_code == (200 if claim.reconciliation else 202):
                result = TriggerAdmission.model_validate_json(response.content)
                if (result.outcome == "accepted" and result.run_id) or (
                    claim.reconciliation and result.outcome in {"closed", "ready"}
                ):
                    return DeliveryResponse(state=result.outcome)
            if response.status_code == 429:
                try:
                    reason = response.json()["error"].get("reasonCode")
                except (ValueError, KeyError, AttributeError, TypeError):
                    reason = None
                if reason not in {"runtime_cluster_backpressure", "runtime_tenant_backpressure"}:
                    reason = "runtime_backpressure"
                return DeliveryResponse(
                    state="retry",
                    reason=reason,
                    delay=wait_seconds(claim.attempt, response.headers.get("Retry-After")),
                )
            if response.status_code == 409:
                try:
                    error_code = response.json()["error"]["code"]
                except (ValueError, KeyError, TypeError):
                    error_code = None
                if error_code in {"scheduler_delivery_stale", "scheduler_lease_lost"}:
                    return DeliveryResponse(state="stale")
                return DeliveryResponse(
                    state="retry", reason="schedule_delivery_rejected", permanent=True
                )
            if response.status_code == 403:
                return DeliveryResponse(
                    state="retry", reason="schedule_permission_revoked", permanent=True
                )
            if response.status_code in {404, 422}:
                return DeliveryResponse(
                    state="retry", reason="schedule_delivery_rejected", permanent=True
                )
        except (httpx.HTTPError, ValidationError):
            return DeliveryResponse(
                state="retry", reason="schedule_delivery_unreachable", delay=delay
            )
        return DeliveryResponse(state="retry", reason="schedule_delivery_unavailable", delay=delay)
