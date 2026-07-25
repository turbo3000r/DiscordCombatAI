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


class AiWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_WORKER_",
        extra="ignore",
        populate_by_name=True,
    )

    node_id: str = Field(min_length=1, max_length=128, pattern=NODE_ID_PATTERN)
    application_version: str = Field(
        validation_alias="APPLICATION_VERSION",
        pattern=RELEASE_TAG_PATTERN,
        max_length=128,
    )

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
    mosquitto_host: str = "mosquitto"
    mosquitto_port: int = Field(default=1883, ge=1, le=65535)

    celery_concurrency: int = Field(default=1, ge=1, le=1)
    progress_heartbeat_sec: float = Field(default=30, gt=0)
    heartbeat_interval_sec: float = Field(default=30, gt=0)
    transport_shell: bool = False
    llm_max_retries: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def _validate_node_identity(self) -> AiWorkerSettings:
        host_node_id = os.environ.get("NODE_ID")
        if host_node_id and host_node_id != self.node_id:
            raise ValueError(
                "AI_WORKER_NODE_ID "
                f"{self.node_id!r} must equal host NODE_ID {host_node_id!r}"
            )
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
            f"AiWorkerSettings(node_id={self.node_id!r}, "
            f"application_version={self.application_version!r}, "
            f"transport_shell={self.transport_shell!r})"
        )

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(*args, **kwargs)
        data["rabbitmq_pass"] = "***"
        return data


__all__ = ["AiWorkerSettings", "NODE_ID_PATTERN", "RELEASE_TAG_PATTERN"]
