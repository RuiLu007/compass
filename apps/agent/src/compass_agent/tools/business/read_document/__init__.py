"""Read a previously discovered paragraph or an adjacent paragraph."""

from uuid import UUID

from pydantic import Field

from compass_agent.documents.tool import Arguments, document_tool


class Input(Arguments):
    ref: UUID
    offset: int = Field(default=0, ge=-1, le=1)


SPEC = document_tool("read_document", "read", Input)
