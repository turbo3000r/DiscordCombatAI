"""Bot application — asyncio owns Discord, fencing, tracker, and domain state."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol

from bot.localization.handler import LocalizationHandler
from bot.modules.commands.suggestions.service import (
    NotificationDeliveryService,
    NotificationSweepService,
    SuggestionQueuePoller,
)
from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import (
    BattleAiTaskEnvelope,
    ControlAck,
    EnvironmentAiTaskEnvelope,
    UnknownSchemaVersionError,
    parse_task_progress_message,
)
from shared.runtime.settings import RuntimeMode

from .modules.client import CombatBot, create_bot
from .modules.events.control_events import (
    CONTROL_ACK_TOPIC,
    DESIRED_STATE_TOPIC,
    GRANT_TOPIC,
    PROGRESS_TOPIC_PREFIX,
    BotControlState,
    BotPahoMqttTransport,
)
from .modules.events.guild_events import GuildEventHandler
from .modules.services.ai_transport import AiTransport, CompletionCallback, PendingDispatch
from .modules.services.guild_sync import GuildSyncService
from .modules.services.heartbeat import BotHeartbeatService
from .modules.services.lifecycle import LifecycleController
from .modules.services.task_tracker import TaskTracker
from .settings import BotSettings

logger = logging.getLogger(__name__)

PROGRESS_SUBSCRIBE = MQTT_TOPIC_POLICIES["progress_ai_worker"].topic
CONTROL_ACK_POLICY = MQTT_TOPIC_POLICIES["status_bot_control_ack"]


class GuildService(Protocol):
    async def ensure_active_guild(
        self,
        *,
        guild_id: str,
        name: str,
        icon_url: str | None,
        member_count: int,
        owner_id: str,
    ) -> Any: ...

    async def patch_metadata(self, guild_id: str, patch: dict[str, Any]) -> Any: ...

    async def mark_left(self, guild_id: str) -> Any: ...


class StatusServiceProto(Protocol):
    async def update_status(self, status: dict[str, Any]) -> Any: ...

    async def get_suggestion_catalog(self) -> Any: ...

    async def ensure_seeded(self) -> Any: ...


class SuggestionServiceProto(Protocol):
    async def create(self, payload: dict[str, Any]) -> Any: ...

    async def get(self, guild_id: str, suggestion_id: str) -> Any: ...


class DependencyHealth:
    def __init__(self) -> None:
        self.cosmos_ok = False
        self.rabbitmq_connected = False
        self.status_blob_ok = False
        # Queue health is false until a successful production receive.
        self.azure_queue_ok = False

    def set_cosmos_ok(self, ok: bool) -> None:
        self.cosmos_ok = ok

    def set_azure_queue_ok(self, ok: bool) -> None:
        self.azure_queue_ok = ok


class _GatewayView:
    def __init__(self, app: BotApplication) -> None:
        self._app = app

    @property
    def gateway_connected(self) -> bool:
        return self._app.gateway_connected

    def guild_count(self) -> int:
        client = self._app.client
        if client is None:
            return 0
        return len(getattr(client, "guilds", []) or [])

    def latency_ms(self) -> int | None:
        client = self._app.client
        if client is None:
            return None
        latency = getattr(client, "latency", None)
        if latency is None:
            return None
        try:
            return max(0, int(float(latency) * 1000))
        except (TypeError, ValueError):
            return None


class BotApplication:
    """Inactive by default. Gateway starts only after an authorized activation."""

    def __init__(
        self,
        settings: BotSettings,
        *,
        guild_service: GuildService | None = None,
        status_service: StatusServiceProto | None = None,
        suggestion_service: SuggestionServiceProto | None = None,
        l10n: LocalizationHandler | None = None,
        bot_factory: Callable[[], CombatBot] | None = None,
        token: str | None = None,
        mqtt_transport: BotPahoMqttTransport | None = None,
        ai_transport: AiTransport | None = None,
        enable_mqtt: bool = False,
        enable_transport: bool = False,
        runtime_mode: RuntimeMode | str = RuntimeMode.production,
        development_guild_id: str | None = None,
        expected_application_id: str | None = None,
        enable_suggestion_queue: bool = True,
        suggestion_queue_client: Any | None = None,
        suggestion_queue_name: str | None = None,
    ) -> None:
        self.settings = settings
        self._guild_service = guild_service
        self._status_service = status_service
        self._suggestion_service = suggestion_service
        self.l10n = l10n
        self._bot_factory = bot_factory or create_bot
        self._token = token or settings.discord_bot_token.get_secret_value()
        self.runtime_mode = RuntimeMode(runtime_mode)
        self.development_guild_id = development_guild_id
        self.expected_application_id = expected_application_id
        # Development suppresses Azure Queue / DM poller and sweep.
        self.enable_suggestion_queue = (
            enable_suggestion_queue and self.runtime_mode is RuntimeMode.production
        )
        self._suggestion_queue_client = suggestion_queue_client
        self._suggestion_queue_name = suggestion_queue_name
        self.health = DependencyHealth()
        self._client: CombatBot | None = None
        self._client_task: asyncio.Task[None] | None = None
        self._guild_handler: GuildEventHandler | None = None
        self._guild_sync: GuildSyncService | None = None
        self._gateway_authorized = False
        self._started = False
        self._enable_mqtt = enable_mqtt
        self._enable_transport = enable_transport
        self._tick_task: asyncio.Task[None] | None = None
        self.accepting_suggestion_claims = False
        self.suggestion_delivery: NotificationDeliveryService | None = None
        self.suggestion_queue_poller: SuggestionQueuePoller | None = None
        self.suggestion_sweep: NotificationSweepService | None = None

        self.tracker = TaskTracker(
            stall_timeout_sec=settings.ai_task_stall_timeout_sec,
            overall_timeout_sec=settings.ai_task_timeout_sec,
        )
        self._ai_lock = asyncio.Lock()
        self._ai_holder: str | None = None
        self.quick_battle: Any = None
        self.lifecycle = LifecycleController(
            node_id=settings.node_id,
            drain_progress_interval_sec=settings.drain_progress_interval_sec,
            shutdown_grace_sec=settings.shutdown_grace_sec,
            revoke_gateway=self.revoke_gateway,
            authorize_gateway=self.authorize_gateway,
            tracker=self.tracker,
        )
        self.control = BotControlState(
            node_id=settings.node_id,
            max_ttl_sec=settings.activation_grant_max_ttl_sec,
            control_drain_timeout_sec=settings.control_drain_timeout_sec,
            on_activate=self.lifecycle.on_activate,
            on_soft_stop=self.lifecycle.on_soft_stop,
            on_hard_stop=self.lifecycle.on_hard_stop,
            publish_ack=self._publish_control_ack,
        )
        self._mqtt = mqtt_transport
        if self._mqtt is None and enable_mqtt:
            self._mqtt = BotPahoMqttTransport(
                host=settings.mosquitto_host,
                port=settings.mosquitto_port,
                client_id=f"bot-{settings.node_id}",
            )
        self.transport = ai_transport
        if self.transport is None and enable_transport:
            self.transport = AiTransport(
                broker_url=settings.broker_url(),
                admit_dispatch=self.admit_dispatch,
                on_confirmed_dispatch=self._on_confirmed_dispatch,
                on_result=self.tracker.handle_result,
                on_progress=self.tracker.handle_progress,
            )
        self.lifecycle.bind(
            mqtt=self._mqtt,
            tracker=self.tracker,
            broker=self.transport,
            leadership_term_provider=self._current_term,
        )
        self.heartbeat = BotHeartbeatService(
            node_id=settings.node_id,
            application_version=settings.application_version,
            heartbeat_interval_sec=settings.heartbeat_interval_sec,
            status_push_interval_sec=settings.status_push_interval_sec,
            mqtt=self._mqtt,
            status_service=status_service,
            health=self.health,
            gateway=_GatewayView(self),
        )
        self._wire_lifecycle_suggestion_hooks()
        from bot.modules.commands.battle.service.session import QuickBattleService

        self.quick_battle = QuickBattleService(application=self)
        self.tracker.on_phase_change = self.quick_battle.handle_task_progress

    def _wire_lifecycle_suggestion_hooks(self) -> None:
        original_activate = self.lifecycle.on_activate
        original_soft = self.lifecycle.on_soft_stop
        original_hard = self.lifecycle.on_hard_stop

        async def _activate() -> None:
            await original_activate()
            # Restart delivery after soft-stop when Gateway is already up.
            await self._start_suggestion_delivery()

        async def _soft_stop() -> None:
            self.accepting_suggestion_claims = False
            await self._stop_suggestion_delivery()
            await original_soft()

        async def _hard_stop() -> None:
            self.accepting_suggestion_claims = False
            await self._stop_suggestion_delivery()
            if self.quick_battle is not None:
                await self.quick_battle.hard_stop_all()
            await original_hard()

        self.lifecycle.on_activate = _activate  # type: ignore[method-assign]
        self.lifecycle.on_soft_stop = _soft_stop  # type: ignore[method-assign]
        self.lifecycle.on_hard_stop = _hard_stop  # type: ignore[method-assign]
        self.control.on_activate = _activate
        self.control.on_soft_stop = _soft_stop
        self.control.on_hard_stop = _hard_stop

    @property
    def started(self) -> bool:
        return self._started

    @property
    def client(self) -> CombatBot | None:
        return self._client

    @property
    def gateway_connected(self) -> bool:
        return self._client is not None and self._client.is_ready()

    @property
    def gateway_authorized(self) -> bool:
        return self._gateway_authorized

    def admit_dispatch(self) -> bool:
        return self.lifecycle.accepting_ai_work and self.control.admit_dispatch()

    async def start(self) -> None:
        """Validate and mark process started; optionally connect brokers."""
        self._started = True
        loop = asyncio.get_running_loop()
        if self.transport is not None and self._enable_transport:
            self.transport.start(loop)
        if self._mqtt is not None and self._enable_mqtt:
            await self._start_mqtt()
        self._tick_task = asyncio.create_task(self._control_tick_loop(), name="bot-control-tick")
        self.heartbeat.start()

    async def authorize_gateway(self) -> None:
        """Create a fresh discord.py client and connect (one activation)."""
        if self._client_task is not None:
            await self._start_suggestion_delivery()
            return
        self._gateway_authorized = True
        self._client = self._bot_factory()
        self._wire_guild_handlers(self._client)
        if self._guild_service is not None:
            self._guild_sync = GuildSyncService(
                guilds=self._guild_service,
                interval_sec=self.settings.guild_sync_interval_sec,
                runtime_mode=self.runtime_mode,
                development_guild_id=self.development_guild_id,
            )

        async def _run() -> None:
            assert self._client is not None
            await self._client.start(self._token)

        self._client_task = asyncio.create_task(_run(), name="bot-gateway")
        await asyncio.sleep(0)
        if self._guild_sync is not None and self._client is not None:
            self._guild_sync.start(self._client)
        await self._start_suggestion_delivery()

    async def revoke_gateway(self) -> None:
        """Close and discard the current client instance."""
        self._gateway_authorized = False
        self.accepting_suggestion_claims = False
        await self._stop_suggestion_delivery()
        if self._guild_sync is not None:
            await self._guild_sync.stop()
            self._guild_sync = None
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._client_task is not None:
            self._client_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._client_task
            self._client_task = None

    async def _start_suggestion_delivery(self) -> None:
        if not self.enable_suggestion_queue:
            return
        if self._suggestion_service is None or self.l10n is None:
            logger.warning("suggestion delivery not started: missing repository or l10n")
            return
        if self._suggestion_queue_client is None or not self._suggestion_queue_name:
            logger.warning("suggestion delivery not started: missing queue client")
            return
        if self.suggestion_delivery is not None:
            self.accepting_suggestion_claims = True
            if self.suggestion_queue_poller is not None:
                self.suggestion_queue_poller.start()
            if self.suggestion_sweep is not None:
                self.suggestion_sweep.start()
            return

        self.suggestion_delivery = NotificationDeliveryService(
            repository=self._suggestion_service,  # type: ignore[arg-type]
            l10n=self.l10n,
            node_id=self.settings.node_id,
            max_attempts=self.settings.suggestion_max_dm_attempts,
            client_provider=lambda: self._client,
            accepting_claims=lambda: self.accepting_suggestion_claims,
        )
        self.suggestion_queue_poller = SuggestionQueuePoller(
            queue_client=self._suggestion_queue_client,
            queue_name=self._suggestion_queue_name,
            delivery=self.suggestion_delivery,
            health=self.health,
            poll_interval_sec=self.settings.queue_poll_interval_sec,
            accepting=lambda: self.accepting_suggestion_claims,
        )
        self.suggestion_sweep = NotificationSweepService(
            repository=self._suggestion_service,  # type: ignore[arg-type]
            delivery=self.suggestion_delivery,
            sweep_interval_sec=self.settings.suggestion_sweep_interval_sec,
            min_age_sec=self.settings.suggestion_sweep_min_age_sec,
            claim_timeout_sec=self.settings.suggestion_claim_timeout_sec,
            accepting=lambda: self.accepting_suggestion_claims,
            utcnow=lambda: datetime.now(tz=UTC),
        )
        self.accepting_suggestion_claims = True
        self.suggestion_queue_poller.start()
        self.suggestion_sweep.start()
        logger.info("suggestion queue poller and sweep started")

    async def _stop_suggestion_delivery(self) -> None:
        self.accepting_suggestion_claims = False
        if self.suggestion_queue_poller is not None:
            await self.suggestion_queue_poller.stop()
        if self.suggestion_sweep is not None:
            await self.suggestion_sweep.stop()
        # Allow an already-started DM/claim transition a short bounded wait.
        if self.suggestion_delivery is not None and self.suggestion_delivery.in_flight:
            for _ in range(10):
                if self.suggestion_delivery.in_flight <= 0:
                    break
                await asyncio.sleep(0.05)

    async def dispatch_environment(
        self,
        envelope: EnvironmentAiTaskEnvelope,
        completion_callback: CompletionCallback,
    ) -> None:
        await self.dispatch(envelope, completion_callback, command="harness")

    async def dispatch(
        self,
        envelope: EnvironmentAiTaskEnvelope | BattleAiTaskEnvelope,
        completion_callback: CompletionCallback,
        *,
        command: str = "harness",
    ) -> None:
        if self.transport is None:
            raise RuntimeError("AI transport is not enabled")
        await self.transport.dispatch(envelope, completion_callback, command=command)

    async def acquire_ai_slot(self, holder: str, timeout_sec: float) -> bool:
        try:
            await asyncio.wait_for(self._ai_lock.acquire(), timeout=timeout_sec)
        except TimeoutError:
            return False
        self._ai_holder = holder
        return True

    def release_ai_slot(self, holder: str) -> None:
        if self._ai_holder != holder:
            return
        self._ai_holder = None
        if self._ai_lock.locked():
            self._ai_lock.release()

    async def close(self) -> None:
        await self.lifecycle.run_shutdown(
            close_mqtt=self._close_mqtt,
            close_transport=self._close_transport,
            close_gateway=self.revoke_gateway,
            hard_stop=True,
        )
        await self.heartbeat.stop()
        if self._tick_task is not None:
            self._tick_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._tick_task
            self._tick_task = None
        self._started = False

    def _current_term(self) -> str | None:
        grant = self.control.current_grant
        if grant is None:
            return None
        return grant.leadership_term

    async def _on_confirmed_dispatch(self, pending: PendingDispatch) -> None:
        await self.tracker.create_from_dispatch(
            envelope=pending.envelope,
            completion_callback=pending.completion_callback,
            command=pending.command,
        )

    async def _publish_control_ack(self, ack: ControlAck) -> None:
        if self._mqtt is None:
            return
        try:
            await self._mqtt.publish(
                CONTROL_ACK_TOPIC,
                ack.model_dump_json().encode("utf-8"),
                qos=CONTROL_ACK_POLICY.qos,
                retain=CONTROL_ACK_POLICY.retain,
            )
        except Exception:  # noqa: BLE001 — control publish loss cannot extend grant
            logger.warning("control ack publish failed")

    async def _start_mqtt(self) -> None:
        assert self._mqtt is not None
        self._mqtt.set_message_handler(self._on_mqtt_message)
        self._mqtt.set_disconnect_handler(self._on_mqtt_disconnect)
        self._mqtt.set_connect_handler(self._on_mqtt_connect)
        await self._mqtt.connect()
        await self.control.on_mqtt_connected()
        await self._mqtt.subscribe(DESIRED_STATE_TOPIC, qos=1)
        await self._mqtt.subscribe(GRANT_TOPIC, qos=1)
        await self._mqtt.subscribe(PROGRESS_SUBSCRIBE, qos=0)

    async def _on_mqtt_connect(self) -> None:
        await self.control.on_mqtt_connected()
        if self._mqtt is not None:
            await self._mqtt.subscribe(DESIRED_STATE_TOPIC, qos=1)
            await self._mqtt.subscribe(GRANT_TOPIC, qos=1)
            await self._mqtt.subscribe(PROGRESS_SUBSCRIBE, qos=0)

    async def _on_mqtt_disconnect(self, _reason: str) -> None:
        await self.control.on_mqtt_disconnected()

    async def _on_mqtt_message(self, topic: str, payload: bytes) -> None:
        if topic.startswith(PROGRESS_TOPIC_PREFIX):
            await self._handle_progress_message(topic, payload)
            return
        await self.control.handle_message(topic, payload)

    async def _handle_progress_message(self, topic: str, payload: bytes) -> None:
        suffix = topic[len(PROGRESS_TOPIC_PREFIX) :]
        try:
            progress = parse_task_progress_message(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, UnknownSchemaVersionError):
            logger.warning(
                "ignoring malformed task progress",
                extra={"topic": topic},
                exc_info=True,
            )
            return
        if suffix and suffix != str(progress.task_id):
            logger.warning("progress topic/task_id mismatch")
            return
        extra: dict[str, Any] = {
            "task_id": str(progress.task_id),
            "graph": progress.graph,
            "phase": progress.phase.value,
        }
        record = self.tracker.records.get(str(progress.task_id))
        if record is not None:
            extra["command"] = record.command
        logger.info("ai_task progress received phase=%s", progress.phase.value, extra=extra)
        if self.transport is not None:
            await self.transport.handle_progress(progress)
        else:
            await self.tracker.handle_progress(progress)

    async def _control_tick_loop(self) -> None:
        while True:
            await self.control.tick()
            if self.quick_battle is not None:
                await self.quick_battle.process_timeouts()
            if self.transport is not None:
                self.health.rabbitmq_connected = self.transport.rabbitmq_connected
            await asyncio.sleep(1.0)

    async def _close_mqtt(self) -> None:
        if self._mqtt is not None:
            await self._mqtt.close()

    async def _close_transport(self) -> None:
        if self.transport is not None:
            await self.transport.close()

    def _wire_guild_handlers(self, client: CombatBot) -> None:
        if self._guild_service is None:
            return
        handler = GuildEventHandler(
            guilds=self._guild_service,
            health=self.health,
            runtime_mode=self.runtime_mode,
            development_guild_id=self.development_guild_id,
        )
        self._guild_handler = handler

        @client.event
        async def on_guild_join(guild: Any) -> None:
            await handler.on_guild_join(guild)

        @client.event
        async def on_guild_update(before: Any, after: Any) -> None:
            await handler.on_guild_update(before, after)

        @client.event
        async def on_guild_remove(guild: Any) -> None:
            await handler.on_guild_remove(guild)


__all__ = ["BotApplication", "DependencyHealth"]
