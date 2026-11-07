"""Fixed, admin-only Langfuse aggregation. No query language crosses the BFF."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from math import isfinite

import httpx

from compass_agent.models.errors import ModelError


def meta(key, value):
    return {
        "column": "metadata",
        "type": "stringObject",
        "key": key,
        "operator": "=",
        "value": value,
    }


def numeric(row, key):
    if row and key not in row:
        raise ValueError("missing aggregate")
    value = row.get(key)
    if value is None:
        return 0
    if type(value) not in {int, float} or not isfinite(value) or value < 0:
        raise ValueError("invalid aggregate")
    return value


async def model_comparison(settings, models, window, *, transport=None):
    hours = {"1h": 1, "24h": 24, "7d": 168}[window]
    now = datetime.now(UTC)
    start = now - timedelta(hours=hours)
    if (
        not settings.compass_langfuse_base_url
        or not settings.compass_langfuse_secret_key.get_secret_value()
    ):
        raise ModelError("unavailable", 503)
    semaphore = asyncio.Semaphore(4)
    results = []
    async with httpx.AsyncClient(
        base_url=settings.compass_langfuse_base_url,
        auth=(
            settings.compass_langfuse_public_key,
            settings.compass_langfuse_secret_key.get_secret_value(),
        ),
        timeout=2,
        follow_redirects=False,
        transport=transport,
    ) as client:

        async def query(filters, metrics):
            spec = {
                "view": "observations",
                "dimensions": [],
                "metrics": metrics,
                "filters": [
                    {
                        "column": "environment",
                        "type": "string",
                        "operator": "=",
                        "value": settings.compass_observability_environment,
                    },
                    *filters,
                ],
                "fromTimestamp": start.isoformat(),
                "toTimestamp": now.isoformat(),
                "config": {"row_limit": 100},
            }
            async with semaphore:
                async with client.stream(
                    "GET", "/api/public/v2/metrics", params={"query": json.dumps(spec)}
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 262144:
                            raise ValueError("aggregate too large")
                    payload = json.loads(body)
                    rows = payload["data"]
                    if not isinstance(rows, list) or len(rows) > 1:
                        raise ValueError("invalid aggregate shape")
                    return rows[0] if rows else {}

        async def one(model):
            selected = [meta("provider_id", str(model.id)), meta("model", model.api_model)]
            logical = [
                {"column": "name", "type": "string", "operator": "=", "value": "compass.logical"},
                meta("stage", "inference"),
                *selected,
            ]
            attempts = [
                {"column": "name", "type": "string", "operator": "=", "value": "compass.attempt"},
                meta("stage", "inference"),
                *selected,
            ]
            count = [{"measure": "count", "aggregation": "count"}]
            tasks = [
                query(
                    [*logical, meta("status", status)],
                    [*count, {"measure": "latency", "aggregation": "sum"}],
                )
                for status in ("completed", "failed", "canceled", "incomplete")
            ]
            tasks += [
                query(
                    attempts,
                    [
                        *count,
                        *[
                            {"measure": key, "aggregation": "sum"}
                            for key in ("inputTokens", "outputTokens", "totalCost")
                        ],
                    ],
                ),
                query([*attempts, meta("usage_complete", "false")], count),
                query([*attempts, meta("cost_state", "unknown")], count),
            ]
            row = {"provider_id": model.id, "model": model.api_model, "data_state": "unavailable"}
            values = await asyncio.gather(*tasks, return_exceptions=True)
            if any(isinstance(x, BaseException) for x in values):
                return row
            states, stats, usage_unknown, cost_unknown = values[:4], values[4], values[5], values[6]
            try:
                counts = [int(numeric(x, "count_count")) for x in states]
                total = sum(counts)
                unknown = int(numeric(cost_unknown, "count_count"))
                row.update(
                    data_state="observed" if total else "empty",
                    logical_count=total,
                    completed=counts[0],
                    failed=counts[1],
                    canceled=counts[2],
                    incomplete=counts[3],
                    success_rate=counts[0] / total if total else None,
                    mean_duration_ms=sum(numeric(x, "sum_latency") for x in states) / total
                    if total
                    else None,
                    attempt_count=int(numeric(stats, "count_count")),
                    known_input_tokens=int(numeric(stats, "sum_inputTokens")),
                    known_output_tokens=int(numeric(stats, "sum_outputTokens")),
                    unknown_usage_attempts=int(numeric(usage_unknown, "count_count")),
                    known_estimated_usd=numeric(stats, "sum_totalCost"),
                    unknown_cost_attempts=unknown,
                    estimated_usd=numeric(stats, "sum_totalCost") if not unknown else None,
                )
            except (ValueError, TypeError, AttributeError):
                return {
                    "provider_id": model.id,
                    "model": model.api_model,
                    "data_state": "unavailable",
                }
            return row

        try:
            async with asyncio.timeout(8):
                results = await asyncio.gather(*(one(model) for model in models))
        except TimeoutError:
            results = [
                {"provider_id": model.id, "model": model.api_model, "data_state": "unavailable"}
                for model in models
            ]
    return {
        "from_timestamp": start,
        "to_timestamp": now,
        "environment": settings.compass_observability_environment,
        "sample_ratio": settings.compass_observability_sample_ratio,
        "items": results,
    }
