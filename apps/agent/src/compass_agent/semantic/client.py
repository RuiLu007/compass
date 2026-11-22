"""Authenticated calls to the Web-owned Registry. No local catalog fallback."""

import asyncio
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import httpx

from compass_agent.models.errors import ModelError
from compass_agent.tools.contracts import PhaseCanceled


class SourceUnavailable(ModelError):
    def __init__(self, status=503, retry_after=None):
        super().__init__("source_authorization_unavailable", status)
        self.retry_after: float | None = None
        if retry_after is not None:
            try:
                self.retry_after = (
                    float(int(retry_after))
                    if retry_after.isdecimal()
                    else max(
                        0.0,
                        (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds(),
                    )
                )
            except (TypeError, ValueError, OverflowError):
                pass


def enabled(settings, workspace_id):
    return str(workspace_id) in {
        v.strip() for v in settings.compass_semantic_workspaces.split(",") if v.strip()
    }


def pin_manifest(settings, trusted, run_id, key):
    try:
        registry = request(settings, trusted, run_id, "manifest")
        if not isinstance(registry.get("releases"), list) or not registry.get("manifest_hash"):
            raise SourceUnavailable()
    except ModelError as exc:
        return {"registryIssue": {"status": "unavailable", "code": exc.code}}
    return {key: {"manifest": registry["manifest_hash"], "pins": registry["releases"]}}


def _wire(settings, trusted, run_id, op, args, pins, namespace):
    if not settings.compass_agent_service_token or trusted.scope.kind != "tenant":
        raise ModelError("forbidden", 403)
    endpoint = urljoin(settings.compass_web_authz_url, f"/api/internal/{namespace}/{op}")
    payload = {
        "actor_user_id": str(trusted.actor_user_id),
        "tenant_id": str(trusted.scope.tenant_id),
        "workspace_id": str(trusted.scope.workspace_id),
        "run_id": str(run_id),
        "args": args or {},
    }
    if pins is not None:
        payload["pins"] = pins
    return endpoint, payload, {"Authorization": "Bearer " + settings.compass_agent_service_token}


def _decode(res):
    if res.status_code in (401, 403, 404):
        raise ModelError("forbidden", 403)
    if res.status_code in (409, 422):
        raise ModelError("proposal_conflict", 409)
    if res.status_code == 429 or res.status_code >= 500:
        raise SourceUnavailable(res.status_code, res.headers.get("Retry-After"))
    res.raise_for_status()
    data = res.json()
    if not isinstance(data, dict):
        raise ValueError()
    return data


def request(settings, trusted, run_id, op, args=None, pins=None, *, namespace="semantic"):
    endpoint, payload, headers = _wire(settings, trusted, run_id, op, args, pins, namespace)
    try:
        with httpx.Client(timeout=4, trust_env=False, follow_redirects=False) as client:
            return _decode(client.post(endpoint, json=payload, headers=headers))
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceUnavailable() from exc


async def read_document_operation(settings, trusted, run_id, op, args):
    endpoint, payload, headers = _wire(settings, trusted, run_id, op, args, None, "documents")
    client = httpx.AsyncClient(timeout=4, trust_env=False, follow_redirects=False)
    try:
        async with client:
            return _decode(await client.post(endpoint, json=payload, headers=headers))
    except asyncio.CancelledError as exc:
        # The document owner serves a read-only request. Its HTTP resource has
        # exited the async client context before this cleanup receipt is issued.
        raise PhaseCanceled(cleanup_confirmed=client.is_closed) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceUnavailable() from exc
