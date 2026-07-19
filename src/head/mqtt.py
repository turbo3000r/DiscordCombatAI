from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from pydantic import ValidationError

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES, TopicPolicy
from shared.models import (
    ActivationGrant,
    AiWorkerDesiredState,
    AiWorkerHeartbeat,
    BotDesiredState,
    BotHeartbeat,
    ControlAck,
    DrainProgress,
    PauseAck,
)


class MqttTransport(Protocol):
    async def connect(self) -> None: ...

    async def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None: ...

    async def subscribe(self, topic: str, *, qos: int) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MqttDisconnected:
    reason: str


HeadMqttEvent: TypeAlias = (
    ControlAck
    | DrainProgress
    | PauseAck
    | BotHeartbeat
    | AiWorkerHeartbeat
    | MqttDisconnected
)


class MqttControlError(RuntimeError):
    pass


def _payload(model: BotDesiredState | ActivationGrant | AiWorkerDesiredState) -> bytes:
    return model.model_dump_json().encode("utf-8")


class MqttManager:
    def __init__(
        self,
        transport: MqttTransport,
        *,
        unhandled_handler: Callable[[str, bytes], Awaitable[bool]] | None = None,
    ) -> None:
        self._transport = transport
        self._unhandled_handler = unhandled_handler
        self.events: asyncio.Queue[HeadMqttEvent] = asyncio.Queue()
        self.connected = False
        self.safe_state_confirmed = False
        self._authoritative = False
        self._bot_desired: BotDesiredState | None = None
        self._worker_desired: AiWorkerDesiredState | None = None
        self._listeners: list[Callable[[HeadMqttEvent], Awaitable[None]]] = []

    def add_listener(self, listener: Callable[[HeadMqttEvent], Awaitable[None]]) -> None:
        self._listeners.append(listener)

    async def start(self, startup_state: BotDesiredState) -> None:
        if startup_state.leadership_term is not None or startup_state.command_seq != 0:
            raise ValueError("startup desired state must not carry lease authority")
        set_message_handler = getattr(self._transport, "set_message_handler", None)
        if set_message_handler is not None:
            set_message_handler(self.ingest)
        set_disconnect_handler = getattr(self._transport, "set_disconnect_handler", None)
        if set_disconnect_handler is not None:
            set_disconnect_handler(self.mark_disconnected)
        set_connect_handler = getattr(self._transport, "set_connect_handler", None)
        if set_connect_handler is not None:
            set_connect_handler(self._restore_after_transport_reconnect)
        await self._transport.connect()
        self.connected = True
        try:
            await self.publish_desired_state(startup_state)
            await self._subscribe_status()
        except Exception:
            self.connected = False
            self.safe_state_confirmed = False
            raise
        self.safe_state_confirmed = True

    async def _restore_after_transport_reconnect(self) -> None:
        self.connected = True
        self.safe_state_confirmed = False
        try:
            if self._bot_desired is None:
                raise MqttControlError("no retained safe state is available")
            await self._publish(
                MQTT_TOPIC_POLICIES["control_bot_desired_state"], self._bot_desired
            )
            if self._worker_desired is not None:
                await self._publish(
                    MQTT_TOPIC_POLICIES["control_ai_worker_desired_state"],
                    self._worker_desired,
                )
            await self._subscribe_status()
        except Exception:
            self.connected = False
            raise
        self.safe_state_confirmed = True

    async def reconnect(self) -> None:
        self.connected = False
        self.safe_state_confirmed = False
        await self._transport.connect()
        self.connected = True
        try:
            if self._bot_desired is None:
                raise MqttControlError("no retained safe state is available")
            await self._publish(MQTT_TOPIC_POLICIES["control_bot_desired_state"], self._bot_desired)
            if self._worker_desired is not None:
                await self._publish(
                    MQTT_TOPIC_POLICIES["control_ai_worker_desired_state"],
                    self._worker_desired,
                )
            await self._subscribe_status()
        except Exception:
            self.connected = False
            raise
        self.safe_state_confirmed = True

    async def _subscribe_status(self) -> None:
        for name in (
            "status_bot_control_ack",
            "status_bot_drain_progress",
            "status_ai_worker_pause_ack",
            "status_bot_heartbeat",
            "status_ai_worker_heartbeat",
            "logs_all",
        ):
            policy = MQTT_TOPIC_POLICIES[name]
            await self._transport.subscribe(policy.topic, qos=policy.qos)

    def set_authoritative(self, value: bool) -> None:
        self._authoritative = value

    async def publish_desired_state(self, state: BotDesiredState) -> None:
        await self._publish(MQTT_TOPIC_POLICIES["control_bot_desired_state"], state)
        self._bot_desired = state
        self.safe_state_confirmed = True

    async def publish_worker_state(self, state: AiWorkerDesiredState) -> None:
        await self._publish(MQTT_TOPIC_POLICIES["control_ai_worker_desired_state"], state)
        self._worker_desired = state

    async def publish_grant(self, grant: ActivationGrant) -> None:
        if not (self.connected and self.safe_state_confirmed and self._authoritative):
            raise MqttControlError("activation grant denied without live control and authority")
        await self._publish(MQTT_TOPIC_POLICIES["control_bot_activation_grant"], grant)

    async def _publish(
        self,
        policy: TopicPolicy,
        model: BotDesiredState | ActivationGrant | AiWorkerDesiredState,
    ) -> None:
        if not self.connected:
            raise MqttControlError("Mosquitto is disconnected")
        await self._transport.publish(
            policy.topic,
            _payload(model),
            qos=policy.qos,
            retain=policy.retain,
        )

    async def ingest(self, topic: str, payload: bytes) -> bool:
        try:
            if topic == MQTT_TOPIC_POLICIES["status_bot_control_ack"].topic:
                event: HeadMqttEvent = ControlAck.parse_wire_json(payload)
            elif topic == MQTT_TOPIC_POLICIES["status_bot_drain_progress"].topic:
                event = DrainProgress.parse_wire_json(payload)
            elif topic == MQTT_TOPIC_POLICIES["status_ai_worker_pause_ack"].topic:
                event = PauseAck.parse_wire_json(payload)
            elif topic == MQTT_TOPIC_POLICIES["status_bot_heartbeat"].topic:
                event = BotHeartbeat.parse_wire_json(payload)
            elif topic == MQTT_TOPIC_POLICIES["status_ai_worker_heartbeat"].topic:
                event = AiWorkerHeartbeat.parse_wire_json(payload)
            else:
                if self._unhandled_handler is not None:
                    return await self._unhandled_handler(topic, payload)
                return False
        except (ValidationError, ValueError):
            return False
        await self.events.put(event)
        for listener in self._listeners:
            await listener(event)
        return True

    async def mark_disconnected(self, reason: str) -> None:
        self.connected = False
        self.safe_state_confirmed = False
        self._authoritative = False
        await self.events.put(MqttDisconnected(reason))

    async def close(self) -> None:
        self.connected = False
        self.safe_state_confirmed = False
        self._authoritative = False
        await self._transport.close()


__all__ = [
    "HeadMqttEvent",
    "MqttControlError",
    "MqttDisconnected",
    "MqttManager",
    "MqttTransport",
]
