"""把已保存和校验的版本提交双人评审，需要维护者确认。"""

from uuid import UUID

from pydantic import Field

from compass_agent.semantic.tool import Arguments, tool


class Input(Arguments):
    version_id: UUID
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str = Field(min_length=1, max_length=160)
    request_key: UUID
    binding_id: UUID | None = None


SPEC = tool("submit_semantic_change", "submit", "govern", Input)
