from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from shared.models import (
    LauncherStatusResponse,
    UpdateAcceptedResponse,
    UpdateAlreadyCurrentResponse,
    UpdateReason,
    UpdateRequest,
    parse_launcher_update_response,
)
from shared.security.ipc_auth import MAX_CLOCK_SKEW_SEC, sign_request

from .clock import Clock


@dataclass(frozen=True, slots=True)
class ClientResponse:
    status: int
    body: bytes


class HttpClientTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        connect_timeout_sec: float,
        response_timeout_sec: float,
    ) -> ClientResponse: ...

    async def close(self) -> None: ...


class LauncherRequestError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


LauncherResult = UpdateAcceptedResponse | UpdateAlreadyCurrentResponse


class LauncherClient:
    def __init__(
        self,
        *,
        base_url: str,
        secret: bytes,
        source_node_id: str,
        transport: HttpClientTransport,
        clock: Clock,
        uuid_factory: Callable[[], UUID] = uuid4,
        jitter: Callable[[int], float] | None = None,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("Launcher IPC secret must contain at least 32 bytes")
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._source_node_id = source_node_id
        self._transport = transport
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._jitter = jitter or (lambda attempt: min(0.25 * 2 ** (attempt - 1), 1.0))

    async def request_update(
        self,
        target_version: str,
        *,
        reason: UpdateReason = UpdateReason.auto_detected,
    ) -> LauncherResult:
        request_id = self._uuid_factory()
        request = UpdateRequest(
            request_id=str(request_id),
            target_version=target_version,
            reason=reason,
            requested_at=self._clock.utcnow(),
            source_node_id=self._source_node_id,
        )
        body = request.model_dump_json().encode("utf-8")
        path = "/v1/update"
        signed_at: datetime | None = None
        headers: Mapping[str, str] | None = None
        last_error: BaseException | None = None

        for attempt in range(1, 4):
            now = self._clock.utcnow()
            if (
                signed_at is None
                or headers is None
                or abs((now - signed_at).total_seconds()) >= MAX_CLOCK_SKEW_SEC
            ):
                signed_at = now
                timestamp = int(now.timestamp())
                signature = sign_request(
                    self._secret,
                    "POST",
                    path,
                    timestamp,
                    request_id,
                    body,
                )
                headers = {
                    "Content-Type": "application/json",
                    "X-DCA-Timestamp": str(timestamp),
                    "X-DCA-Request-ID": str(request_id),
                    "X-DCA-Signature": signature,
                }
            try:
                response = await self._transport.request(
                    "POST",
                    f"{self._base_url}{path}",
                    headers=headers,
                    body=body,
                    connect_timeout_sec=2,
                    response_timeout_sec=5,
                )
            except (ConnectionError, TimeoutError) as exc:
                last_error = exc
            else:
                if response.status in {200, 202}:
                    parsed = parse_launcher_update_response(json.loads(response.body))
                    if isinstance(parsed, (UpdateAcceptedResponse, UpdateAlreadyCurrentResponse)):
                        return parsed
                    raise LauncherRequestError("unexpected successful Launcher payload")
                if response.status not in {500, 503}:
                    raise LauncherRequestError(
                        f"Launcher rejected update with HTTP {response.status}",
                        status=response.status,
                    )
                last_error = LauncherRequestError(
                    f"transient Launcher HTTP {response.status}",
                    status=response.status,
                )
            if attempt < 3:
                await self._clock.sleep(self._jitter(attempt))
        raise LauncherRequestError("Launcher update outcome remains unknown") from last_error

    async def get_status(self) -> LauncherStatusResponse:
        path = "/v1/status"
        request_id = self._uuid_factory()
        timestamp = int(self._clock.utcnow().timestamp())
        headers = {
            "X-DCA-Timestamp": str(timestamp),
            "X-DCA-Request-ID": str(request_id),
            "X-DCA-Signature": sign_request(
                self._secret, "GET", path, timestamp, request_id, b""
            ),
        }
        response = await self._transport.request(
            "GET",
            f"{self._base_url}{path}",
            headers=headers,
            body=b"",
            connect_timeout_sec=2,
            response_timeout_sec=5,
        )
        if response.status != 200:
            raise LauncherRequestError(
                f"Launcher status failed with HTTP {response.status}", status=response.status
            )
        return LauncherStatusResponse.parse_wire_json(response.body)

    async def close(self) -> None:
        await self._transport.close()


__all__ = [
    "ClientResponse",
    "HttpClientTransport",
    "LauncherClient",
    "LauncherRequestError",
    "LauncherResult",
]
