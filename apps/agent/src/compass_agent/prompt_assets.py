"""Read packaged assets or a member from the publication bound to this task."""

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from importlib.resources import files
from pathlib import PurePosixPath

from compass_agent.models.errors import ModelError
from compass_agent.promptops.layout import asset_path, extract

_reader: ContextVar[Callable[[str], str] | None] = ContextVar("compass_prompt_reader", default=None)


@lru_cache(maxsize=512)
def raw_factory_text(path: str) -> str:
    parts = PurePosixPath(path)
    if (
        parts.is_absolute()
        or str(parts) != path
        or ".." in parts.parts
        or parts.suffix not in {".md", ".json"}
    ):
        raise ValueError("invalid prompt asset path")
    body = (
        files("compass_agent")
        .joinpath("prompts", *PurePosixPath(asset_path(path)).parts)
        .read_text(encoding="utf-8")
    )
    return extract(path, body, runtime=True).rstrip("\n")


@lru_cache(maxsize=512)
def factory_text(path: str) -> str:
    body = raw_factory_text(path)
    if path.startswith("tools/") and path.endswith(".json"):
        from compass_agent.promptops.structured import refresh_tool_schema

        body = refresh_tool_schema(path, body)
    return body.rstrip("\n")


def prompt_text(path: str) -> str:
    read = _reader.get()
    if read is None:
        return factory_text(path)
    try:
        return read(path)
    except KeyError as exc:
        raise ModelError("prompt_version_unavailable", 503) from exc


def tool_text(name: str, parameter: str | None = None) -> str:
    path = f"tools/{name}.json"
    read = _reader.get()
    body = read(path) if read else raw_factory_text(path)
    document = json.loads(body)["function"]
    text = (
        document["description"]
        if parameter is None
        else document["parameters"].get("properties", {}).get(parameter, {}).get("description", "")
    )
    return text.rstrip("\n")


@contextmanager
def use_prompts(source: Mapping[str, str] | Callable[[str], str]) -> Iterator[None]:
    read = source if callable(source) else source.__getitem__
    token = _reader.set(read)
    try:
        yield
    finally:
        _reader.reset(token)
