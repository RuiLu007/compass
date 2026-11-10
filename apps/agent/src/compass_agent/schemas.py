from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, model_validator


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    service: Literal["compass-agent"] = "compass-agent"


class DemoOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"] = "1"
    source: Literal["synthetic-fixture"] = "synthetic-fixture"
    currency: Literal["CNY"] = "CNY"
    start_date: date
    end_date: date
    revenue_minor: int = Field(ge=0)
    orders: int = Field(ge=0)
    days: int = Field(ge=1)


# Feature 993 HTTP contracts. Identity is carried separately by the trusted BFF.


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlatformScope(StrictModel):
    kind: Literal["platform"]


class TenantScope(StrictModel):
    kind: Literal["tenant"]
    tenant_id: UUID
    workspace_id: UUID


class TrustedContext(StrictModel):
    actor_user_id: UUID
    scope: Annotated[PlatformScope | TenantScope, Field(discriminator="kind")]
    purpose: Literal["manage", "test", "chat", "module_read"]


class Capability(StrictModel):
    state: Literal["supported", "unsupported", "unknown"]
    reason: str


class ModelCapabilities(StrictModel):
    sync: Capability
    stream: Capability
    tools: Capability
    thinking: Capability
    json_output: Capability
    schema_output: Capability
    reasoning_efforts: list[str]


class CatalogItem(StrictModel):
    id: str
    vendor: str
    api_model: str
    kind: Literal["llm", "decision", "unsupported"]
    adapter_supported: bool
    chat_supported: bool = False
    source: Literal["litellm", "project_verified", "systemone"]
    base_url: str | None = None
    capabilities: ModelCapabilities


class CatalogPage(StrictModel):
    items: list[CatalogItem]
    total: int
    page: int
    page_size: int
    sdk_version: str


class ProviderCreate(StrictModel):
    operation_id: UUID
    catalog_id: str = Field(min_length=1, max_length=200)
    api_model: str = Field(min_length=1, max_length=200)
    alias: str = Field(min_length=1, max_length=80)
    base_url: str = Field(default="", max_length=2048)
    api_key: str = Field(default="", max_length=4096, repr=False)
    connection_params: dict | None = Field(default=None, repr=False)
    enabled: bool = False
    chat_visible: bool = False


class ProviderPatch(StrictModel):
    operation_id: UUID
    expected_version: int = Field(ge=1)
    catalog_id: str | None = Field(default=None, min_length=1, max_length=200)
    api_model: str | None = Field(default=None, min_length=1, max_length=200)
    alias: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    connection_params: dict | None = Field(default=None, repr=False)
    enabled: bool | None = None
    chat_visible: bool | None = None


class SavedProvider(StrictModel):
    provider_id: UUID
    version: int
    operation_id: UUID


class TestSummary(StrictModel):
    call_id: UUID
    status: str
    binding_version: int
    started_at: datetime
    capabilities: list[str]
    stale: bool


class ProviderView(StrictModel):
    id: UUID
    catalog_id: str
    vendor: str
    api_model: str
    alias: str
    kind: Literal["llm", "decision"]
    base_url: str
    has_api_key: bool
    has_connection_params: bool = False
    enabled: bool
    chat_visible: bool
    chat_supported: bool
    version: int
    binding_version: int
    capabilities: ModelCapabilities
    latest_test: TestSummary | None = None
    created_at: datetime
    updated_at: datetime


class ProviderPage(StrictModel):
    items: list[ProviderView]
    total: int
    page: int
    page_size: int


class AvailableModel(StrictModel):
    id: UUID
    alias: str
    api_model: str
    kind: Literal["llm"]
    capabilities: ModelCapabilities


class AvailableModels(StrictModel):
    items: list[AvailableModel]


class ModelChangeView(StrictModel):
    operation_id: UUID
    provider_id: UUID
    actor_user_id: UUID
    before_version: int | None
    after_version: int
    changed_fields: list[str]
    created_at: datetime


