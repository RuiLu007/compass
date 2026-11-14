"""Per-call HTTP boundary: one request, bounded reads and observed stream termination."""

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

from compass_agent.llm.errors import RuntimeErrorCode, RuntimeFailure, model_input_error
from compass_agent.schemas import ModelInputError

logger = logging.getLogger(__name__)


@dataclass
class CallResources:
    sent: bool = False
    http_status: int | None = None
    has_partial: bool = False
    saw_done: bool = False
    clients: list = field(default_factory=list)
    total_bytes: int = 0
    max_response_bytes: int = 2 * 1024 * 1024
    error_code: RuntimeErrorCode | None = None
    wire_finish_reason: str | None = None
    wire_response_model: str | None = None
    wire_request_id: str | None = None

    cleanup_confirmed: bool = True

    async def close(self):
        for client in reversed(self.clients):
            try:
                async with asyncio.timeout(1):
                    await client.aclose()
            except BaseException as exc:
                self.cleanup_confirmed = False
                if isinstance(exc, asyncio.CancelledError):
                    raise
                logger.warning("Model resource close failed; failure_type=%s", type(exc).__name__)


class DecodedResponseStream(httpx.AsyncByteStream):
    """Decode HTTP content before observing the provider's JSON/SSE protocol."""

    def __init__(self, response, resources):
        self.response = response
        self.resources = resources

    async def __aiter__(self):
        try:
            # Default chunks preserve SSE delivery without waiting for a fixed buffer size.
            async for chunk in self.response.aiter_bytes():
                yield chunk
        except httpx.DecodingError as exc:
            self.resources.error_code = "provider_protocol_error"
            raise RuntimeFailure("provider_protocol_error") from exc

    async def aclose(self):
        await self.response.aclose()


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, stream, resources, is_sse):
        self.stream = stream
        self.resources = resources
        self.is_sse = is_sse
        self.buffer = bytearray()
        self.data_lines: list[bytes] = []

    def observe(self, value):
        if not isinstance(value, dict):
            return
        for field_name in ("model", "id"):
            if value.get(field_name) is not None and (
                not isinstance(value[field_name], str) or len(value[field_name]) > 200
            ):
                self.resources.error_code = "provider_protocol_error"
                raise RuntimeFailure("provider_protocol_error")
        if isinstance(value.get("model"), str):
            self.resources.wire_response_model = value["model"]
        if isinstance(value.get("id"), str):
            self.resources.wire_request_id = value["id"]
        for choice in value.get("choices") or []:
            reason = choice.get("finish_reason")
            if reason is not None:
                if reason not in {"stop", "tool_calls", "length", "content_filter", "refusal"}:
                    self.resources.error_code = "provider_protocol_error"
                    raise RuntimeFailure("provider_protocol_error")
                self.resources.wire_finish_reason = reason

    def payload(self, encoded):
        try:
            value = json.loads(encoded)
            self.observe(value)
            return value
        except (ValueError, TypeError, AttributeError) as exc:
            self.resources.error_code = "provider_protocol_error"
            raise RuntimeFailure("provider_protocol_error") from exc

    def line(self, line):
        if line:
            if line.startswith(b"data:"):
                value = line[5:]
                self.data_lines.append(value[1:] if value.startswith(b" ") else value)
            return None
        if not self.data_lines:
            return None
        payload = b"\n".join(self.data_lines).strip()
        self.data_lines.clear()
        if payload == b"[DONE]":
            self.resources.saw_done = True
            return b"data: [DONE]\n\n"
        value = self.payload(payload)
        if isinstance(value, dict) and isinstance(value.get("error"), dict):
            failure = model_input_error(value)
            if failure is not None:
                self.resources.error_code = failure
                raise RuntimeFailure(failure)
        # LiteLLM 1.106.6 scans for a sentinel substring before parsing JSON.
        # JSON-equivalent escapes preserve model text while making only real DONE a sentinel.
        normalized = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        normalized = normalized.replace(b"[DONE]", b"\\u005bDONE\\u005d")
        return b"data: " + normalized + b"\n\n"

    async def __aiter__(self):
        async for chunk in self.stream:
            self.resources.total_bytes += len(chunk)
            if self.resources.total_bytes > self.resources.max_response_bytes:
                self.resources.error_code = "response_too_large"
                raise RuntimeFailure("response_too_large")
            self.buffer.extend(chunk)
            if self.is_sse:
                while b"\n" in self.buffer:
                    line, _, remaining = self.buffer.partition(b"\n")
                    self.buffer = bytearray(remaining)
                    frame = self.line(line.rstrip(b"\r"))
                    if frame:
                        yield frame
            else:
                yield chunk
        # An SSE event requires its blank-line terminator; EOF is not a DONE frame.
        if not self.is_sse and self.buffer:
            self.payload(self.buffer)

    async def aclose(self):
        await self.stream.aclose()


class BoundedStream(httpx.AsyncByteStream):
    """Protocol-neutral reads; native provider frames are interpreted by LiteLLM."""

    def __init__(self, stream, resources):
        self.stream = stream
        self.resources = resources

    async def __aiter__(self):
        async for chunk in self.stream:
            self.resources.total_bytes += len(chunk)
            if self.resources.total_bytes > self.resources.max_response_bytes:
                self.resources.error_code = "response_too_large"
                raise RuntimeFailure("response_too_large")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


async def read_model_input_error(response: httpx.Response) -> ModelInputError | None:
    body = bytearray()
    try:
        async for chunk in response.aiter_bytes(chunk_size=4096):
            body.extend(chunk)
            if len(body) > 16 * 1024:
                return None
        return model_input_error(json.loads(body))
    except (ValueError, httpx.HTTPError):
        return None


class SingleAttemptTransport(httpx.AsyncBaseTransport):
    def __init__(self, resources, inner=None, observe_openai=True, *, classify_model_input=False):
        self.resources = resources
        self.observe_openai = observe_openai
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self.used = False
        self.classify_model_input = classify_model_input

    async def handle_async_request(self, request):
        if self.used:
            raise RuntimeFailure("provider_protocol_error")
        self.used = True
        self.resources.sent = True
        try:
            response = await self.inner.handle_async_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            self.resources.sent = False
            raise RuntimeFailure(
                "model_unavailable_upstream", recovery="not_sent_transient"
            ) from exc
        self.resources.http_status = response.status_code
        self.resources.clients.append(response)
        if not 200 <= response.status_code < 300:
            self.resources.error_code = (
                "model_authentication_failed"
                if response.status_code in {401, 403}
                else "model_rate_limited"
                if response.status_code == 429
                else "model_unavailable_upstream"
                if response.status_code >= 500
                else "provider_protocol_error"
            )
            if self.classify_model_input and response.status_code in {400, 422}:
                self.resources.error_code = (
                    await read_model_input_error(response) or self.resources.error_code
                )
            await response.aclose()
            raise RuntimeFailure(
                self.resources.error_code,
                recovery="rejected_transient"
                if response.status_code == 429 or 500 <= response.status_code < 600
                else "never",
            )
        headers = response.headers.copy()
        # The forwarded stream is decoded and SSE frames may be normalized below.
        headers.pop("content-encoding", None)
        headers.pop("content-length", None)
        return httpx.Response(
            status_code=response.status_code,
            headers=headers,
            stream=ObservedStream(
                DecodedResponseStream(response, self.resources),
                self.resources,
                "text/event-stream" in response.headers.get("content-type", ""),
            )
            if self.observe_openai
            else BoundedStream(DecodedResponseStream(response, self.resources), self.resources),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.inner.aclose()
