"""Synchronous Mosquitto client for AI Worker progress publishes."""

from __future__ import annotations

import logging
import threading
from contextlib import suppress
from typing import Any

from shared.messaging.mqtt_connack import mqtt_connect_accepted

logger = logging.getLogger(__name__)

_CONNECT_WAIT_SEC = 5.0
_PUBLISH_WAIT_SEC = 2.0


class SyncPahoMqttTransport:
    """Per-task paho client. Created inside the worker child, never inherited across fork."""

    def __init__(self, *, host: str, port: int, client_id: str) -> None:
        import paho.mqtt.client as mqtt

        self._host = host
        self._port = port
        self._connected = threading.Event()
        self._connect_error: BaseException | None = None
        self._client = mqtt.Client(  # type: ignore[misc]
            mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=True,
        )
        self._client.on_connect = self._on_connect

    def _on_connect(
        self,
        _client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        if mqtt_connect_accepted(reason_code):
            self._connect_error = None
            self._connected.set()
            return
        self._connect_error = ConnectionError(f"MQTT CONNACK rejected: {reason_code}")
        self._connected.set()

    def connect(self) -> None:
        self._connected.clear()
        self._connect_error = None
        self._client.connect(self._host, self._port, keepalive=60)
        self._client.loop_start()
        if not self._connected.wait(_CONNECT_WAIT_SEC):
            self.close()
            raise TimeoutError("MQTT connect timed out")
        if self._connect_error is not None:
            error = self._connect_error
            self.close()
            raise error

    def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=_PUBLISH_WAIT_SEC)

    def close(self) -> None:
        with suppress(Exception):
            self._client.loop_stop()
        with suppress(Exception):
            self._client.disconnect()


__all__ = ["SyncPahoMqttTransport"]