class ModelChangePage(StrictModel):
    items: list[ModelChangeView]
    total: int
    page: int
    page_size: int


class ToolFunction(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    description: str = Field(default="", max_length=2000)
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolDefinition(StrictModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ToolCallFunction(StrictModel):
    name: str
    arguments: str


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def validate_role_fields(self):
        if self.role == "tool" and (not self.tool_call_id or self.content is None):
            raise ValueError("tool message requires content and tool_call_id")
        if self.role != "tool" and self.tool_call_id is not None:
            raise ValueError("tool_call_id requires tool role")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("tool_calls requires assistant role")
        if self.content is None and not self.tool_calls:
            raise ValueError("message requires content or tool calls")
        return self


class GenerateInput(StrictModel):
    kind: Literal["generate"] = "generate"
    messages: list[ChatMessage] = Field(min_length=1, max_length=64)
    stream: bool = False
    thinking: bool = False
    reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=16)
    output_format: Literal["text", "json", "schema"] = "text"
    output_schema: dict | None = None
    max_tokens: int = Field(default=8192, ge=1, le=32768)
    continuation_token: str | None = Field(default=None, max_length=1048576, repr=False)

    @model_validator(mode="after")
    def validate_options(self):
        if not self.thinking and self.reasoning_effort is not None:
            raise ValueError("reasoning_effort requires thinking")
        if (self.output_format == "schema") != (self.output_schema is not None):
            raise ValueError("output_schema is required only for schema mode")
        return self


class DecisionQuestion(StrictModel):
    type: Literal["choice", "noul", "score"]
    instructions: str | dict | list = ""
    criteria: dict[str, str | dict | list | None] | list[str | dict | list] | None = None

    @model_validator(mode="after")
    def validate_criteria(self):
        if self.type == "choice" and (
            not isinstance(self.criteria, dict) or not 1 <= len(self.criteria) <= 255
        ):
            raise ValueError("choice requires 1..255 named criteria")
        if self.type == "score" and (
            not isinstance(self.criteria, list) or not 2 <= len(self.criteria) <= 255
        ):
            raise ValueError("score requires 2..255 ordered criteria")
        if (
            self.type == "noul"
            and self.criteria is not None
            and (not isinstance(self.criteria, dict) or set(self.criteria) != {"true", "false"})
        ):
            raise ValueError("noul criteria must contain true and false")
        return self


class DecisionInput(StrictModel):
    kind: Literal["decision"] = "decision"
    state: str | dict | list
    questions: dict[str, DecisionQuestion] = Field(min_length=1, max_length=16)
    stream: Literal[False] = False


class TestCallInput(StrictModel):
    call_id: UUID
    input: Annotated[GenerateInput | DecisionInput, Field(discriminator="kind")]


class MainModelSelection(StrictModel):
    mode: Literal["fixed", "auto"]
    provider_id: UUID | None = None

    @model_validator(mode="after")
    def validate_selection(self):
        if (self.mode == "fixed") != (self.provider_id is not None):
            raise ValueError("fixed requires provider_id; auto must omit it")
        return self


# Feature 989: a single, versioned Agent Run stream contract.  These models stay
# top-level so the repository contract exporter can discover every named branch.

RUN_STREAM_PROTOCOL: Literal["compass.run"] = "compass.run"
RUN_STREAM_VERSION: Literal[1] = 1
RUN_STREAM_LIMITS = {
    "frameBytes": 256 * 1024,
    "streamBytes": 2 * 1024 * 1024,
    "events": 96,
    "batchItems": 16,
    "batchBytes": 32 * 1024,
    "batchWaitMs": 100,
    "heartbeatMs": 15_000,
    "extensionBytes": 4 * 1024,
    "jsonDepth": 20,
    "answerBytes": 32 * 1024,
    "fileBytes": 128 * 1024,
    "fileRows": 200,
    "steps": 6,
    "toolsPerStep": 4,
}

RunUUID = Annotated[str, Field(pattern=r"^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$")]
RunToolId = Annotated[str, Field(min_length=1, max_length=200)]
RunDay = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
RunRowCount = Annotated[int, Field(ge=1, le=RUN_STREAM_LIMITS["fileRows"])]

RunToolError = Literal[
    "unknown_tool",
    "invalid_parameters",
    "retail_unavailable",
    "retail_query_failed",
    "retail_query_timeout",
    "result_limit",
]
RunError = Literal[
    "model_failed",
    "model_incomplete",
    "model_unavailable",
    "run_timeout",
    "context_limit",
    "step_limit",
    "internal_error",
]
RunStopCause = Literal["user", "disconnected", "shutdown"]
RunCleanupCause = RunStopCause | Literal["deadline", "execution_error"]
RunTerminalStatus = Literal["completed", "failed", "canceled", "cleanup_unconfirmed"]


class RunContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


class AnalysisHistoryTurn(RunContractModel):
    question: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=RUN_STREAM_LIMITS["answerBytes"])


