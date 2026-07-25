"""Bot MQTT fencing: desired state, grants, soft/hard-stop triggers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import (
    ActivationGrant,
    ActivationGrantMode,
    BotDesiredState,
    BotDesiredStateValue,
    ControlAck,
    UnknownSchemaVersionError,
)

logger = logging.getLogger(__name__)

DESIRED_STATE_TOPIC = MQTT_TOPIC_POLICIES["control_bot_desired_state"].topic
GRANT_TOPIC = MQTT_TOPIC_POLICIES["control_bot_activation_grant"].topic
CONTROL_ACK_TOPIC = MQTT_TOPIC_POLICIES["status_bot_control_ack"].topic
PROGRESS_TOPIC_PREFIX = "progress/ai_worker/"

MessageHandler = Callable[[str, bytes], Awaitable[None]]
DisconnectHandler = Callable[[str], Awaitable[None]]
ConnectHandler = Callable[[], Awaitable[None]]


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def utcnow(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        import time

        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(tz=UTC)


@dataclass
class GrantDeadline:
    leadership_term: str
    command_seq: int
    mode: ActivationGrantMode
    expires_at_monotonic: float
    head_instance_id: str


class BotControlState:
    """Loop-owned fencing state. MQTT callbacks must only hand off into the loop."""

    def __init__(
        self,
        *,
        node_id: str,
        max_ttl_sec: int,
        control_drain_timeout_sec: float,
        clock: Clock | None = None,
        on_activate: Callable[[], Awaitable[None]] | None = None,
        on_soft_stop: Callable[[], Awaitable[None]] | None = None,
        on_hard_stop: Callable[[], Awaitable[None]] | None = None,
        publish_ack: Callable[[ControlAck], Awaitable[None]] | None = None,
    ) -> None:
        self.node_id = node_id
        self.max_ttl_sec = max_ttl_sec
        self.control_drain_timeout_sec = control_drain_timeout_sec
        self.clock = clock or SystemClock()
        self.on_activate = on_activate
        self.on_soft_stop = on_soft_stop
        self.on_hard_stop = on_hard_stop
        self.publish_ack = publish_ack

        self.desired_state = BotDesiredStateValue.inactive
        self.draining = False
        self.gateway_connected = False
        self.mqtt_connected = False
        self.current_grant: GrantDeadline | None = None
        self._term_seq: dict[str, int] = {}
        self._soft_stop_deadline: float | None = None
        self._last_head_instance_id: str | None = None
        self._last_term: str | None = None
        self._last_seq: int = 0

    def admit_dispatch(self) -> bool:
        return (
            self.mqtt_connected
            and self.current_grant is not None
            and self.current_grant.mode is ActivationGrantMode.active
            and not self.draining
            and self.clock.monotonic() < self.current_grant.expires_at_monotonic
        )

    async def on_mqtt_connected(self) -> None:
        self.mqtt_connected = True

    async def on_mqtt_disconnected(self) -> None:
        self.mqtt_connected = False
        await self._enter_soft_stop(reason="mqtt_disconnect")

    async def handle_message(self, topic: str, payload: bytes) -> None:
        if topic == DESIRED_STATE_TOPIC:
            await self._handle_desired_state(payload)
        elif topic == GRANT_TOPIC:
            await self._handle_grant(payload)

    async def tick(self) -> None:
        now = self.clock.monotonic()
        if self.current_grant is not None and now >= self.current_grant.expires_at_monotonic:
            await self._enter_hard_stop(reason="grant_expired")
            return
        if self._soft_stop_deadline is not None and now >= self._soft_stop_deadline:
            await self._enter_hard_stop(reason="control_drain_timeout")

    async def _handle_desired_state(self, payload: bytes) -> None:
        try:
            desired = BotDesiredState.parse_wire_json(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, UnknownSchemaVersionError):
            logger.warning("ignoring malformed desired_state")
            return
        self.desired_state = desired.state
        self._last_head_instance_id = str(desired.head_instance_id)
        self._last_seq = desired.command_seq
        if desired.leadership_term is not None:
            self._last_term = str(desired.leadership_term)
        if desired.state is BotDesiredStateValue.draining:
            await self._enter_soft_stop(reason="desired_draining")
        elif desired.state is BotDesiredStateValue.stopped:
            await self._enter_hard_stop(reason="desired_stopped")
        elif desired.state is BotDesiredStateValue.inactive and self.current_grant is None:
            # Retained inactive cannot activate; clear active authorization.
            self.draining = False

    async def _handle_grant(self, payload: bytes) -> None:
        if not self.mqtt_connected:
            return
        try:
            grant = ActivationGrant.parse_wire_json(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, UnknownSchemaVersionError):
            logger.warning("ignoring malformed activation_grant")
            return
        if grant.node_id != self.node_id:
            return
        if grant.ttl_sec > self.max_ttl_sec or grant.ttl_sec <= 0:
            logger.warning("rejecting grant with invalid ttl_sec=%s", grant.ttl_sec)
            return
        term = str(grant.leadership_term)
        last_seq = self._term_seq.get(term, 0)
        if grant.command_seq <= last_seq:
            return
        self._term_seq[term] = grant.command_seq
        self._last_term = term
        self._last_seq = grant.command_seq
        self._last_head_instance_id = str(grant.head_instance_id)
        self.current_grant = GrantDeadline(
            leadership_term=term,
            command_seq=grant.command_seq,
            mode=grant.mode,
            expires_at_monotonic=self.clock.monotonic() + grant.ttl_sec,
            head_instance_id=str(grant.head_instance_id),
        )
        self._soft_stop_deadline = None
        if grant.mode is ActivationGrantMode.active:
            self.draining = False
            self.desired_state = BotDesiredStateValue.inactive  # desired never carries active
            if self.on_activate is not None:
                await self.on_activate()
            self.gateway_connected = True
            await self._publish_ack(BotDesiredStateValue.inactive)
        else:
            await self._enter_soft_stop(reason="grant_draining")

    async def _enter_soft_stop(self, *, reason: str) -> None:
        self.draining = True
        if self._soft_stop_deadline is None:
            bound = self.control_drain_timeout_sec
            if self.current_grant is not None:
                remaining = self.current_grant.expires_at_monotonic - self.clock.monotonic()
                bound = min(bound, max(remaining, 0.0))
            self._soft_stop_deadline = self.clock.monotonic() + bound
        if self.on_soft_stop is not None:
            await self.on_soft_stop()
        await self._publish_ack(BotDesiredStateValue.draining)

    async def _enter_hard_stop(self, *, reason: str) -> None:
        self.draining = False
        self.current_grant = None
        self._soft_stop_deadline = None
        self.gateway_connected = False
        if self.on_hard_stop is not None:
            await self.on_hard_stop()
        await self._publish_ack(BotDesiredStateValue.stopped)

    async def _publish_ack(self, state: BotDesiredStateValue) -> None:
        if self.publish_ack is None:
            return
        if self._last_head_instance_id is None or self._last_term is None:
            return
        ack = ControlAck(
            node_id=self.node_id,
            head_instance_id=self._last_head_instance_id,
            leadership_term=self._last_term,
            command_seq=self._last_seq,
            state=state,
            gateway_connected=self.gateway_connected,
            observed_at=self.clock.utcnow(),
        )
        await self.publish_ack(ack)


class BotPahoMqttTransport:
    """paho adapter: callbacks only hand bytes/topic/connect/disconnect onto the loop."""

    def __init__(self, *, host: str, port: int, client_id: str) -> None:
        import paho.mqtt.client as mqtt

        self._mqtt_module = mqtt
        self._host = host
        self._port = port
        self._handler: MessageHandler | None = None
        self._disconnect_handler: DisconnectHandler | None = None
        self._connect_handler: ConnectHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected: asyncio.Future[None] | None = None
        self._network_loop_started = False
        self._client = mqtt.Client(  # type: ignore[misc]
            mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=True,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect  # type: ignore[assignment]
        self._client.on_message = self._on_message

    def set_message_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    def set_disconnect_handler(self, handler: DisconnectHandler) -> None:
        self._disconnect_handler = handler

    def set_connect_handler(self, handler: ConnectHandler) -> None:
        self._connect_handler = handler

    def _on_connect(
        self,
        _client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        if self._loop is None or self._connected is None:
            return
        if int(reason_code) == 0:
            initial_connect = not self._connected.done()
            self._loop.call_soon_threadsafe(self._resolve_connected, None)
            if not initial_connect and self._connect_handler is not None:
                handler = self._connect_handler

                async def _run_connect() -> None:
                    await handler()

                asyncio.run_coroutine_threadsafe(_run_connect(), self._loop)
        else:
            self._loop.call_soon_threadsafe(
                self._resolve_connected,
                ConnectionError(f"MQTT CONNACK rejected: {reason_code}"),
            )

    def _resolve_connected(self, error: BaseException | None) -> None:
        if self._connected is None or self._connected.done():
            return
        if error is None:
            self._connected.set_result(None)
        else:
            self._connected.set_exception(error)

    def _on_disconnect(
        self,
        _client: Any,
        _userdata: Any,
        _disconnect_flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        if self._loop is not None and self._disconnect_handler is not None:
            asyncio.run_coroutine_threadsafe(
                self._dispatch_disconnect(str(reason_code)),
                self._loop,
            )

    async def _dispatch_disconnect(self, reason: str) -> None:
        if self._disconnect_handler is not None:
            await self._disconnect_handler(reason)

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        if self._loop is not None and self._handler is not None:
            asyncio.run_coroutine_threadsafe(
                self._dispatch_message(str(message.topic), bytes(message.payload)),
                self._loop,
            )

    async def _dispatch_message(self, topic: str, payload: bytes) -> None:
        if self._handler is not None:
            await self._handler(topic, payload)

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._connected = self._loop.create_future()
        if self._network_loop_started:
            await asyncio.to_thread(self._client.reconnect)
        else:
            await asyncio.to_thread(self._client.connect, self._host, self._port, 60)
            self._client.loop_start()
            self._network_loop_started = True
        await asyncio.wait_for(self._connected, timeout=10)

    async def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        qos: int,
        retain: bool,
    ) -> None:
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        if info.rc != self._mqtt_module.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT publish rejected with code {info.rc}")
        await asyncio.to_thread(info.wait_for_publish, 5)
        if not info.is_published():
            raise TimeoutError("MQTT publish was not confirmed")

    async def subscribe(self, topic: str, *, qos: int) -> None:
        result, _mid = self._client.subscribe(topic, qos=qos)
        if result != self._mqtt_module.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT subscribe rejected with code {result}")

    async def close(self) -> None:
        if self._network_loop_started:
            await asyncio.to_thread(self._client.disconnect)
            self._client.loop_stop()
            self._network_loop_started = False


__all__ = [
    "BotControlState",
    "BotPahoMqttTransport",
    "CONTROL_ACK_TOPIC",
    "Clock",
    "DESIRED_STATE_TOPIC",
    "GRANT_TOPIC",
    "PROGRESS_TOPIC_PREFIX",
    "SystemClock",
]
