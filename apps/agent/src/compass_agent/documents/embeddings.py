"""Build document and query vectors with the configured Agent-owned OpenAI connection."""

import asyncio
import math
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from compass_agent.models.entities import Provider
from compass_agent.models.errors import ModelError
from compass_agent.models.secrets import SecretStore
from compass_agent.models.validation import validate_endpoint

MODEL = "text-embedding-3-small"
DIMENSIONS = 1024
INDEX = f"{MODEL}:{DIMENSIONS}:v1"


class EmbeddingUnavailable(Exception):
    """Safe public failure reason, without credentials or provider response bodies."""


def _connection(engine, settings):
    with Session(engine) as session:
        stmt = select(Provider).where(Provider.enabled, Provider.vendor == "openai")
        if settings.compass_embedding_provider_id:
            stmt = stmt.where(Provider.id == UUID(settings.compass_embedding_provider_id))
        rows = list(session.scalars(stmt.limit(2)))
        if len(rows) != 1:
            raise EmbeddingUnavailable("select_one_enabled_openai_provider")
        provider = rows[0]
        endpoint = validate_endpoint(
            provider.base_url, provider.adapter, settings.compass_model_allowed_origins
        )
        key, params = SecretStore(settings.compass_model_encryption_key).open_connection(
            provider.id, provider.api_key_ciphertext
        )
        headers = {
            **params.get("default_headers", {}),
            **params.get("extra_headers", {}),
            "Authorization": f"Bearer {key}",
        }
        return (endpoint or "https://api.openai.com/v1").rstrip("/") + "/embeddings", headers


async def embed(engine, settings, texts: list[str]) -> list[list[float]]:
    try:
        url, headers = await asyncio.to_thread(_connection, engine, settings)
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "model": MODEL,
                    "dimensions": DIMENSIONS,
                    "input": texts,
                    "encoding_format": "float",
                },
            )
        if response.status_code != 200:
            raise EmbeddingUnavailable(f"openai_http_{response.status_code}")
        result = response.json()
        if result.get("model") != MODEL:
            raise EmbeddingUnavailable("openai_model_mismatch")
        rows = sorted(result["data"], key=lambda row: row["index"])
        vectors = [row["embedding"] for row in rows]
        if [r["index"] for r in rows] != list(range(len(texts))) or any(
            len(v) != DIMENSIONS
            or not all(isinstance(x, (float, int)) and math.isfinite(x) for x in v)
            or not any(v)
            for v in vectors
        ):
            raise EmbeddingUnavailable("invalid_openai_vectors")
        return vectors
    except (httpx.HTTPError, KeyError, TypeError, ValueError, ModelError) as exc:
        raise EmbeddingUnavailable("embedding_connection_or_response_invalid") from exc