class AnalysisRunInput(RunContractModel):
    # JSON carries UUID text; keep the rest of the Run contract strictly typed.
    request_id: UUID = Field(alias="requestId", strict=False)
    question: str = Field(min_length=1, max_length=8000)
    history: list[AnalysisHistoryTurn] = Field(default_factory=list, max_length=12)
    selection: MainModelSelection

    @model_validator(mode="after")
    def validate_visible_context_bytes(self):
        # Match the BFF's newline-separated byte accounting before any Run can
        # route, invoke a model, or open a tool connection.
        visible = [
            self.question,
            *(value for turn in self.history for value in (turn.question, turn.answer)),
        ]
        if len("\n".join(visible).encode()) > 32 * 1024:
            raise ValueError("visible context exceeds 32 KiB")
        return self


class RunPeriod(RunContractModel):
    start: RunDay
    end: RunDay


class ChannelRunParams(RunContractModel):
    dimension: Literal["channel"]
    period: RunPeriod
    compare_to: RunPeriod | None = Field(alias="compareTo")


class ProductRunParams(RunContractModel):
    dimension: Literal["product"]
    period: RunPeriod
    compare_to: RunPeriod | None = Field(alias="compareTo")
    channel_code: Annotated[str, Field(pattern=r"^[A-Z0-9_]{1,24}$")] = Field(alias="channelCode")


RetailRunParams = Annotated[
    ChannelRunParams | ProductRunParams,
    Field(discriminator="dimension"),
]


class RunEmpty(RunContractModel):
    pass


class RunRoutedPayload(RunContractModel):
    provider_id: RunUUID = Field(alias="providerId")
    model_label: Annotated[str, Field(min_length=1, max_length=160)] = Field(alias="modelLabel")
    scene_id: Annotated[str, Field(pattern=r"^SC-[0-9]{2}$")] = Field(alias="sceneId")
    tools_enabled: bool = Field(alias="toolsEnabled")


class StepStartedPayload(RunContractModel):
    step_id: RunUUID = Field(alias="stepId")
    step_index: Annotated[int, Field(ge=1, le=RUN_STREAM_LIMITS["steps"])] = Field(
        alias="stepIndex"
    )
    model_call_id: RunUUID = Field(alias="modelCallId")


class ToolEventKey(RunContractModel):
    step_id: RunUUID = Field(alias="stepId")
    tool_call_id: RunToolId = Field(alias="toolCallId")
    tool_index: Annotated[int, Field(ge=1, le=RUN_STREAM_LIMITS["toolsPerStep"])] = Field(
        alias="toolIndex"
    )


class ToolStartedPayload(ToolEventKey):
    tool_kind: Literal["retail_sales", "unavailable"] = Field(alias="toolKind")
    params: RetailRunParams | None


