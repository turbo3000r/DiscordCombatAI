"""dev-support settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DevSupportSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    node_id: str = Field(alias="NODE_ID", default="node-local")
    host: str = Field(default="0.0.0.0", alias="DEV_SUPPORT_HOST")
    port: int = Field(default=8080, alias="DEV_SUPPORT_PORT")
    db_path: str = Field(default="/data/dev-support.db", alias="DEV_SUPPORT_DB_PATH")
    mosquitto_host: str = Field(default="mosquitto", alias="DEV_SUPPORT_MOSQUITTO_HOST")
    mosquitto_port: int = Field(default=1883, alias="DEV_SUPPORT_MOSQUITTO_PORT")
    grant_ttl_sec: int = Field(default=45, alias="DEV_SUPPORT_GRANT_TTL_SEC")
    grant_renew_sec: int = Field(default=15, alias="DEV_SUPPORT_GRANT_RENEW_SEC")


__all__ = ["DevSupportSettings"]
