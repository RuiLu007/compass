"""Bounded Streamable HTTP sessions with an explicitly approved, pinned network target."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from contextlib import AsyncExitStack
from urllib.parse import urlsplit

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams, Tool, ToolListChangedNotification

from compass_agent.tools.contracts import ToolRejected, json_bytes
from compass_agent.tools.mcp.repository import ConnectionConfig


class EndpointPolicy:
    def __init__(self, allowed_origins: str, local_fixture_origins: str = ""):
        self.allowed = frozenset(
            filter(None, (s.strip().rstrip("/") for s in allowed_origins.split(",")))
        )
        self.local = frozenset(
            filter(None, (s.strip().rstrip("/") for s in local_fixture_origins.split(",")))
        )

    def validate(self, endpoint: str):
        try:
            url = urlsplit(endpoint)
            origin = f"{url.scheme}://{url.hostname}"
            if url.port and url.port != (443 if url.scheme == "https" else 80):
                origin += f":{url.port}"
            if (
                url.username
                or url.password
                or url.query
                or url.fragment
                or not url.hostname
                or "%" in url.netloc
                or any(ord(c) < 33 for c in endpoint)
                or not url.path.startswith("/")
                or ".." in url.path
            ):
                raise ValueError()
            if origin not in self.allowed | self.local or (
                url.scheme != "https" and origin not in self.local
            ):
                raise ValueError()
            if url.scheme not in {"http", "https"}:
                raise ValueError()
            return url, origin in self.local
        except ValueError:
            raise ToolRejected("mcp_endpoint_denied") from None

    async def resolve(self, endpoint: str) -> str:
        url, local = self.validate(endpoint)
        addresses = await asyncio.get_running_loop().getaddrinfo(
            url.hostname,
            url.port or (443 if url.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        values = {str(item[4][0]) for item in addresses}
        if not values:
            raise ToolRejected("mcp_endpoint_denied")
        for address in values:
            ip = ipaddress.ip_address(address)
            # Local fixture origins allow private addresses; metadata and link-local stay denied.
            if (
                ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
                or (not local and not ip.is_global)
            ):
                raise ToolRejected("mcp_endpoint_denied")
        return sorted(values)[0]


class LimitedStream(httpx2.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > 1024 * 1024:
                raise ToolRejected("mcp_response_limit")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class PinnedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, endpoint: str, address: str):
        self.endpoint = httpx2.URL(endpoint)
        self.address = address
        self.transport = httpx2.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        if request.url != self.endpoint:
            raise ToolRejected("mcp_endpoint_denied")
        # Keep Host and TLS SNI, while dialing the already validated IP without a second DNS lookup.
        headers = request.headers.copy()
        headers["host"] = self.endpoint.netloc.decode()
        pinned = httpx2.Request(
            request.method,
            request.url.copy_with(host=self.address),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": self.endpoint.host},
        )
        response = await self.transport.handle_async_request(pinned)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise ToolRejected("mcp_redirect_denied")
        response.stream = LimitedStream(response.stream)
        return response

    async def aclose(self):
        await self.transport.aclose()


class McpSession:
    """The owner task opens and closes AnyIO scopes; Run tasks only call the live client."""

    def __init__(self, config: ConnectionConfig, policy: EndpointPolicy):
        self.config, self.policy = config, policy
        self.changed = False
        self._calls: set[asyncio.Task] = set()
        self.tools: tuple[Tool, ...] = ()
        self.client: ClientSession | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.get_running_loop().create_future()
        self._task: asyncio.Task | None = None
        self._slots = asyncio.Semaphore(config.max_inflight)

    async def start(self, discovery_slots: asyncio.Semaphore):
        self._task = asyncio.create_task(self._serve(discovery_slots))
        await self._ready
        return self

    async def _message(self, message):
        if isinstance(message, ToolListChangedNotification):
            self.changed = True
            for task in tuple(self._calls):
                task.cancel()

    async def _serve(self, discovery_slots):
        try:
            async with AsyncExitStack() as stack:
                async with discovery_slots, asyncio.timeout(8):
                    address = await self.policy.resolve(self.config.endpoint)
                    headers = (
                        {"Authorization": "Bearer " + self.config.token.get_secret_value()}
                        if self.config.token.get_secret_value()
                        else {}
                    )
                    http = await stack.enter_async_context(
                        httpx2.AsyncClient(
                            transport=PinnedTransport(self.config.endpoint, address),
                            headers=headers,
                            trust_env=False,
                            follow_redirects=False,
                            timeout=httpx2.Timeout(20, connect=3),
                        )
                    )
                    read, write = await stack.enter_async_context(
                        streamable_http_client(
                            self.config.endpoint, http_client=http, max_sse_event_size=512 * 1024
                        )
                    )
                    self.client = await stack.enter_async_context(
                        ClientSession(
                            read, write, read_timeout_seconds=8, message_handler=self._message
                        )
                    )
                    initialized = await self.client.initialize()
                    if initialized.protocol_version != "2025-11-25":
                        raise ToolRejected("mcp_protocol_unsupported")
                    self.tools = await self._discover()
                self._ready.set_result(None)
                await self._stop.wait()
        except BaseException:
            if not self._ready.done():
                self._ready.set_exception(ToolRejected("mcp_discovery_failed"))
            self.changed = True
        finally:
            self.client = None

    async def _discover(self) -> tuple[Tool, ...]:
        assert self.client is not None
        tools: dict[str, Tool] = {}
        cursor = None
        cursors = set()
        size = 0
        while True:
            result = await self.client.list_tools(
                params=PaginatedRequestParams(cursor=cursor) if cursor else None
            )
            size += len(
                json_bytes(result.model_dump(mode="json", by_alias=True, exclude_none=True))
            )
            if size > 512 * 1024 or len(tools) + len(result.tools) > 256:
                raise ToolRejected("mcp_catalog_limit")
            for tool in result.tools:
                if tool.name in tools or not tool.name or len(tool.name) > 256:
                    raise ToolRejected("mcp_catalog_invalid")
                tools[tool.name] = tool
            cursor = result.next_cursor
            if not cursor:
                return tuple(tools.values())
            if cursor in cursors or len(cursors) >= 256:
                raise ToolRejected("mcp_catalog_invalid")
            cursors.add(cursor)

    async def call(self, name: str, arguments: dict, timeout: float):
        async with asyncio.timeout(timeout), self._slots:
            if self.changed or self.client is None:
                raise ToolRejected("mcp_catalog_stale")
            task = asyncio.current_task()
            assert task is not None
            self._calls.add(task)
            try:
                return await self.client.call_tool(name, arguments, read_timeout_seconds=timeout)
            finally:
                self._calls.discard(task)

    async def close(self):
        self._stop.set()
        if self._task is not None:
            try:
                async with asyncio.timeout(3):
                    await asyncio.shield(self._task)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
