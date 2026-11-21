"""Discover authorized document sections from the collection pinned to a Run."""

from datetime import date
from typing import Literal

from pydantic import Field

from compass_agent.documents.tool import Arguments, document_tool


class Input(Arguments):
    query: str = Field(min_length=1, max_length=256)
    kind: Literal["knowledge", "history", "external"] | None = None
    entity: str | None = Field(default=None, min_length=1, max_length=240)
    businessDate: date | None = None
    historical: bool = False


SPEC = document_tool("search_documents", "search", Input)
