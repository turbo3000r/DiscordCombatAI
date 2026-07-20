from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from shared.models import HealthResponse, HealthStatus
from shared.security.ipc_auth import verify_request

from .clock import Clock

MAX_BODY_BYTES = 16 * 1024
REPLAY_TTL_SEC = 10 * 60


class HeadStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    election_state: str
    is_leader: bool
    lease_held: bool
    mosquitto_connected: bool
    pubsub_connected: bool


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    path_with_query: str
    headers: Mapping[str, str]
    body: bytes = b""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    content_type: str = "application/json"


class StatusProvider(Protocol):
    def __call__(self) -> HeadStatus: ...


class RequestAuthenticator:
    def __init__(self, secret: bytes, clock: Clock) -> None:
        if len(secret) < 32:
            raise ValueError("Launcher IPC secret must contain at least 32 bytes")
        self._secret = secret
        self._clock = clock
        self._replays: dict[str, tuple[str, float]] = {}

    def verify(self, request: HttpRequest, *, allow_identical_replay: bool = False) -> bool:
        if len(request.body) > MAX_BODY_BYTES:
            return False
        headers = {key.lower(): value for key, value in request.headers.items()}
        timestamp = headers.get("x-dca-timestamp")
        request_id = headers.get("x-dca-request-id")
        signature = headers.get("x-dca-signature")
        if timestamp is None or request_id is None or signature is None:
            return False
        try:
            normalized_id = str(UUID(request_id))
        except ValueError:
            return False
        now_monotonic = self._clock.monotonic()
        self._replays = {
            key: value for key, value in self._replays.items() if value[1] > now_monotonic
        }
        previous = self._replays.get(normalized_id)
        if previous is not None and (not allow_identical_replay or previous[0] != signature):
            return False
        if not verify_request(
            self._secret,
            request.method,
            request.path_with_query,
            timestamp,
            normalized_id,
            request.body,
            signature,
            now=self._clock.utcnow(),
        ):
            return False
        self._replays[normalized_id] = (signature, now_monotonic + REPLAY_TTL_SEC)
        return True


class HeadHttpApi:
    def __init__(
        self,
        *,
        authenticator: RequestAuthenticator,
        clock: Clock,
        version: str,
        instance_id: str,
        started_at: datetime,
        status_provider: StatusProvider,
    ) -> None:
        self._authenticator = authenticator
        self._clock = clock
        self._version = version
        self._instance_id = instance_id
        self._started_at = started_at
        self._status_provider = status_provider
        self.initialized = False

    def handle(self, request: HttpRequest) -> HttpResponse:
        if request.method.upper() != "GET" or request.body:
            return self._json(405, b'{"error":"method_not_allowed"}')
        if not self._authenticator.verify(request):
            return self._json(401, b'{"error":"unauthorized"}')
        path = request.path_with_query.split("?", 1)[0]
        if path == "/v1/health":
            payload = HealthResponse(
                status=HealthStatus.alive if self.initialized else HealthStatus.initializing,
                version=self._version,
                instance_id=self._instance_id,
                started_at=self._started_at,
            )
            return self._json(200 if self.initialized else 503, payload.model_dump_json().encode())
        if path == "/v1/status":
            return self._json(200, self._status_provider().model_dump_json().encode())
        return self._json(404, b'{"error":"not_found"}')

    @staticmethod
    def _json(status: int, body: bytes) -> HttpResponse:
        return HttpResponse(status=status, body=body)


__all__ = [
    "HeadHttpApi",
    "HeadStatus",
    "HttpRequest",
    "HttpResponse",
    "MAX_BODY_BYTES",
    "RequestAuthenticator",
    "StatusProvider",
]
