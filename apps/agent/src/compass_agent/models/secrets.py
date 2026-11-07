"""Purpose-bound credential encryption and bounded, stateless tool continuations."""

import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from compass_agent.llm.continuation import canonical as canonical
from compass_agent.llm.contracts import ContinuationPayload
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.models.errors import ModelError


class SecretStore:
    def __init__(self, key: str):
        try:
            self.cipher = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ModelError("unavailable", 503) from exc
        self.mac_key = hashlib.sha256(b"compass/model-operation/" + key.encode()).digest()

    def seal(self, purpose: str, value: dict) -> str:
        return self.cipher.encrypt(canonical({"purpose": purpose, "value": value})).decode()

    def open(self, token: str, purpose: str, ttl: int | None = None) -> dict:
        try:
            decoded = json.loads(self.cipher.decrypt(token.encode(), ttl=ttl))
            if decoded["purpose"] != purpose or not isinstance(decoded["value"], dict):
                raise ValueError()
            return decoded["value"]
        except (InvalidToken, ValueError, TypeError, KeyError) as exc:
            raise ModelError(
                "continuation_invalid" if purpose == "tool_continuation" else "unavailable",
                422 if ttl else 503,
            ) from exc

    def seal_key(self, provider_id, key: str, connection_params: dict | None = None) -> str:
        return self.seal(
            "provider_key",
            {
                "provider_id": str(provider_id),
                "key": key,
                "connection_params": connection_params or {},
            },
        )

    def open_connection(self, provider_id, encrypted: str) -> tuple[str, dict]:
        key = self.open_key(provider_id, encrypted)
        params = self.open(encrypted, "provider_key").get("connection_params", {})
        if not isinstance(params, dict):
            raise ModelError("unavailable", 503)
        return key, params

    def open_key(self, provider_id, encrypted: str) -> str:
        value = self.open(encrypted, "provider_key")
        if value.get("provider_id") != str(provider_id) or not isinstance(value.get("key"), str):
            raise ModelError("unavailable", 503)
        return value["key"]

    def fingerprint(self, value) -> str:
        return hmac.new(self.mac_key, canonical(value), hashlib.sha256).hexdigest()


class ModelContinuationCodec:
    """Purpose-limited adapter; never exposes provider-key operations to the runtime."""

    def __init__(self, store: SecretStore):
        self._store = store

    def open(self, token: str) -> ContinuationPayload:
        try:
            if len(token.encode()) > 1048576:
                raise RuntimeFailure("continuation_invalid")
            return ContinuationPayload.model_validate(
                self._store.open(token, "tool_continuation", ttl=900)
            )
        except (ModelError, ValidationError) as exc:
            raise RuntimeFailure("continuation_invalid") from exc

    def seal(self, payload: ContinuationPayload) -> str:
        token = self._store.seal("tool_continuation", payload.model_dump(mode="json"))
        if len(token.encode()) > 1048576:
            raise RuntimeFailure("continuation_too_large")
        return token
