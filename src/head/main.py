from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from shared.azure.clients.blob import BlobClient
from shared.azure.clients.pubsub import PubSubClient
from shared.azure.clients.table import TableClient
from shared.azure.configs.settings import load_azure_settings
from shared.azure.services.logging import LogArchiveService
from shared.azure.services.metrics import MetricsService
from shared.models import AiWorkerHeartbeat, BotHeartbeat, LeaderHeartbeat, UpdateAvailableMessage

from .app import HeadApplication
from .clock import SystemClock
from .election import ElectionMachine
from .http import HeadHttpApi, HeadStatus, RequestAuthenticator
from .launcher import LauncherClient
from .lease import LeaseCoordinator
from .logs import LogAggregator
from .mqtt import HeadMqttEvent, MqttManager
from .pubsub import ClusterPubSub, ServiceSdkTelemetrySender
from .rabbitmq_bridge import RabbitMqEventBridge
from .release import ReleasePoller
from .settings import HeadSettings
from .telemetry import MetricsServiceWriter, TelemetryPipeline
from .transports import (
    AioHttpClientTransport,
    AioHttpHeadServer,
    AioPikaEventExchangeTransport,
    AzureClusterPubSubTransport,
    GitHubApiReleaseSource,
    PahoMqttTransport,
    PsutilProcessSampler,
)
from .update import MqttDrainObserver, UpdateOrchestrator


def _read_secret(path: str) -> bytes:
    secret = Path(path).read_bytes()
    if len(secret) < 32:
        raise ValueError("Launcher IPC secret must contain at least 32 bytes")
    return secret


