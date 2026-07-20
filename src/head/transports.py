from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextlib import suppress
from typing import Any

import aiohttp
import psutil

from shared.azure.clients.pubsub import PubSubClient

from .http import HeadHttpApi, HttpRequest
from .launcher import ClientResponse, HttpClientTransport
from .release import GitHubRelease, ReleaseSource
from .telemetry import HardwareSnapshot, ProcessSampler

MessageHandler = Callable[[str, bytes], Awaitable[bool]]
DisconnectHandler = Callable[[str], Awaitable[None]]
ConnectHandler = Callable[[], Coroutine[Any, Any, None]]
ConnectionStateHandler = Callable[[bool], Awaitable[None]]


class AioHttpClientTransport(HttpClientTransport):
    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        connect_timeout_sec: float,
        response_timeout_sec: float,
    ) -> ClientResponse:
        timeout = aiohttp.ClientTimeout(
            total=connect_timeout_sec + response_timeout_sec,
            sock_connect=connect_timeout_sec,
            sock_read=response_timeout_sec,
        )
        try:
            async with self._client().request(
                method,
                url,
                headers=headers,
                data=body or None,
                timeout=timeout,
                allow_redirects=False,
            ) as response:
                return ClientResponse(status=response.status, body=await response.read())
        except TimeoutError as exc:
            raise TimeoutError("HTTP request timed out") from exc
        except aiohttp.ClientConnectionError as exc:
            raise ConnectionError("HTTP connection failed") from exc

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None


class AioHttpHeadServer:
    def __init__(self, api: HeadHttpApi, *, bind: str, port: int) -> None:
        self._api = api
        self._bind = bind
        self._port = port
        self._runner: Any | None = None

    async def start(self) -> None:
        from aiohttp import web

        app = web.Application(client_max_size=16 * 1024)

        async def dispatch(request: Any) -> Any:
            try:
                body = await request.read()
            except web.HTTPRequestEntityTooLarge:
                return web.Response(status=413, body=b'{"error":"body_too_large"}')
            response = self._api.handle(
                HttpRequest(
                    method=request.method,
                    path_with_query=request.rel_url.raw_path_qs,
                    headers=dict(request.headers),
                    body=body,
                )
            )
            return web.Response(
                status=response.status,
                body=response.body,
                content_type=response.content_type,
            )

        app.router.add_route("*", "/{path:.*}", dispatch)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._bind, self._port).start()

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


class PahoMqttTransport:
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
                asyncio.run_coroutine_threadsafe(
                    self._connect_handler(),
                    self._loop,
                )
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


class AzureClusterPubSubTransport:
    def __init__(
        self,
        *,
        token_client: PubSubClient,
        group: str,
        user_id: str,
    ) -> None:
        self._token_client = token_client
        self._group = group
        self._user_id = user_id
        self._client: Any | None = None
        self._messages: asyncio.Queue[bytes | BaseException] = asyncio.Queue()
        self._state_handler: ConnectionStateHandler | None = None

    def set_state_handler(self, handler: ConnectionStateHandler) -> None:
        self._state_handler = handler

    async def connect(self) -> None:
        from azure.messaging.webpubsubclient.aio import WebPubSubClient
        from azure.messaging.webpubsubclient.models import CallbackType

        token = await self._token_client.get_client_access_token(
            group=self._group,
            user_id=self._user_id,
            allow_send=True,
        )
        self._client = WebPubSubClient(
            token.url,
            message_retry_total=0,
            auto_rejoin_groups=True,
            logging_enable=False,
        )

        async def on_group_message(event: Any) -> None:
            data = getattr(event, "data", b"")
            if isinstance(data, bytes):
                payload = data
            elif isinstance(data, str):
                payload = data.encode("utf-8")
            else:
                payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
            self._messages.put_nowait(payload)

        async def on_connected(_event: Any) -> None:
            if self._state_handler is not None:
                await self._state_handler(True)

        async def on_disconnected(event: Any) -> None:
            if self._state_handler is not None:
                await self._state_handler(False)
            reason = str(getattr(event, "message", "Web PubSub disconnected"))
            self._messages.put_nowait(ConnectionError(reason))

        await self._client.subscribe(CallbackType.GROUP_MESSAGE, on_group_message)
        await self._client.subscribe(CallbackType.CONNECTED, on_connected)
        await self._client.subscribe(CallbackType.DISCONNECTED, on_disconnected)
        await self._client.open()

    async def join_group(self, group: str) -> None:
        if self._client is None:
            raise RuntimeError("Web PubSub is disconnected")
        await self._client.join_group(group)

    async def send_group(self, group: str, payload: bytes) -> None:
        from azure.messaging.webpubsubclient.models import WebPubSubDataType

        if self._client is None:
            raise RuntimeError("Web PubSub is disconnected")
        await self._client.send_to_group(
            group,
            payload.decode("utf-8"),
            WebPubSubDataType.TEXT,
        )

    async def receive(self) -> bytes:
        result = await self._messages.get()
        if isinstance(result, BaseException):
            raise result
        return result

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


