from __future__ import annotations

import asyncio
import json
from typing import Protocol, TypeAlias

from pydantic import ValidationError

from shared.azure.clients.pubsub import PubSubClient
from shared.models import LeaderHeartbeat, TelemetryLivePayload, UpdateAvailableMessage

ClusterMessage: TypeAlias = LeaderHeartbeat | UpdateAvailableMessage


class ClusterPubSubTransport(Protocol):
    """Long-lived client-protocol connection, separate from the service SDK."""

    async def connect(self) -> None: ...

    async def join_group(self, group: str) -> None: ...

    async def send_group(self, group: str, payload: bytes) -> None: ...

    async def receive(self) -> bytes: ...

    async def close(self) -> None: ...


class DashboardTelemetrySender(Protocol):
    """Narrow service-SDK boundary used only for dashboard fan-out."""

    async def send(self, group: str, payload: TelemetryLivePayload) -> None: ...


class ServiceSdkTelemetrySender:
    def __init__(self, client: PubSubClient) -> None:
        self._client = client

    async def send(self, group: str, payload: TelemetryLivePayload) -> None:
        await self._client.send_to_group(
            group, payload.model_dump_json(), content_type="application/json"
        )


class ClusterPubSub:
    def __init__(self, transport: ClusterPubSubTransport, *, group: str) -> None:
        self._transport = transport
        self.group = group
        self.connected = False
        self.events: asyncio.Queue[ClusterMessage] = asyncio.Queue()

    async def start(self) -> None:
        set_state_handler = getattr(self._transport, "set_state_handler", None)
        if set_state_handler is not None:
            set_state_handler(self._handle_connection_state)
        await self._transport.connect()
        await self._transport.join_group(self.group)
        self.connected = True

    async def _handle_connection_state(self, connected: bool) -> None:
        self.connected = connected

    async def send(self, message: ClusterMessage) -> None:
        if not self.connected:
            raise RuntimeError("cluster PubSub is disconnected")
        await self._transport.send_group(
            self.group,
            message.model_dump_json().encode("utf-8"),
        )

    async def receive_once(self) -> bool:
        raw = await self._transport.receive()
        try:
            discriminator = json.loads(raw).get("type")
            if discriminator == "leader_heartbeat":
                event: ClusterMessage = LeaderHeartbeat.parse_wire_json(raw)
            elif discriminator == "update_available":
                event = UpdateAvailableMessage.parse_wire_json(raw)
            else:
                return False
        except (AttributeError, json.JSONDecodeError, ValidationError, ValueError):
            return False
        await self.events.put(event)
        return True

    async def mark_disconnected(self) -> None:
        self.connected = False

    async def close(self) -> None:
        self.connected = False
        await self._transport.close()


__all__ = [
    "ClusterMessage",
    "ClusterPubSub",
    "ClusterPubSubTransport",
    "DashboardTelemetrySender",
    "ServiceSdkTelemetrySender",
]