async def build_application(settings: HeadSettings | None = None) -> HeadApplication:
    config = settings or HeadSettings()  # type: ignore[call-arg]
    clock = SystemClock()
    secret = _read_secret(config.launcher_ipc_secret_file)
    azure = load_azure_settings("head")

    blob_client = BlobClient(service="head", settings=azure)
    table_client = TableClient(service="head", settings=azure)
    pubsub_service_client = PubSubClient(service="head", settings=azure)
    archive_service = LogArchiveService(
        blob_client=blob_client,
        container_name=azure.azure_log_archive_container,
    )
    log_aggregator = LogAggregator(archive_service)

    async def handle_unmatched_mqtt(topic: str, payload: bytes) -> bool:
        if not topic.startswith("logs/"):
            return False
        try:
            log_aggregator.ingest(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False
        return True

    mqtt_transport = PahoMqttTransport(
        host=config.mosquitto_host,
        port=config.mosquitto_port,
        client_id=f"head-{config.node_id}-{uuid4()}",
    )
    mqtt = MqttManager(mqtt_transport, unhandled_handler=handle_unmatched_mqtt)

    cluster_transport = AzureClusterPubSubTransport(
        token_client=pubsub_service_client,
        group=config.pubsub_cluster_group,
        user_id=f"head:{config.node_id}:{uuid4()}",
    )
    cluster = ClusterPubSub(cluster_transport, group=config.pubsub_cluster_group)
    lease = LeaseCoordinator(
        blob_client,
        container=config.lease_blob_container,
        blob_name=config.lease_blob_name,
        duration_sec=config.lease_duration_sec,
    )
    election = ElectionMachine(
        settings=config,
        clock=clock,
        mqtt=mqtt,
        lease=lease,
        pubsub=cluster,
    )

    launcher_transport = AioHttpClientTransport()
    launcher = LauncherClient(
        base_url=config.launcher_ipc_url,
        secret=secret,
        source_node_id=config.node_id,
        transport=launcher_transport,
        clock=clock,
    )
    observer = MqttDrainObserver(mqtt, clock)
    updater = UpdateOrchestrator(
        election=election,
        cluster=cluster,
        launcher=launcher,
        observer=observer,
    )

    telemetry = TelemetryPipeline(
        node_id=config.node_id,
        clock=clock,
        sampler=PsutilProcessSampler(),
        logs=log_aggregator,
        metrics_writer=MetricsServiceWriter(
            MetricsService(
                table_client=table_client,
                table_name=azure.azure_metrics_table,
            )
        ),
        live_sender=ServiceSdkTelemetrySender(pubsub_service_client),
        dashboard_group=config.pubsub_dashboard_group,
        batch_interval_sec=int(config.telemetry_batch_interval_sec),
        heartbeat_stale_sec=config.service_heartbeat_stale_sec,
        metrics_buffer_max_batches=config.metrics_buffer_max_batches,
        live_max_logs=config.telemetry_live_max_logs,
        live_max_bytes=config.telemetry_live_max_bytes,
    )

    event_bridge = RabbitMqEventBridge(
        transport=AioPikaEventExchangeTransport(
            host=config.rabbitmq_host,
            port=config.rabbitmq_port,
            username=config.rabbitmq_user,
            password=config.rabbitmq_pass,
            vhost=config.rabbitmq_vhost,
        ),
        publisher=mqtt,
        clock=clock,
    )

    async def on_mqtt_event(event: HeadMqttEvent) -> None:
        if isinstance(event, BotHeartbeat):
            telemetry.update_bot_heartbeat(event)
        elif isinstance(event, AiWorkerHeartbeat):
            telemetry.update_worker_heartbeat(event)

    mqtt.add_listener(on_mqtt_event)

    api = HeadHttpApi(
        authenticator=RequestAuthenticator(secret, clock),
        clock=clock,
        version=config.application_version,
        instance_id=str(election.instance_id),
        started_at=clock.utcnow(),
        status_provider=lambda: HeadStatus(
            election_state=election.state,
            is_leader=election.is_leader,
            lease_held=lease.held,
            mosquitto_connected=mqtt.connected,
            pubsub_connected=cluster.connected,
        ),
    )
    server = AioHttpHeadServer(api, bind=config.ipc_bind, port=config.ipc_port)

    async def election_loop() -> None:
        while True:
            await election.tick()
            if election.can_restore_same_term:
                await election.restore_same_term()
            await clock.sleep(1)

    async def cluster_receive_loop() -> None:
        while True:
            try:
                accepted = await cluster.receive_once()
                if not accepted:
                    continue
                event = await cluster.events.get()
                if isinstance(event, LeaderHeartbeat):
                    election.observe_heartbeat(event)
                elif isinstance(event, UpdateAvailableMessage):
                    await updater.handle(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                await election.handle_pubsub_loss()
                await clock.sleep(1)

    async def sample_loop() -> None:
        while True:
            telemetry.sample_local()
            await clock.sleep(config.metrics_interval_sec)

    async def persistent_telemetry_loop() -> None:
        while True:
            await clock.sleep(config.telemetry_batch_interval_sec)
            try:
                await telemetry.flush_persistent(
                    is_leader=election.is_leader,
                    leadership_term=(
                        str(election.leadership_term)
                        if election.leadership_term is not None
                        else None
                    ),
                )
                await log_aggregator.flush_if_leader(
                    is_leader=election.is_leader,
                    node_id=config.node_id,
                    timestamp=clock.utcnow(),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    async def live_telemetry_loop() -> None:
        while True:
            await clock.sleep(config.telemetry_live_interval_sec)
            try:
                await telemetry.stream_live(
                    is_leader=election.is_leader,
                    leadership_term=(
                        str(election.leadership_term)
                        if election.leadership_term is not None
                        else None
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    release_source = GitHubApiReleaseSource()
    release_poller = ReleasePoller(
        source=release_source,
        repository=config.github_repo,
        current_version=config.application_version,
        is_leader=lambda: election.is_leader,
        on_release=lambda release: updater.announce(release.tag_name),
        clock=clock,
        interval_sec=config.release_poll_interval_sec,
        include_prerelease=config.release_include_prerelease,
    )

    return HeadApplication(
        components=(server, election, event_bridge),
        resources=(
            release_poller,
            launcher,
            table_client,
            blob_client,
            pubsub_service_client,
        ),
        loops=(
            ("head-election", election_loop),
            ("head-cluster-receive", cluster_receive_loop),
            ("head-metrics-sample", sample_loop),
            ("head-telemetry-persist", persistent_telemetry_loop),
            ("head-telemetry-live", live_telemetry_loop),
            ("head-release-poll", release_poller.run),
        ),
        on_initialized=lambda initialized: setattr(api, "initialized", initialized),
    )


async def async_main() -> None:
    application = await build_application()
    try:
        await application.run()
    finally:
        await application.close()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(async_main())


if __name__ == "__main__":
    main()
