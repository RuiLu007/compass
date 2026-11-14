"""Versioned, purpose/owner-bound runtime envelopes; never executable serialization."""

import hashlib
from uuid import UUID

from compass_agent.llm.continuation import canonical
from compass_agent.models.errors import ModelError
from compass_agent.models.secrets import SecretStore


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class PrivateState:
    def __init__(self, key: str):
        self.key = key

    def seal(self, run_id: UUID, purpose: str, value) -> bytes:
        return (
            SecretStore(self.key)
            .seal(
                "runtime-state",
                {"schemaVersion": 1, "runId": str(run_id), "kind": purpose, "data": value},
            )
            .encode()
        )

    def open(self, run_id: UUID, purpose: str, content: bytes):
        try:
            value = SecretStore(self.key).open(content.decode(), "runtime-state")
            if (value.get("schemaVersion"), value.get("runId"), value.get("kind")) != (
                1,
                str(run_id),
                purpose,
            ):
                raise ValueError()
            return value["data"]
        except (ModelError, ValueError, KeyError, UnicodeError):
            raise ModelError("runtime_state_invalid", 409) from None
