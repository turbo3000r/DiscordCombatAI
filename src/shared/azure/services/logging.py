from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from shared.azure._helpers import utc_now_iso
from shared.azure.clients.blob import BlobClient
from shared.security.redact import redact_sensitive


@dataclass
class _LogBuffer:
    lines: deque[str] = field(default_factory=deque)
    byte_size: int = 0

    def append(self, line: str) -> None:
        encoded_size = len(line.encode("utf-8")) + 1
        self.lines.append(line)
        self.byte_size += encoded_size
        while len(self.lines) > 10_000 or self.byte_size > 8 * 1024 * 1024:
            removed = self.lines.popleft()
            self.byte_size -= len(removed.encode("utf-8")) + 1


class LogArchiveService:
    def __init__(self, *, blob_client: BlobClient, container_name: str) -> None:
        self.blob_client = blob_client
        self.container_name = container_name
        self.buffer = _LogBuffer()

    def buffer_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.buffer.append(redact_sensitive(line))

    def blob_path(self, *, node_id: str, timestamp_iso: str | None = None) -> str:
        timestamp = timestamp_iso or utc_now_iso()
        year, month, day = timestamp[:4], timestamp[5:7], timestamp[8:10]
        return f"logs/{node_id}/{year}/{month}/{day}.log"

    async def flush(self, *, node_id: str, timestamp_iso: str | None = None) -> str:
        path = self.blob_path(node_id=node_id, timestamp_iso=timestamp_iso)
        payload = "\n".join(self.buffer.lines)
        if payload:
            payload += "\n"
        await self.blob_client.append_text(self.container_name, path, payload)
        self.buffer.lines.clear()
        self.buffer.byte_size = 0
        return path