class GitHubApiReleaseSource(ReleaseSource):
    def __init__(self, *, token: str | None = None) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._session = aiohttp.ClientSession(headers=headers)

    async def list_releases(self, repository: str) -> list[GitHubRelease]:
        url = f"https://api.github.com/repos/{repository}/releases"
        releases: list[GitHubRelease] = []
        for page in range(1, 11):
            async with self._session.get(
                url,
                params={"per_page": "100", "page": str(page)},
                timeout=aiohttp.ClientTimeout(total=15, sock_connect=5),
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                payload = await response.json()
            releases.extend(GitHubRelease.model_validate(item) for item in payload)
            if len(payload) < 100:
                break
        return releases

    async def close(self) -> None:
        await self._session.close()


class PsutilProcessSampler(ProcessSampler):
    def __init__(self) -> None:
        self._process = psutil.Process()
        self._process.cpu_percent(None)

    def sample(self) -> HardwareSnapshot:
        memory = self._process.memory_info()
        return HardwareSnapshot(
            cpu_percent=float(self._process.cpu_percent(None)),
            memory_mb=float(memory.rss / (1024 * 1024)),
            memory_percent=float(self._process.memory_percent()),
        )


class AioPikaEventExchangeTransport:
    """Consumes rabbitmq_event_exchange metadata only (never message payloads)."""

    EVENT_EXCHANGE = "amq.rabbitmq.event"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        vhost: str,
        binding_keys: tuple[str, ...] = ("#",),
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._vhost = vhost
        self._binding_keys = binding_keys
        self._connection: Any | None = None
        self._channel: Any | None = None
        self._queue: Any | None = None
        self._iterator: Any | None = None

    async def connect(self) -> None:
        import aio_pika

        await self.close()
        self._connection = await aio_pika.connect_robust(
            host=self._host,
            port=self._port,
            login=self._username,
            password=self._password,
            virtualhost=self._vhost,
            timeout=5,
        )
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=10)
        self._queue = await self._channel.declare_queue(exclusive=True, auto_delete=True)
        exchange = await self._channel.get_exchange(self.EVENT_EXCHANGE, ensure=False)
        for key in self._binding_keys:
            await self._queue.bind(exchange, routing_key=key)
        self._iterator = self._queue.iterator().__aiter__()

    async def consume_once(self) -> tuple[str, Mapping[str, object], Any] | None:
        from datetime import UTC, datetime

        if self._iterator is None:
            raise ConnectionError("event exchange transport is not connected")
        message = await self._iterator.__anext__()
        async with message.process(ignore_processed=True):
            headers: dict[str, object] = {}
            raw_headers = message.headers or {}
            for key, value in raw_headers.items():
                if isinstance(key, str):
                    headers[key] = value
            occurred = message.timestamp
            if occurred is None:
                occurred_at = datetime.now(tz=UTC)
            elif occurred.tzinfo is None:
                occurred_at = occurred.replace(tzinfo=UTC)
            else:
                occurred_at = occurred.astimezone(UTC)
            # Intentionally ignore message.body — broker event payloads must not be logged.
            return str(message.routing_key or ""), headers, occurred_at

    async def close(self) -> None:
        iterator = self._iterator
        self._iterator = None
        if iterator is not None:
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                with suppress(Exception):
                    await aclose()
        queue = self._queue
        self._queue = None
        if queue is not None:
            with suppress(Exception):
                await queue.delete(if_unused=False, if_empty=False)
        channel = self._channel
        self._channel = None
        if channel is not None:
            with suppress(Exception):
                await channel.close()
        connection = self._connection
        self._connection = None
        if connection is not None:
            with suppress(Exception):
                await connection.close()


__all__ = [
    "AioHttpClientTransport",
    "AioHttpHeadServer",
    "AioPikaEventExchangeTransport",
    "AzureClusterPubSubTransport",
    "GitHubApiReleaseSource",
    "PahoMqttTransport",
    "PsutilProcessSampler",
]
