"""Local-only protocol fixture for browser acceptance."""

import asyncio

from mcp.server import MCPServer
from pydantic import BaseModel
from starlette.responses import JSONResponse
from starlette.routing import Route

writes: list[tuple[int, int]] = []

server = MCPServer("Lesson 18 personal tools")


class Sum(BaseModel):
    sum: int


@server.tool()
def add(left: int, right: int) -> Sum:
    return Sum(sum=left + right)


@server.tool()
async def slow(left: int, right: int) -> Sum:
    await asyncio.sleep(60)
    return Sum(sum=left + right)


@server.tool()
def write(left: int, right: int) -> Sum:
    writes.append((left, right))
    return Sum(sum=left + right)


app = server.streamable_http_app()


async def write_count(request):
    return JSONResponse({"count": len(writes)})


app.routes.append(Route("/write-count", write_count))
