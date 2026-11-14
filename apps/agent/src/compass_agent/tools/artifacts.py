"""Accepted PNG bytes are immutable and independent of sandbox workspaces."""

import hashlib
import struct
import zlib
from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID, uuid5

from compass_agent.execution_context import current_execution
from compass_agent.tools.contracts import ArtifactRef, ToolExecutionContext, ToolRejected


@dataclass(frozen=True)
class StoredArtifact:
    ref: ArtifactRef
    owner: tuple[UUID, UUID, UUID]
    content: bytes = field(repr=False)


def validate_png(content: bytes) -> None:
    if not content or len(content) > 512 * 1024:
        raise ToolRejected("artifact_limit")
    if content[:8] != b"\x89PNG\r\n\x1a\n":
        raise ToolRejected("artifact_invalid")
    offset, chunks, image_data = 8, [], bytearray()
    width = height = color = 0
    while offset + 12 <= len(content):
        size = struct.unpack(">I", content[offset : offset + 4])[0]
        end = offset + size + 12
        if end > len(content) or len(chunks) >= 2048:
            raise ToolRejected("artifact_invalid")
        kind = content[offset + 4 : offset + 8]
        data = content[offset + 8 : end - 4]
        crc = struct.unpack(">I", content[end - 4 : end])[0]
        if zlib.crc32(kind + data) != crc or kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise ToolRejected("artifact_invalid")
        chunks.append(kind)
        if kind == b"IHDR":
            if len(chunks) != 1 or len(data) != 13:
                raise ToolRejected("artifact_invalid")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            if not (0 < width <= 2048 and 0 < height <= 2048 and width * height <= 1_048_576):
                raise ToolRejected("artifact_limit")
            if depth != 8 or color not in {2, 6} or compression or filtering or interlace:
                raise ToolRejected("artifact_invalid")
        if kind == b"IDAT":
            image_data.extend(data)
        if kind == b"IEND" and (size or end != len(content)):
            raise ToolRejected("artifact_invalid")
        offset = end
    if (
        offset != len(content)
        or not chunks
        or chunks[0] != b"IHDR"
        or chunks[-1] != b"IEND"
        or not image_data
    ):
        raise ToolRejected("artifact_invalid")
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(image_data, 8 * 1024 * 1024 + 1)
        if len(decoded) > 8 * 1024 * 1024 or not decoder.eof or decoder.unused_data:
            raise ToolRejected("artifact_invalid")
        stride = 1 + width * (3 if color == 2 else 4)
        if len(decoded) != height * stride or any(
            decoded[row * stride] > 4 for row in range(height)
        ):
            raise ToolRejected("artifact_invalid")
    except zlib.error as error:
        raise ToolRejected("artifact_invalid") from error


class ArtifactStore:
    def __init__(self):
        self._files: dict[str, StoredArtifact] = {}
        self._bytes: dict[UUID, int] = {}
        self._expires: dict[UUID, float] = {}

    def register(self, content: bytes, context: ToolExecutionContext) -> ArtifactRef:
        context.require_running()
        self.expire()
        if context.run_id not in self._bytes and len(self._bytes) >= 64:
            raise ToolRejected("artifact_capacity")
        validate_png(content)
        identifier = str(uuid5(context.run_id, f"{context.step_id}/{context.call_id}/chart.png"))
        digest = hashlib.sha256(content).hexdigest()
        previous = self._files.get(identifier)
        if previous:
            if previous.owner != context.owner or previous.ref.sha256 != digest:
                raise ToolRejected("artifact_conflict")
            return previous.ref
        used = self._bytes.get(context.run_id, 0)
        if used + len(content) > 2 * 1024 * 1024:
            raise ToolRejected("artifact_limit")
        ref = ArtifactRef(
            identifier,
            "chart.png",
            "image/png",
            context.run_id,
            context.step_id,
            context.call_id,
            len(content),
            digest,
        )
        if journal := current_execution.get():
            journal.artifact(ref, content)
        self._files[identifier] = StoredArtifact(ref, context.owner, bytes(content))
        self._bytes[context.run_id] = used + len(content)
        self._expires[context.run_id] = monotonic() + 1800
        return ref

    def read(self, identifier: str, owner: tuple[UUID, UUID, UUID], run_id: UUID) -> StoredArtifact:
        self.expire()
        item = self._files.get(identifier)
        if item is None or item.owner != owner or item.ref.run_id != run_id:
            raise ToolRejected("artifact_not_found")
        return item

    def expire(self) -> None:
        for run_id, deadline in tuple(self._expires.items()):
            if deadline <= monotonic():
                self.close_run(run_id)

    def close_run(self, run_id: UUID) -> None:
        self._files = {
            key: value for key, value in self._files.items() if value.ref.run_id != run_id
        }
        self._bytes.pop(run_id, None)
        self._expires.pop(run_id, None)