class ToolSucceededPayload(ToolEventKey):
    status: Literal["ok"]
    row_count: RunRowCount = Field(alias="rowCount")


class ToolEmptyPayload(ToolEventKey):
    status: Literal["empty"]
    row_count: Literal[0] = Field(alias="rowCount")


class ToolFailedPayload(ToolEventKey):
    status: Literal["error"]
    error_code: RunToolError = Field(alias="errorCode")


ToolFinishedPayload = Annotated[
    ToolSucceededPayload | ToolEmptyPayload | ToolFailedPayload,
    Field(discriminator="status"),
]


class StepFinishedPayload(RunContractModel):
    step_id: RunUUID = Field(alias="stepId")
    status: Literal["completed", "failed", "canceled"]


class AnalysisFileNone(RunContractModel):
    status: Literal["none"]


class AnalysisFileUnavailable(RunContractModel):
    status: Literal["unavailable"]
    code: Literal["file_limit", "file_generation_failed"]


class AnalysisFileReady(RunContractModel):
    status: Literal["ready"]
    step_id: RunUUID = Field(alias="stepId")
    tool_call_id: RunToolId = Field(alias="toolCallId")
    filename: Annotated[
        str,
        Field(pattern=r"^retail-products-[a-f0-9-]{36}\.csv$"),
    ]
    media_type: Literal["text/csv; charset=utf-8"] = Field(alias="mediaType")
    row_count: RunRowCount = Field(alias="rowCount")
    byte_length: Annotated[int, Field(ge=1, le=RUN_STREAM_LIMITS["fileBytes"])] = Field(
        alias="byteLength"
    )
    content_base64: Annotated[
        str,
        Field(
            min_length=4,
            max_length=4 * ((RUN_STREAM_LIMITS["fileBytes"] + 2) // 3),
        ),
    ] = Field(alias="contentBase64")


AnalysisFile = Annotated[
    AnalysisFileNone | AnalysisFileUnavailable | AnalysisFileReady,
    Field(discriminator="status"),
]


class RunCompleted(RunContractModel):
    status: Literal["completed"]
    final_answer: Annotated[
        str,
        Field(min_length=1, max_length=RUN_STREAM_LIMITS["answerBytes"]),
    ] = Field(alias="finalAnswer")
    file: AnalysisFile


class RunFailed(RunContractModel):
    status: Literal["failed"]
    error_code: RunError = Field(alias="errorCode")


class RunCanceled(RunContractModel):
    status: Literal["canceled"]
    reason: RunStopCause
    cleanup: Literal["confirmed"]


class RunCleanupUnconfirmed(RunContractModel):
    status: Literal["cleanup_unconfirmed"]
    reason: RunCleanupCause
    cleanup: Literal["unconfirmed"]


RunResult = Annotated[
    RunCompleted | RunFailed | RunCanceled | RunCleanupUnconfirmed,
    Field(discriminator="status"),
]


class RunExtensionPayload(RunContractModel):
    name: Annotated[
        str,
        Field(max_length=96, pattern=r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$"),
    ]
    version: int = Field(ge=1, le=65535)
    required: bool
    data: dict[str, JsonValue]


class RunStarted(RunContractModel):
    type: Literal["run.started"]
    payload: RunEmpty


class RunRouted(RunContractModel):
    type: Literal["run.routed"]
    payload: RunRoutedPayload


class StepStarted(RunContractModel):
    type: Literal["step.started"]
    payload: StepStartedPayload


class ToolStarted(RunContractModel):
    type: Literal["tool.started"]
    payload: ToolStartedPayload


class ToolFinished(RunContractModel):
    type: Literal["tool.finished"]
    payload: ToolFinishedPayload


class StepFinished(RunContractModel):
    type: Literal["step.finished"]
    payload: StepFinishedPayload


class StreamExtension(RunContractModel):
    type: Literal["stream.extension"]
    payload: RunExtensionPayload


RunProgressEvent = Annotated[
    RunRouted | StepStarted | ToolStarted | ToolFinished | StepFinished | StreamExtension,
    Field(discriminator="type"),
]


class StreamBatch(RunContractModel):
    type: Literal["stream.batch"]
    payload: list[RunProgressEvent] = Field(
        min_length=1,
        max_length=RUN_STREAM_LIMITS["batchItems"],
    )


class RunFinished(RunContractModel):
    type: Literal["run.finished"]
    payload: RunResult


RunStreamEvent = Annotated[
    RunStarted
    | RunRouted
    | StepStarted
    | ToolStarted
    | ToolFinished
    | StepFinished
    | StreamExtension
    | StreamBatch
    | RunFinished,
    Field(discriminator="type"),
]


class RunPacket(RunContractModel):
    protocol: Literal["compass.run"]
    version: Literal[1]
    request_id: RunUUID = Field(alias="requestId")
    run_id: RunUUID = Field(alias="runId")
    seq: int = Field(ge=1, le=RUN_STREAM_LIMITS["events"])
    event: RunStreamEvent


class AnalysisCancelAccepted(RunContractModel):
    run_id: RunUUID = Field(alias="runId")
    status: Literal["accepted"]


class AnalysisCancelAlreadyTerminal(RunContractModel):
    run_id: RunUUID = Field(alias="runId")
    status: Literal["already_terminal"]
    terminal_status: RunTerminalStatus = Field(alias="terminalStatus")


class AnalysisCancelAck(
    RootModel[
        Annotated[
            AnalysisCancelAccepted | AnalysisCancelAlreadyTerminal,
            Field(discriminator="status"),
        ]
    ]
):
    pass


class ChatCallInput(StrictModel):
    call_id: UUID
    provider_id: UUID | None = None
    main_model: MainModelSelection | None = None
    input: GenerateInput

    @model_validator(mode="after")
    def validate_selection(self):
        if (self.provider_id is None) == (self.main_model is None):
            raise ValueError("provide either provider_id or main_model")
        return self

    def selection(self):
        return self.main_model or MainModelSelection(mode="fixed", provider_id=self.provider_id)


class ModelUsage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class OutputValidation(StrictModel):
    status: Literal["not_requested", "valid", "invalid"] = "not_requested"
    mode: Literal["text", "json", "schema", "decision"] = "text"
    paths: list[str] = Field(default_factory=list)


class DecisionAnswer(StrictModel):
    type: Literal["choice", "noul", "score"]
    choice: str | None = None
    noul: float | None = Field(default=None, ge=0, le=1)
    score: float | None = None
    probabilities: dict[str, float] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    legend: dict[str, str | dict | list] | None = None


class AttemptSummary(StrictModel):
    attempt_id: UUID
    number: int = Field(ge=1, le=2)
    provider_id: UUID
    config_version: int = Field(ge=1)
    binding_version: int = Field(ge=1)
    started_at: datetime
    duration_ms: int = Field(ge=0)
    status: Literal["completed", "failed", "canceled", "incomplete"]
    error_code: str | None = Field(default=None, max_length=80)
    upstream_request_id: str | None = Field(default=None, max_length=200)
    upstream_outcome_unknown: bool = False
    sent: bool = False
    cleanup_confirmed: bool = True
    usage: ModelUsage = Field(default_factory=ModelUsage)


class ExecutionSummary(StrictModel):
    version: Literal[1] = 1
    max_attempts: Literal[1, 2]
    queue_wait_ms: int = Field(ge=0)
    stop_reason: Literal[
        "completed",
        "non_retryable",
        "attempts_exhausted",
        "deadline_exceeded",
        "canceled",
        "overloaded",
        "cleanup_unconfirmed",
    ]
    attempts: list[AttemptSummary] = Field(default_factory=list, max_length=2)


class CallOutcome(StrictModel):
    execution: ExecutionSummary | None = None
    call_id: UUID
    provider_id: UUID
    config_version: int
    status: Literal["completed", "failed", "canceled", "incomplete"]
    duration_ms: int
    usage: ModelUsage = Field(default_factory=ModelUsage)
    error_code: str | None = None
    upstream_outcome_unknown: bool = False
    requested_model: str
    response_model: str | None = None
    upstream_request_id: str | None = None
    finish_reason: str | None = None
    validation: OutputValidation = Field(default_factory=OutputValidation)


class CallResponse(CallOutcome):
    kind: Literal["generate", "decision"]
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    answers: dict[str, DecisionAnswer] | None = None
    raw_response: dict | None = Field(default=None, repr=False)
    continuation_token: str | None = Field(default=None, repr=False)


class CallRecordView(StrictModel):
    execution: ExecutionSummary | None = None
    call_id: UUID
    provider_id: UUID
    config_version: int
    binding_version: int
    purpose: Literal["test", "chat"]
    status: Literal["running", "completed", "failed", "canceled", "incomplete"]
    requested_model: str
    response_model: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    usage: ModelUsage
    error_code: str | None
    upstream_outcome_unknown: bool


class StartEvent(StrictModel):
    type: Literal["start"] = "start"
    call_id: UUID
    provider_id: UUID
    config_version: int


class TextDeltaEvent(StrictModel):
    type: Literal["text_delta"] = "text_delta"
    call_id: UUID
    text: str


class ToolDeltaEvent(StrictModel):
    type: Literal["tool_delta"] = "tool_delta"
    call_id: UUID
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


class ReasoningMetaEvent(StrictModel):
    type: Literal["reasoning_meta"] = "reasoning_meta"
    call_id: UUID
    thinking: bool
    reasoning_effort: str | None = None


class FinalEvent(CallOutcome):
    type: Literal["final"] = "final"
    continuation_token: str | None = Field(default=None, repr=False)


class ModelEvent(
    RootModel[
        Annotated[
            StartEvent | TextDeltaEvent | ToolDeltaEvent | ReasoningMetaEvent | FinalEvent,
            Field(discriminator="type"),
        ]
    ]
):
    pass


class ModelErrorDetail(StrictModel):
    code: str
    message: str
    requestId: str
    currentVersion: int | None = None


class ModelErrorResponse(StrictModel):
    error: ModelErrorDetail


# Feature 992: classification has exactly two business fields.
class Intent(StrictModel):
    category_id: str = Field(max_length=80)
    scene_id: str = Field(max_length=20)


class RoutingFeatures(StrictModel):
    task_type: str
    difficulty: Literal["L1", "L2", "L3", "undetermined"]
    response_mode: Literal["answer", "clarify", "refuse", "handoff"]
    required_capabilities: list[str] = Field(default_factory=list)


class ModelRoute(StrictModel):
    mode: Literal["fixed", "auto"]
    provider_id: UUID
    model: str
    reason: str
    dictionary_version: str
    routing_version: str


class RoutingMetrics(StrictModel):
    classifier_source: Literal["jev", "flash"] | None = None
    fallback_reason: str | None = None
    classification_calls: list[CallOutcome] = Field(default_factory=list, max_length=2)
    duration_ms: int = 0
    usage_complete: bool = False


class RoutedCallResponse(StrictModel):
    call_id: UUID
    status: Literal["completed", "failed", "canceled", "incomplete"]
    error_code: str | None = None
    intent: Intent | None = None
    features: RoutingFeatures | None = None
    routing: ModelRoute | None = None
    result: CallResponse | None = None
    metrics: RoutingMetrics = Field(default_factory=RoutingMetrics)


class RoutingStartEvent(StrictModel):
    type: Literal["start"] = "start"
    call_id: UUID


class ClassificationEvent(StrictModel):
    type: Literal["classification_status"] = "classification_status"
    call_id: UUID
    stage: Literal["jev", "flash"]


class RouteSelectedEvent(StrictModel):
    type: Literal["route_selected"] = "route_selected"
    call_id: UUID
    intent: Intent
    features: RoutingFeatures
    routing: ModelRoute


class RoutingFinalEvent(RoutedCallResponse):
    type: Literal["final"] = "final"


class RoutedModelEvent(
    RootModel[
        Annotated[
            RoutingStartEvent
            | ClassificationEvent
            | RouteSelectedEvent
            | TextDeltaEvent
            | ToolDeltaEvent
            | ReasoningMetaEvent
            | RoutingFinalEvent,
            Field(discriminator="type"),
        ]
    ]
):
    pass


type ReliabilityScenario = Literal[
    "success",
    "rate_limit",
    "unavailable",
    "exhausted",
    "authentication",
    "timeout",
    "interrupted",
    "schema_invalid",
    "cancel",
    "concurrency",
    "cleanup_failed",
]


class ShowcaseScenario(StrictModel):
    id: ReliabilityScenario
    title: str
    description: str
    expected: str


class ShowcaseCatalog(StrictModel):
    items: list[ShowcaseScenario]


class ShowcaseInput(StrictModel):
    call_id: UUID
    scenario: ReliabilityScenario


class GateSnapshot(StrictModel):
    capacity: int = Field(ge=1, le=64)
    max_inflight: int = Field(ge=1, le=256)
    active: int = Field(ge=0)
    inflight: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    available: int = Field(ge=0)
    peak_active: int = Field(ge=0)
    peak_inflight: int = Field(ge=0)


class ShowcaseCall(StrictModel):
    number: int = Field(ge=1, le=20)
    result: CallResponse
    preview: str = Field(max_length=8192)
    upstream_requests: int = Field(ge=0, le=2)
    accepted: bool


class ShowcaseReport(StrictModel):
    call_id: UUID
    scenario: ReliabilityScenario
    simulation: Literal[True] = True
    passed: bool
    duration_ms: int = Field(ge=0)
    calls: list[ShowcaseCall] = Field(max_length=20)
    capacity: GateSnapshot


class ShowcaseStart(StrictModel):
    type: Literal["start"] = "start"
    call_id: UUID


class ShowcaseProgress(StrictModel):
    type: Literal["progress"] = "progress"
    call_id: UUID
    number: int = Field(ge=1, le=20)
    message: str = Field(max_length=200)
    text: str = Field(default="", max_length=8192)
    capacity: GateSnapshot


class ShowcaseFinal(ShowcaseReport):
    type: Literal["final"] = "final"


class ShowcaseEvent(
    RootModel[
        Annotated[ShowcaseStart | ShowcaseProgress | ShowcaseFinal, Field(discriminator="type")]
    ]
):
    pass


class ObservabilityStatus(StrictModel):
    instance_id: str
    environment: str
    enabled: bool
    configured: bool
    sample_ratio: float
    queue_size: int = Field(ge=0)
    queue_capacity: int = Field(ge=1)
    dropped: int = Field(ge=0)
    export_failures: int = Field(ge=0)
    instrumentation_errors: int = Field(ge=0)
    last_export: Literal["never", "success", "failure"]


class ModelComparisonRow(StrictModel):
    provider_id: UUID
    model: str
    data_state: Literal["observed", "empty", "unavailable"]
    logical_count: int | None = None
    completed: int | None = None
    failed: int | None = None
    canceled: int | None = None
    incomplete: int | None = None
    success_rate: float | None = None
    mean_duration_ms: float | None = None
    attempt_count: int | None = None
    known_input_tokens: int | None = None
    known_output_tokens: int | None = None
    unknown_usage_attempts: int | None = None
    known_estimated_usd: float | None = None
    estimated_usd: float | None = None
    unknown_cost_attempts: int | None = None


class ModelComparison(StrictModel):
    from_timestamp: datetime
    to_timestamp: datetime
    environment: str
    sample_ratio: float
    items: list[ModelComparisonRow] = Field(max_length=2)
