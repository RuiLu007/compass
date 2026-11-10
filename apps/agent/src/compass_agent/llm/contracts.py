"""Request-owned execution values and adapter interfaces; no persistence or HTTP."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import Field, SecretStr

from compass_agent.llm.errors import RuntimeErrorInfo
from compass_agent.llm.policy import CallPolicy as CallPolicy
from compass_agent.llm.transport import CallResources
from compass_agent.schemas import (
    DecisionAnswer,
    DecisionInput,
    ExecutionSummary,
    GenerateInput,
    ModelUsage,
    OutputValidation,
    PlatformScope,
    ReasoningMetaEvent,
    StrictModel,
    TenantScope,
    TextDeltaEvent,
    ToolCall,
    ToolDeltaEvent,
)

type ModelKind = Literal["llm", "decision"]

type AdapterKind = Literal["litellm_chat", "systemone_decision"]

type TerminalStatus = Literal["completed", "failed", "canceled", "incomplete"]

type CapabilityMode = Literal["verified", "diagnostic"]

type ModelInput = Annotated[GenerateInput | DecisionInput, Field(discriminator="kind")]

type RuntimeDelta = TextDeltaEvent | ToolDeltaEvent | ReasoningMetaEvent

type DeltaSink = Callable[[RuntimeDelta], Awaitable[None]]

type ToolOutputValidation = Literal["strict", "agent_dispatch"]


@dataclass(frozen=True, slots=True)
class ModelSpec:
    provider_id: UUID
    config_version: int
    binding_version: int
    vendor: str
    kind: ModelKind
    adapter: AdapterKind
    api_model: str
    sdk_model: str | None
    api_base: str


@dataclass(frozen=True, slots=True)
class ProviderAccess:
    spec: ModelSpec
    api_key: SecretStr = field(repr=False)
    connection_params: dict = field(default_factory=dict, repr=False)


class ContinuationBinding(StrictModel):
    actor: UUID
    scope: Annotated[PlatformScope | TenantScope, Field(discriminator="kind")]
    provider: UUID
    binding_version: int = Field(ge=1)


class ContinuationPayload(StrictModel):
    binding: ContinuationBinding
    visible_count: int = Field(ge=1)
    visible_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    private_assistants: dict[str, str | dict] = Field(default_factory=dict, repr=False)
    allowed_tool_call_ids: list[str] = Field(min_length=1, max_length=16)


class ContinuationCodec(Protocol):
    def open(self, token: str) -> ContinuationPayload:
        """Verify purpose and Fernet TTL=900; invalid tokens raise RuntimeFailure."""
        ...

    def seal(self, payload: ContinuationPayload) -> str:
        """Encrypt only tool_continuation payload and enforce ciphertext byte limit."""
        ...


@dataclass(frozen=True, slots=True)
class InvocationContext:
    call_id: UUID
    capability_mode: CapabilityMode
    continuation_binding: ContinuationBinding


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    context: InvocationContext
    access: ProviderAccess
    input: ModelInput = field(repr=False)
    policy: CallPolicy
    tool_output_validation: ToolOutputValidation = "strict"


@dataclass(frozen=True, slots=True)
class GenerationOutput:
    kind: Literal["generate"] = "generate"
    text: str = field(default="", repr=False)
    tool_calls: tuple[ToolCall, ...] = field(default=(), repr=False)
    continuation_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class DecisionOutput:
    kind: Literal["decision"] = "decision"
    answers: dict[str, DecisionAnswer] = field(default_factory=dict, repr=False)
    raw_response: dict | None = field(default=None, repr=False)


type ModelOutput = GenerationOutput | DecisionOutput


@dataclass(slots=True)
class ProviderProgress:
    output: ModelOutput = field(repr=False)
    usage: ModelUsage = field(default_factory=ModelUsage)
    validation: OutputValidation = field(default_factory=OutputValidation)
    response_model: str | None = None
    upstream_request_id: str | None = None
    finish_reason: str | None = None
    normal_eof: bool = False

    @property
    def text(self) -> str:
        return self.output.text if isinstance(self.output, GenerationOutput) else ""

    @text.setter
    def text(self, value: str) -> None:
        assert isinstance(self.output, GenerationOutput)
        self.output = replace(self.output, text=value)

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return self.output.tool_calls if isinstance(self.output, GenerationOutput) else ()

    @tool_calls.setter
    def tool_calls(self, value: tuple[ToolCall, ...]) -> None:
        assert isinstance(self.output, GenerationOutput)
        self.output = replace(self.output, tool_calls=tuple(value))

    @property
    def continuation_token(self) -> str | None:
        return self.output.continuation_token if isinstance(self.output, GenerationOutput) else None

    @continuation_token.setter
    def continuation_token(self, value: str | None) -> None:
        assert isinstance(self.output, GenerationOutput)
        self.output = replace(self.output, continuation_token=value)


@dataclass(slots=True)
class CallSession:
    resources: CallResources
    progress: ProviderProgress
    emit: DeltaSink | None = field(default=None, repr=False)
    observation: object | None = field(default=None, repr=False)


class ProviderAdapter(Protocol):
    async def invoke(self, request: ExecutionRequest, session: CallSession) -> ModelOutput:
        """Normalize the provider protocol, update progress, and return validated output.

        RuntimeFailure carries a safe error code; CancelledError must propagate.
        Register clients/responses in session.resources before waiting on network IO.
        Update normalized partial output before awaiting the public delta sink.
        """
        ...


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    call_id: UUID
    provider_id: UUID
    config_version: int
    binding_version: int
    requested_model: str
    status: TerminalStatus
    duration_ms: int
    output: ModelOutput = field(repr=False)
    usage: ModelUsage
    validation: OutputValidation
    response_model: str | None = None
    upstream_request_id: str | None = None
    finish_reason: str | None = None
    error: RuntimeErrorInfo | None = None
    upstream_outcome_unknown: bool = False
    upstream_sent: bool = False
    adapter_started: bool = False
    cleanup_confirmed: bool = True
    execution: ExecutionSummary | None = None


class RuntimeCancelled(asyncio.CancelledError):
    def __init__(self, result: RuntimeResult):
        self.result = result
        super().__init__("model call canceled")
