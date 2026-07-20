from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RELEASE_TAG_PATTERN = (
    r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)


class HeadSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HEAD_", extra="ignore", populate_by_name=True)

    node_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    application_version: str = Field(
        validation_alias="APPLICATION_VERSION",
        pattern=RELEASE_TAG_PATTERN,
        max_length=128,
    )
    github_repo: str = Field(
        max_length=201,
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
    )
    release_include_prerelease: bool = False

    pubsub_cluster_group: str = "cluster"
    pubsub_dashboard_group: str = "dashboard-live"
    election_heartbeat_sec: float = Field(default=30, gt=0)
    election_heartbeat_timeout_sec: float = Field(default=90, gt=0)
    lease_blob_container: str = "coordination"
    lease_blob_name: str = "leader.lock"
    lease_duration_sec: int = Field(default=60, ge=15, le=60)
    loss_of_internet_grace_sec: float = Field(default=0, ge=0)
    reconnect_backoff_max_sec: float = Field(default=1800, gt=0)

    release_poll_interval_sec: float = Field(default=300, gt=0)
    drain_timeout_sec: float = Field(default=120, gt=0)
    bot_stop_ack_timeout_sec: float = Field(default=15, gt=0)
    bot_grant_ttl_sec: int = Field(default=45, gt=0)
    bot_grant_renew_sec: float = Field(default=15, gt=0)

    mosquitto_host: str = "mosquitto"
    mosquitto_port: int = Field(default=1883, ge=1, le=65535)
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = Field(default=5672, ge=1, le=65535)
    rabbitmq_user: str = Field(min_length=1, max_length=128)
    rabbitmq_pass: str = Field(min_length=1, max_length=256)
    rabbitmq_vhost: str = Field(
        default="/discordcombatai",
        validation_alias="RABBITMQ_DEFAULT_VHOST",
        min_length=1,
        max_length=128,
    )
    metrics_interval_sec: float = Field(default=2, gt=0)
    telemetry_batch_interval_sec: float = Field(default=60, gt=0)
    telemetry_live_interval_sec: float = Field(default=10, gt=0)
    metrics_buffer_max_batches: int = Field(default=60, gt=0)
    telemetry_live_max_logs: int = Field(default=50, ge=0)
    telemetry_live_max_bytes: int = Field(default=65_536, ge=1024)
    service_heartbeat_stale_sec: float = Field(default=90, gt=0)
    bot_heartbeat_interval_sec: float = Field(
        default=30,
        validation_alias="BOT_HEARTBEAT_INTERVAL_SEC",
        gt=0,
    )
    ai_worker_heartbeat_interval_sec: float = Field(
        default=30,
        validation_alias="AI_WORKER_HEARTBEAT_INTERVAL_SEC",
        gt=0,
    )

    launcher_ipc_url: str = "http://host.docker.internal:9700"
    launcher_ipc_secret_file: str = "/run/secrets/launcher_ipc_secret"
    ipc_bind: str = "0.0.0.0"
    ipc_port: int = Field(default=9800, ge=1, le=65535)

    @model_validator(mode="after")
    def _validate_cadences(self) -> HeadSettings:
        if self.election_heartbeat_timeout_sec <= self.election_heartbeat_sec:
            raise ValueError("heartbeat timeout must exceed heartbeat cadence")
        if self.lease_duration_sec <= self.election_heartbeat_sec:
            raise ValueError("lease duration must exceed heartbeat cadence")
        if self.bot_grant_renew_sec >= self.bot_grant_ttl_sec:
            raise ValueError("grant renewal cadence must be below grant TTL")
        if self.pubsub_cluster_group == self.pubsub_dashboard_group:
            raise ValueError("cluster and dashboard PubSub groups must differ")
        largest_service_heartbeat = max(
            self.bot_heartbeat_interval_sec,
            self.ai_worker_heartbeat_interval_sec,
        )
        if self.service_heartbeat_stale_sec < largest_service_heartbeat * 2:
            raise ValueError("service heartbeat staleness must be at least two heartbeat intervals")
        return self


__all__ = ["HeadSettings", "RELEASE_TAG_PATTERN"]
