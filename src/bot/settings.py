from __future__ import annotations

import os
from typing import Any

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.messaging import build_rabbitmq_broker_url

RELEASE_TAG_PATTERN = (
    r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)
NODE_ID_PATTERN = r"^[A-Za-z0-9._-]+$"


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOT_", extra="ignore", populate_by_name=True)

    node_id: str = Field(min_length=1, max_length=128, pattern=NODE_ID_PATTERN)
    application_version: str = Field(
        validation_alias="APPLICATION_VERSION",
        pattern=RELEASE_TAG_PATTERN,
        max_length=128,
    )
    discord_bot_token: SecretStr = Field(
        validation_alias="DISCORD_BOT_TOKEN",
        min_length=1,
    )

    mosquitto_host: str = "mosquitto"
    mosquitto_port: int = Field(default=1883, ge=1, le=65535)
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = Field(default=5672, ge=1, le=65535)
    rabbitmq_user: str = Field(min_length=1, max_length=128)
    rabbitmq_pass: SecretStr = Field(min_length=1, max_length=256)
    rabbitmq_vhost: str = Field(
        default="/discordcombatai",
        validation_alias="RABBITMQ_DEFAULT_VHOST",
        min_length=1,
        max_length=128,
    )

    drain_progress_interval_sec: float = Field(default=5, gt=0)
    ai_task_stall_timeout_sec: float = Field(default=120, gt=0)
    ai_task_timeout_sec: float = Field(default=900, gt=0)
    guild_sync_interval_sec: float = Field(default=3600, gt=0)
    heartbeat_interval_sec: float = Field(default=30, gt=0)
    status_push_interval_sec: float = Field(default=60, gt=0)
    shutdown_grace_sec: float = Field(default=30, gt=0)
    control_drain_timeout_sec: float = Field(default=45, gt=0)
    activation_grant_max_ttl_sec: int = Field(default=60, gt=0)
    queue_poll_interval_sec: float = Field(default=300, gt=0)
    suggestion_sweep_interval_sec: float = Field(default=900, gt=0)
    suggestion_sweep_min_age_sec: float = Field(default=600, gt=0)
    suggestion_max_dm_attempts: int = Field(default=5, gt=0)
    suggestion_claim_timeout_sec: float = Field(default=120, gt=0)

    @model_validator(mode="after")
    def _validate_node_identity_and_timers(self) -> BotSettings:
        host_node_id = os.environ.get("NODE_ID")
        if host_node_id and host_node_id != self.node_id:
            raise ValueError(
                f"BOT_NODE_ID {self.node_id!r} must equal host NODE_ID {host_node_id!r}"
            )
        if self.ai_task_stall_timeout_sec >= self.ai_task_timeout_sec:
            raise ValueError("stall timeout must be below overall AI task timeout")
        if self.suggestion_claim_timeout_sec <= 60:
            raise ValueError("suggestion claim timeout must exceed queue visibility (60s)")
        return self

    def broker_url(self) -> str:
        return build_rabbitmq_broker_url(
            user=self.rabbitmq_user,
            password=self.rabbitmq_pass.get_secret_value(),
            host=self.rabbitmq_host,
            port=self.rabbitmq_port,
            vhost=self.rabbitmq_vhost,
        )

    def __repr__(self) -> str:
        return (
            f"BotSettings(node_id={self.node_id!r}, "
            f"application_version={self.application_version!r})"
        )

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(*args, **kwargs)
        data["discord_bot_token"] = "***"
        data["rabbitmq_pass"] = "***"
        return data


__all__ = ["BotSettings", "NODE_ID_PATTERN", "RELEASE_TAG_PATTERN"]
